# pylint: disable=[missing-module-docstring]  # see class docstrings
from random import choices
from typing import cast

import numpy as np
import serial
from serial.serialutil import SerialException

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, getTestMode, parameterDict
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [MIPS]


class MIPS(Device):
    """Contains a list of voltages channels from one or multiple MIPS power supplies with 8 channels each.

    The voltages are monitored and a warning is given if the set potentials are not reached.
    """

    name = 'MIPS'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = 'V'
    useMonitors = True
    iconFile = 'mips.png'
    useOnOffLogic = True
    channels: 'list[VoltageChannel]'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = VoltageChannel

    def initGUI(self) -> None:
        super().initGUI()
        self.controller = VoltageController(controllerParent=self)  # after all channels loaded

    def getChannels(self) -> 'list[VoltageChannel]':
        return cast('list[VoltageChannel]', super().getChannels())

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings[f'{self.name}/Interval'][Parameter.VALUE] = 1000  # overwrite default value
        defaultSettings[f'{self.name}/{self.MAXDATAPOINTS}'][Parameter.VALUE] = 1E5  # overwrite default value
        return defaultSettings

    def getCOMs(self) -> list[str]:  # get list of unique used COMs
        """List of COM ports."""
        return list({channel.com for channel in self.channels if channel.real})


class VoltageChannel(Channel):

    COM = 'COM'
    ID = 'ID'
    channelParent: MIPS

    def getDefaultChannel(self) -> dict[str, dict]:

        # definitions for type hinting
        self.com: str
        self.id: int

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'Voltage (V)'  # overwrite to change header
        channel[self.VALUE][Parameter.UNIT] = 'V'
        channel[self.MONITOR][Parameter.HEADER] = 'Monitor (V)'
        channel[self.MONITOR][Parameter.UNIT] = 'V'
        channel[self.COM] = parameterDict(value='COM1', toolTip='COM port of MIPS.', items=','.join([f'COM{x}' for x in range(1, 25)]),
                                          parameterType=PARAMETERTYPE.COMBO, advanced=True, attr='com')
        channel[self.ID] = parameterDict(value=0, parameterType=PARAMETERTYPE.INT, advanced=True,
                                    header='ID', minimum=1, maximum=8, attr='id')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.displayedParameters.append(self.COM)
        self.displayedParameters.append(self.ID)

    def monitorChanged(self) -> None:
        # overwriting super().monitorChanged() to set 0 as expected value when device is off
        self.updateWarningState(self.enabled and self.channelParent.controller.acquiring
                                and ((self.channelParent.isOn() and abs(self.monitor - self.value) > 1)
                                or (not self.channelParent.isOn() and abs(self.monitor - 0) > 1)))

    def realChanged(self) -> None:
        self.getParameterByName(self.COM).setVisible(self.real)
        self.getParameterByName(self.ID).setVisible(self.real)
        super().realChanged()


