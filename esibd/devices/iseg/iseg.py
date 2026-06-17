# pylint: disable=[missing-module-docstring]  # see class docstrings
import socket
from typing import cast

import numpy as np

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, getTestMode, parameterDict
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [ISEG]


class ISEG(Device):
    """Contains a list of voltages channels from an ISEG ECH244 power supply.

    The voltages are monitored and a warning is given if the set potentials are not reached.
    In case of any issues, first make sure ISEG ECH244 and all modules are turned on, and communicating.
    Use SNMP Control to quickly test this independent of this plugin.
    """

    name = 'ISEG'
    version = '1.1'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = 'V'
    useMonitors = True
    iconFile = 'ISEG.png'
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

    ip: str
    port: int

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings[f'{self.name}/IP'] = parameterDict(value='169.254.163.182', toolTip='IP address of ECH244',
                                                                parameterType=PARAMETERTYPE.TEXT, attr='ip')
        defaultSettings[f'{self.name}/Port'] = parameterDict(value=10001, toolTip='SCPI port of ECH244',
                                                                parameterType=PARAMETERTYPE.INT, attr='port')
        defaultSettings[f'{self.name}/Interval'][Parameter.VALUE] = 1000  # overwrite default value
        defaultSettings[f'{self.name}/{self.MAXDATAPOINTS}'][Parameter.VALUE] = 1E5  # overwrite default value
        return defaultSettings

    def getModules(self) -> list[int]:
        """Get list of used modules."""
        return list({channel.module for channel in self.channels if channel.real})

    def closeCommunication(self) -> None:
        self.setOn(False)
        self.controller.toggleOnFromThread(parallel=False)
        super().closeCommunication()


class VoltageChannel(Channel):

    MODULE = 'Module'
    ID = 'ID'
    channelParent: ISEG

    def getDefaultChannel(self) -> dict[str, dict]:

        # definitions for type hinting
        self.module: int
        self.id: int

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'Voltage (V)'  # overwrite to change header
        channel[self.VALUE][Parameter.UNIT] = 'V'  # overwrite to change header
        channel[self.MONITOR][Parameter.HEADER] = 'Monitor (V)'
        channel[self.MONITOR][Parameter.UNIT] = 'V'
        channel[self.MODULE] = parameterDict(value=0, parameterType=PARAMETERTYPE.INT, advanced=True,
                                    header='Mod', minimum=0, maximum=99, attr='module')
        channel[self.ID] = parameterDict(value=0, parameterType=PARAMETERTYPE.INT, advanced=True,
                                    header='ID', minimum=0, maximum=99, attr='id')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.displayedParameters.append(self.MODULE)
        self.displayedParameters.append(self.ID)

    def monitorChanged(self) -> None:
        # overwriting super().monitorChanged() to set 0 as expected value when device is off
        self.updateWarningState(self.enabled and self.channelParent.controller.acquiring
                                and ((self.channelParent.isOn() and abs(self.monitor - self.value) > 1)
                                or (not self.channelParent.isOn() and abs(self.monitor - 0) > 1)))

    def realChanged(self) -> None:
        self.getParameterByName(self.MODULE).setVisible(self.real)
        self.getParameterByName(self.ID).setVisible(self.real)
        super().realChanged()


class VoltageController(DeviceController):

    controllerParent: ISEG

    def __init__(self, controllerParent) -> None:
        super().__init__(controllerParent=controllerParent)
        self.socket = None
        self.modules = None
        self.maxID = None

    def initializeValues(self, reset: bool = False) -> None:  # noqa: ARG002
        self.modules = self.controllerParent.getModules() or [0]
        self.maxID = max(channel.id if channel.real else 0 for channel in self.controllerParent.getChannels())  # used to query correct amount of monitors
        if self.modules is not None and self.maxID is not None:
            self.values = np.full([len(self.modules), self.maxID + 1], fill_value=np.nan, dtype=np.float32)

    def runInitialization(self) -> None:
        try:
            self.socket = socket.create_connection(address=(self.controllerParent.ip, int(self.controllerParent.port)), timeout=3)
            self.print(self.ISEGWriteRead(message='*IDN?\r\n'))
            self.signalComm.initCompleteSignal.emit()
        except Exception as e:  # pylint: disable=[broad-except]  # socket does not throw more specific exception  # noqa: BLE001
            self.print(f'Could not establish SCPI connection to {self.controllerParent.ip} on port {int(self.controllerParent.port)}. Exception: {e}', flag=PRINT.WARNING)
        finally:
            self.initializing = False

    def readNumbers(self) -> None:
        if self.modules and self.maxID:
            for module in self.modules:
                res = self.ISEGWriteRead(message=f':MEAS:VOLT? (#{module}@0-{self.maxID + 1})\r\n', already_acquired=True)
                if res:
                    try:
                        monitors = [float(x[:-1]) for x in res[:-4].split(',')]  # res[:-4] to remove trailing '\r\n'
                        # fill up to self.maxID to handle all modules the same independent of the number of channels.
                        self.values[module] = np.hstack([monitors, np.zeros(self.maxID + 1 - len(monitors))])
                    except (ValueError, TypeError) as e:
                        self.print(f'Parsing error: {e} for {res}.')
                        self.errorCount += 1

    def fakeNumbers(self) -> None:
        for channel in self.controllerParent.getChannels():
            if channel.enabled and channel.real:
                # fake values with noise and 10% channels with offset to simulate defect channel or short
                self.values[channel.module][channel.id] = ((channel.value if self.controllerParent.isOn() and channel.enabled else 0)
                                   + 5 * (self.rng.choice([0, 1], p=[0.98, 0.02])) + self.rng.random() - 0.5)

    def applyValue(self, channel: VoltageChannel) -> None:
        self.ISEGWriteRead(message=f':VOLT {channel.value if channel.enabled else 0},(#{channel.module}@{channel.id})\r\n')

    def updateValues(self) -> None:
        # Overwriting to use values for multiple modules
        if self.values is None:
            return
        for channel in self.controllerParent.getChannels():
            if channel.enabled and channel.real:
                channel.monitor = np.nan if channel.waitToStabilize else self.values[channel.module][channel.id]

    def toggleOn(self) -> None:
        super().toggleOn()
        if self.modules:
            for module in self.modules:
                self.ISEGWriteRead(message=f":VOLT {'ON' if self.controllerParent.isOn() else 'OFF'},(#{module}@0-{self.maxID})\r\n")

    def closeCommunication(self) -> None:
        super().closeCommunication()
        self.socket = None
        self.initialized = False
        self.closing = False

    def ISEGWriteRead(self, message: str, already_acquired: bool = False) -> str:
        """ISEG specific serial write and read.

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
                if lock_acquired and self.socket:
                    self.socket.sendall(message.encode())
                    response = self.socket.recv(4096).decode('utf-8')
                    self.print('ISEGWriteRead message: ' + message.replace('\r', '').replace('\n', '') +
                               ', response: ' + response.replace('\r', '').replace('\n', ''), flag=PRINT.TRACE)
        return response
