# pylint: disable=[missing-module-docstring]  # see class docstrings
from random import choices
from typing import cast

import numpy as np
import pyvisa
from PyQt6.QtCore import QTimer

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, parameterDict
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [RSPD3303C]


class RSPD3303C(Device):
    """Contains a list of voltages channels from a single RSPD3303C power supplies with 2 analog outputs.

    In case of any issues, first test communication independently with EasyPowerX.
    """

    name = 'RSPD3303C'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = 'V'
    useMonitors = True
    useOnOffLogic = True
    iconFile = 'RSPD3303C.png'
    channels: 'list[VoltageChannel]'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = VoltageChannel
        self.shutDownTimer = QTimer(self)
        self.shutDownTimer.timeout.connect(self.updateTimer)

    def initGUI(self) -> None:
        super().initGUI()
        self.controller = VoltageController(controllerParent=self)  # after all channels loaded

    def finalizeInit(self) -> None:
        self.shutDownTime = 0
        super().finalizeInit()

    def getChannels(self) -> 'list[VoltageChannel]':
        return cast('list[VoltageChannel]', super().getChannels())

    ADDRESS = 'Address'
    address: str
    SHUTDOWNTIMER = 'Shutdown timer'
    shutDownTime: int

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings[f'{self.name}/Interval'][Parameter.VALUE] = 1000  # overwrite default value
        defaultSettings[f'{self.name}/{self.MAXDATAPOINTS}'][Parameter.VALUE] = 1E5  # overwrite default value
        defaultSettings[f'{self.name}/{self.SHUTDOWNTIMER}'] = parameterDict(value=0, parameterType=PARAMETERTYPE.INT, attr='shutDownTime', instantUpdate=False,
                                                                     toolTip=f'Time in minutes. Starts a countdown which turns {self.name} off once expired.',
                                                                     event=self.initTimer, internal=True)
        defaultSettings[f'{self.name}/{self.ADDRESS}'] = parameterDict(value='USB0::0xF4EC::0x1430::SPD3EGGD7R2257::INSTR', parameterType=PARAMETERTYPE.TEXT, attr='address')
        return defaultSettings

    def initTimer(self) -> None:
        """Initialize the shutdown timer. Only called once user confirms change of shutDownTime."""
        if self.shutDownTime != 0:
            if not self.isOn():
                self.print('Ignoring shutdown timer as device is already off.')
                return
            self.print(f'Will turn off in {self.shutDownTime} minutes.')
            self.shutDownTimer.start(60000)  # 1 min steps

    def updateTimer(self) -> None:
        """Update the shutdowntimer, notifies about remaining time and turns of the device once expired."""
        self.pluginManager.Settings.settings[f'{self.name}/{self.SHUTDOWNTIMER}'].setValueWithoutEvents(max(0, self.shutDownTime - 1))
        if self.shutDownTime > 1:
            if (self.shutDownTime < 10 or  # notify every minute  # noqa: PLR0916, PLR2004
            (self.shutDownTime < 60 and self.shutDownTime % 10 == 0) or  # notify every 10 minutes  # noqa: PLR2004
            (self.shutDownTime < 600 and self.shutDownTime % 60 == 0) or  # notify every hour  # noqa: PLR2004
            (self.shutDownTime % 600 == 0)):  # notify every 10 hours
                self.print(f'Will turn off in {self.shutDownTime} minutes.')
        elif self.shutDownTime == 1:
            self.print('Timer expired. Setting PID off and heater voltages to 0 V.', flag=PRINT.WARNING)
            if hasattr(self.pluginManager, 'PID'):
                self.pluginManager.PID.setOn(on=False)
            for channel in self.channels:
                channel.value = 0
        elif self.shutDownTime == 0:
            self.print('Timer expired. Turning off.', flag=PRINT.WARNING)
            self.shutDownTimer.stop()
            self.setOn(on=False)


class VoltageChannel(Channel):

    CURRENT = 'Current'
    POWER = 'Power'
    ID = 'ID'
    channelParent: RSPD3303C

    def getDefaultChannel(self) -> dict[str, dict]:

        # definitions for type hinting
        self.power: float
        self.current: float
        self.id: int

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'Voltage (V)'  # overwrite to change header
        channel[self.VALUE][Parameter.UNIT] = 'V'
        channel[self.MONITOR][Parameter.HEADER] = 'Monitor (V)'
        channel[self.MONITOR][Parameter.UNIT] = 'V'
        channel[self.MIN][Parameter.VALUE] = 0
        channel[self.MAX][Parameter.VALUE] = 1  # start with safe limits
        channel[self.POWER] = parameterDict(value=np.nan, parameterType=PARAMETERTYPE.FLOAT, advanced=False, recorded=True,
                                                indicator=True, attr='power', restore=False, header='Power (W)', unit='W')
        channel[self.CURRENT] = parameterDict(value=np.nan, parameterType=PARAMETERTYPE.FLOAT, advanced=True, unit='A',
                                                indicator=True, attr='current', restore=False, header='Current (A)')
        channel[self.ID] = parameterDict(value=0, parameterType=PARAMETERTYPE.INT, advanced=True,
                                    header='ID', minimum=0, maximum=99, attr='id')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.insertDisplayedParameter(self.CURRENT, before=self.MIN)
        self.insertDisplayedParameter(self.POWER, before=self.MIN)
        self.displayedParameters.append(self.ID)

    def tempParameters(self) -> list[str]:
        return [*super().tempParameters(), self.POWER, self.CURRENT]

    def monitorChanged(self) -> None:
        # overwriting super().monitorChanged() to set 0 as expected value when device is off
        self.updateWarningState(self.enabled and self.channelParent.controller.acquiring and ((self.channelParent.isOn() and abs(self.monitor - self.value) > 1)
                                                                    or (not self.channelParent.isOn() and abs(self.monitor - 0) > 1)))

    def realChanged(self) -> None:
        self.getParameterByName(self.POWER).setVisible(self.real)
        self.getParameterByName(self.CURRENT).setVisible(self.real)
        self.getParameterByName(self.ID).setVisible(self.real)
        super().realChanged()