class VoltageController(DeviceController):

    controllerParent: MIPS

    def __init__(self, controllerParent) -> None:
        super().__init__(controllerParent)
        self.maxID = None
        self.initCOMs()

    def initCOMs(self) -> None:
        """Initialize COMs and ports."""
        self.COMs = self.controllerParent.getCOMs() or ['COM1']
        self.ports = [None] * len(self.COMs)  # type: ignore  # noqa: PGH003

    def runInitialization(self) -> None:
        self.initCOMs()
        if self.COMs is None:
            return
        try:
            self.ports = [serial.Serial(baudrate=9600, port=COM, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                                        bytesize=serial.EIGHTBITS, timeout=2) for COM in self.COMs]
            result = self.MIPSWriteRead(self.COMs[0], 'GDCBV,1\r\n')
        except (ValueError, SerialException) as e:  # pylint: disable=[broad-except]  # socket does not throw more specific exception
            self.print(f'Could not establish Serial connection to a MIPS at {self.COMs}. Exception: {e}', flag=PRINT.WARNING)
        else:
            if result:
                self.signalComm.initCompleteSignal.emit()
            else:
                msg = 'Could not read values. Make sure MIPS is turned on.'
                raise ValueError(msg)
        finally:
            self.initializing = False

    def initializeValues(self, reset: bool = False) -> None:  # noqa: ARG002
        self.maxID = max(channel.id if channel.real else 0 for channel in self.controllerParent.getChannels())  # used to query correct amount of monitors
        if self.COMs is not None and self.maxID is not None:
            self.values = np.full([len(self.COMs), self.maxID + 1], fill_value=np.nan, dtype=np.float32)

    def readNumbers(self) -> None:
        for i in range(len(self.COMs)):
            for ID in range(8):
                try:
                    self.values[i][ID] = float(self.MIPSWriteRead(self.COMs[i], f'GDCBV,{ID + 1}\r\n', already_acquired=True))
                except ValueError as e:
                    self.print(f'Error while reading voltage {e}')
                    self.errorCount += 1
                    self.values[i][ID] = np.nan

    def fakeNumbers(self) -> None:
        if self.COMs is not None:
            for channel in self.controllerParent.getChannels():
                if channel.enabled and channel.real:
                    if self.controllerParent.isOn() and channel.enabled:
                        # fake values with noise and 10% channels with offset to simulate defect channel or short
                        self.values[self.COMs.index(channel.com)][channel.id - 1] = channel.value + 5 * choices([0, 1], [.98, .02])[0] + self.rng.random() - .5
                    else:
                        self.values[self.COMs.index(channel.com)][channel.id - 1] = 0 + 5 * choices([0, 1], [.9, .1])[0] + self.rng.random() - .5

    def applyValue(self, channel: VoltageChannel) -> None:
        self.MIPSWriteRead(channel.com, message=f'SDCB,{channel.id},{channel.value if (channel.enabled and self.controllerParent.isOn()) else 0}\r\n')

    def updateValues(self) -> None:
        # Overwriting to use values for multiple COM ports
        if self.values is not None and self.COMs is not None:
            for channel in self.controllerParent.getChannels():
                if channel.enabled and channel.real:
                    channel.monitor = np.nan if channel.waitToStabilize else self.values[self.COMs.index(channel.com)][channel.id - 1]

    def closeCommunication(self) -> None:
        super().closeCommunication()
        if self.ports is not None:
            for i, port in enumerate(self.ports):
                if port:
                    with self.lock.acquire_timeout(1, timeoutMessage=f'Could not acquire lock before closing {port.port}.'):
                        port.close()
                        self.ports[i] = None  # type: ignore  # noqa: PGH003
        self.initialized = False
        self.closing = False

    def MIPSWrite(self, COM, message) -> None:
        """MIPS specific serial write.

        :param COM: The COM port be used.
        :type COM: str
        :param message: The serial message to be send.
        :type message: str
        """
        if self.ports is not None and self.COMs is not None:
            port = self.ports[self.COMs.index(COM)]
            if port is not None:
                self.serialWrite(port, message)

    def MIPSRead(self, COM) -> str:
        """MIPS specific serial read.

        :param COM: The COM port be used.
        :type COM: str
        """
        # only call from thread!  # make sure lock is acquired before and released after
        if ((not getTestMode() and self.initialized) or self.initializing) and self.ports is not None and self.COMs is not None:
            port = self.ports[self.COMs.index(COM)]
            if port is not None:
                return self.serialRead(port, EOL='\r', strip='b\x06')
        return ''

    def MIPSWriteRead(self, COM, message, already_acquired=False) -> str:
        """MIPS specific serial write and read.

        :param COM: The COM port be used.
        :type COM: str
        :param message: The serial message to be send.
        :type message: str
        :param already_acquired: Indicates if the lock has already been acquired, defaults to False
        :type already_acquired: bool, optional
        :return: The serial response received.
        :rtype: str
        """
        response = ''
        if not getTestMode():
            with self.lock.acquire_timeout(1, timeoutMessage=f'Cannot acquire lock for message: {message}.', already_acquired=already_acquired) as lock_acquired:
                if lock_acquired:
                    self.MIPSWrite(COM, message)  # get channel name
                    response = self.MIPSRead(COM)
        return response
