# pylint: disable=[missing-module-docstring]  # see class docstrings
import nidaqmx

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, parameterDict
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [NI9263]


class NI9263(Device):
    """Contains a list of voltages channels from one or multiple NI9263 power supplies with 4 analog outputs each."""

    name = 'NI9263'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = 'V'
    iconFile = 'NI9263.png'
    useOnOffLogic = True
    channels: 'list[VoltageChannel]'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = VoltageChannel
        self.controller = VoltageController(controllerParent=self)


class VoltageChannel(Channel):

    ADDRESS = 'Address'
    channelParent: NI9263

    def getDefaultChannel(self) -> dict[str, dict]:

        # definitions for type hinting
        self.address: str

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'Voltage (V)'  # overwrite to change header
        channel[self.VALUE][Parameter.UNIT] = 'V'
        channel[self.MIN][Parameter.VALUE] = 0
        channel[self.MAX][Parameter.VALUE] = 1  # start with safe limits
        channel[self.ADDRESS] = parameterDict(value='cDAQ1Mod1/ao0', toolTip='Address of analog output',
                                          parameterType=PARAMETERTYPE.TEXT, advanced=True, attr='address')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.displayedParameters.append(self.ADDRESS)

    def realChanged(self) -> None:
        self.getParameterByName(self.ADDRESS).setVisible(self.real)
        super().realChanged()


class VoltageController(DeviceController):

    controllerParent: NI9263

    def runInitialization(self) -> None:
        try:
            with nidaqmx.Task() as task:
                task.ao_channels  # will raise exception if connection failed  # noqa: B018
            self.signalComm.initCompleteSignal.emit()
        except Exception as e:  # pylint: disable=[broad-except]  # socket does not throw more specific exception  # noqa: BLE001
            self.closeCommunication()
            self.print(f'Could not establish connection at {self.controllerParent.channels[0].address}. Exception: {e}', flag=PRINT.WARNING)
        finally:
            self.initializing = False

    def applyValue(self, channel: VoltageChannel) -> None:
        with self.lock.acquire_timeout(1, timeoutMessage=f'Cannot acquire lock to set voltage of {channel.name}.') as lock_acquired:
            if lock_acquired:
                with nidaqmx.Task() as task:
                    task.ao_channels.add_ao_voltage_chan(channel.address)
                    value = channel.value if (channel.enabled and self.controllerParent.isOn()) else 0
                    task.write(value)
                    self.print(f'Setting {channel.name} at {channel.address} to {value} V', flag=PRINT.TRACE)

    def runAcquisition(self) -> None:
        pass  # nothing to acquire, no readbacks

    def updateValues(self) -> None:
        pass  # nothing to update, no read values and no monitors.

    def toggleOn(self) -> None:
        super().toggleOn()
        for channel in self.controllerParent.getChannels():
            if channel.real:
                self.applyValueFromThread(channel)

    def closeCommunication(self) -> None:
        super().closeCommunication()
        # nothing to close
        self.initialized = False
        self.closing = False