class VoltageController(DeviceController):

    port: 'pyvisa.resources.usb.USBInstrument | None'
    controllerParent: RSPD3303C
    rm: 'pyvisa.ResourceManager | None' = None

    def runInitialization(self) -> None:
        try:
            self.rm = pyvisa.ResourceManager()
            # name = self.rm.list_resources()  # noqa: ERA001
            self.port = self.rm.open_resource(self.controllerParent.address, open_timeout=500)  # type: ignore  # noqa: PGH003
            if self.port:
                self.controllerParent.print(self.port.query('*IDN?'))
                self.signalComm.initCompleteSignal.emit()
        except Exception as e:  # pylint: disable=[broad-except]  # socket does not throw more specific exception  # noqa: BLE001
            self.print(f'Could not establish connection to {self.controllerParent.address}. Exception: {e}', flag=PRINT.WARNING)
        finally:
            self.initializing = False

    def initializeValues(self, reset: bool = False) -> None:  # noqa: ARG002
        self.currents = np.array([np.nan] * len(self.controllerParent.getChannels()))
        self.values = np.array([np.nan] * len(self.controllerParent.getChannels()))

    def readNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.active and channel.real:
                try:
                    self.values[i] = float(self.RSQuery(f'MEAS:VOLT? CH{channel.id}', already_acquired=True))
                    self.currents[i] = float(self.RSQuery(f'MEAS:CURR? CH{channel.id}', already_acquired=True))
                except ValueError as e:
                    self.print(f'Error while reading values: {e}', flag=PRINT.ERROR)
                    self.errorCount += 1
                    self.values[i] = np.nan
                    self.currents[i] = np.nan

    def fakeNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real:
                if self.controllerParent.isOn() and channel.enabled:
                    # fake values with noise and 10% channels with offset to simulate defect channel or short
                    self.values[i] = channel.value + 5 * choices([0, 1], [.98, .02])[0] + self.rng.random()
                else:
                    self.values[i] = 0 + 5 * choices([0, 1], [.9, .1])[0] + self.rng.random()
                self.currents[i] = 50 / self.values[i] if self.values[i] != 0 else 0  # simulate 50 W

    def applyValue(self, channel: VoltageChannel) -> None:
        if channel.enabled:
            # ignore channels that are disabled
            # prevents edge case where a disabled channel with the same channel ID overwrites an enabled channel
            # requires users to make sure channels are set to 0 (safe state) before disabling them.
            self.RSWrite(f'CH{channel.id}:VOLT {channel.value if self.controllerParent.isOn() else 0}')

    def updateValues(self) -> None:
        # Overwriting to also update custom current and power parameters.
        if self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real:
                channel.monitor = np.nan if channel.waitToStabilize else self.values[i]
                channel.current = np.nan if channel.waitToStabilize else self.currents[i]
                channel.power = np.nan if channel.waitToStabilize else channel.monitor * channel.current

    def toggleOn(self) -> None:
        super().toggleOn()
        for channel in self.controllerParent.getChannels():
            self.RSWrite(f"OUTPUT CH{channel.id},{'ON' if self.controllerParent.isOn() else 'OFF'}")

    def closeCommunication(self) -> None:
        super().closeCommunication()
        if self.rm:
            self.rm.close()
        self.initialized = False
        self.closing = False

    def RSWrite(self, message) -> None:
        """RS specific pyvisa write.

        :param message: The message to be send.
        :type message: str
        """
        if self.port:
            with self.lock.acquire_timeout(1, timeoutMessage=f'Cannot acquire lock for message {message}.') as lock_acquired:
                if lock_acquired:
                    self.print('RSWrite message: ' + message.replace('\r', '').replace('\n', ''), flag=PRINT.TRACE)
                    self.port.write(message)

    def RSQuery(self, query, already_acquired=False) -> str:
        """RS specific pyvisa query.

        :param query: The message to be send.
        :type query: str
        :param already_acquired: Indicates if the lock has already been acquired, defaults to False
        :type already_acquired: bool, optional
        :return: The response received.
        :rtype: str
        """
        response = ''
        if self.port:
            with self.lock.acquire_timeout(1, timeoutMessage=f'Cannot acquire lock for query {query}.', already_acquired=already_acquired) as lock_acquired:
                if lock_acquired:
                    response = self.port.query(query)
                    self.print('RSQuery query: ' + query.replace('\r', '').replace('\n', '') + f', response: {response}', flag=PRINT.TRACE)
        return response
