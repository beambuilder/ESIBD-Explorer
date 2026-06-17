# pylint: disable=[missing-module-docstring] # only single class in module
# install lakeshore drivers from here if not automatically installed via windows updates: https://www.lakeshore.com/resources/software/drivers

import time
from typing import cast

import numpy as np
from lakeshore import Model335
from lakeshore.generic_instrument import InstrumentException

from esibd.core import (
    PARAMETERTYPE,
    PLUGINTYPE,
    PRINT,
    Channel,
    DeviceController,
    Parameter,
    ToolButton,
    getDarkMode,
    parameterDict,
)
from esibd.plugins import Device, Plugin, getTestMode


def providePlugins() -> 'list[type[Plugin]]':
    """Indicate that this module provides plugins. Returns list of provided plugins."""
    return [LS335]


class LS335(Device):
    """Device that reads and controls temperature using a LakeShore 335.

    It will read PID values from the device when connecting and allow to change them in advanced mode.
    It allows to switch units between K and °C.
    """

    name = 'LS335'
    version = '1.0'
    supportedVersion = '1.0'
    iconFile = 'LS335.png'
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = 'K'
    useMonitors = True
    useOnOffLogic = True

    controller: 'TemperatureController'
    channels: 'list[TemperatureChannel]'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = TemperatureChannel
        self.controller = TemperatureController(controllerParent=self)

    def initGUI(self) -> None:
        super().initGUI()
        self.unitAction = self.addStateAction(event=self.changeUnit, toolTipFalse='Change to °C', iconFalse=self.makeIcon('tempC_dark.png'),
                                               toolTipTrue='Change to K', iconTrue=self.makeIcon('tempK_dark.png'), attr='displayC')

    def afterFinalizeInit(self) -> None:
        super().afterFinalizeInit()
        self.onAction.setVisible(False)

    def getChannels(self) -> 'list[TemperatureChannel]':
        return cast('list[TemperatureChannel]', super().getChannels())

    def runTestParallel(self) -> None:
        self.testControl(self.unitAction, self.unitAction.state)
        super().runTestParallel()

    def changeUnit(self) -> None:
        """Update plots to account for change of unit."""
        self.pluginManager.connectAllSources()
        if self.liveDisplayActive():
            self.clearPlot()
            self.liveDisplay.plot()
        if self.staticDisplayActive():
            self.staticDisplay.plot()

    COM: str

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings[f'{self.name}/Interval'][Parameter.VALUE] = 5000  # overwrite default value
        defaultSettings[f'{self.name}/LS335 COM'] = parameterDict(value='COM3', toolTip='COM port of LakeShore 335.', items=','.join([f'COM{x}' for x in range(1, 25)]),
                                          parameterType=PARAMETERTYPE.COMBO, attr='COM')
        defaultSettings[f'{self.name}/{self.MAXDATAPOINTS}'][Parameter.VALUE] = 1E6  # overwrite default value
        return defaultSettings

    def convertDataDisplay(self, data: np.ndarray) -> np.ndarray:
        return data - 273.15 if self.unitAction.state else data

    def getDisplayUnit(self) -> str:
        return '°C' if self.unitAction.state else self.unit

    def updateTheme(self) -> None:
        super().updateTheme()
        self.unitAction.iconFalse = self.makeIcon('tempC_dark.png' if getDarkMode() else 'tempC_light.png')
        self.unitAction.iconTrue = self.makeIcon('tempK_dark.png' if getDarkMode() else 'tempK_light.png')
        self.unitAction.updateIcon(self.unitAction.state)


class TemperatureChannel(Channel):
    """UI for pressure with integrated functionality."""

    ID = 'ID'
    HEATER = 'HEATER'
    active = True
    channelParent: LS335
    KP = 'Kp'
    KI = 'Ki'
    KD = 'Kd'

    def getDefaultChannel(self) -> dict[str, dict]:

        # definitions for type hinting
        self.id: str
        self.heater: int
        self.channel_active: bool
        self.Kp: float
        self.Ki: float
        self.Kd: float

        channel = super().getDefaultChannel()
        channel.pop(Channel.EQUATION)
        channel[self.VALUE][Parameter.HEADER] = 'Set Temp (K)'  # overwrite existing parameter to change header
        channel[self.VALUE][Parameter.UNIT] = 'K'
        channel[self.MONITOR][Parameter.HEADER] = 'Monitor (K)'  # overwrite existing parameter to change header
        channel[self.MONITOR][Parameter.UNIT] = 'K'
        channel[self.VALUE][Parameter.INSTANTUPDATE] = False
        channel[self.ACTIVE] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, attr='channel_active', toolTip='Activate PID control.',
                                              event=self.channel_active_changed)
        channel[self.ID] = parameterDict(value='A', parameterType=PARAMETERTYPE.COMBO, advanced=True,
                                        items='A, B', attr='id')
        channel[self.HEATER] = parameterDict(value=1, parameterType=PARAMETERTYPE.INTCOMBO, advanced=True,
                                        items='1, 2', attr='heater')
        channel[self.KP] = parameterDict(value=np.nan, parameterType=PARAMETERTYPE.FLOAT, minimum=0, maximum=1000, attr='Kp', instantUpdate=False,
                                        event=self.setPID, advanced=True, restore=False)  # value = 500
        channel[self.KI] = parameterDict(value=np.nan, parameterType=PARAMETERTYPE.FLOAT, minimum=1, maximum=1000, attr='Ki', instantUpdate=False,
                                        event=self.setPID, advanced=True, restore=False)  # value = 14
        channel[self.KD] = parameterDict(value=np.nan, parameterType=PARAMETERTYPE.FLOAT, minimum=0, maximum=200, attr='Kd', instantUpdate=False,
                                        event=self.setPID, advanced=True, restore=False)  # value = 100
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.displayedParameters.remove(self.EQUATION)
        self.insertDisplayedParameter(self.ACTIVE, before=self.NAME)
        self.displayedParameters.append(self.ID)
        self.displayedParameters.append(self.HEATER)
        self.insertDisplayedParameter(self.KP, before=self.OPTIMIZE)
        self.insertDisplayedParameter(self.KI, before=self.OPTIMIZE)
        self.insertDisplayedParameter(self.KD, before=self.OPTIMIZE)

    def initGUI(self, item) -> None:
        super().initGUI(item)
        active = self.getParameterByName(self.ACTIVE)
        value = active.value
        active.widget = ToolButton()
        active.applyWidget()
        if active.check:
            active.check.setMaximumHeight(active.rowHeight)
            active.check.setText(self.ACTIVE.title())
            active.check.setMinimumWidth(5)
            active.check.setCheckable(True)
        active.value = value
        self.updateColor()  # update color for new Widget
        self.channel_active_changed()

    def channel_active_changed(self) -> None:
        """Turn heater on or off. Only show PID controls if heater is running."""
        self.channelParent.controller.toggleOnFromThread(parallel=True)
        self.getParameterByName(self.KP).setVisible(self.channel_active)
        self.getParameterByName(self.KI).setVisible(self.channel_active)
        self.getParameterByName(self.KD).setVisible(self.channel_active)

    def setPID(self) -> None:
        """Set PID values."""
        self.channelParent.controller.setPID(self)


class TemperatureController(DeviceController):
    """Implements communication with LakeShore 335.

    PID control will only be active if activated on channel and device level.
    """

    ls335 = None
    controllerParent: LS335

    def runInitialization(self) -> None:
        try:
            self.ls335 = Model335(baud_rate=57600, com_port=self.controllerParent.COM)  # may raise AttributeError that can not be excepted
            time.sleep(500)  # TODO test if initialization gets more stable with this delay
            self.signalComm.initCompleteSignal.emit()
        except (AttributeError, Exception) as e:  # pylint: disable=[broad-except]  # noqa: BLE001
            self.print(f'Error while initializing: {e}', flag=PRINT.ERROR)
        finally:
            self.initializing = False

    def initializeValues(self, reset: bool = False) -> None:
        super().initializeValues(reset)
        if reset:
            for channel in self.controllerParent.channels:
                if channel.real:
                    # explicitly display NaN to indicate that the real value cannot be known until communication is established.
                    channel.Kp = np.nan
                    channel.Ki = np.nan
                    channel.Kd = np.nan

    def initComplete(self) -> None:
        for channel in self.controllerParent.channels:
            if channel.real:
                # update pid values from hardware to make sure the displayed value is meaningful
                try:
                    heater_pid = self.ls335.get_heater_pid(channel.heater) if self.ls335 and not getTestMode() else {'gain': -1, 'integral': -1, 'ramp_rate': -1}
                    Kp = channel.getParameterByName(channel.KP)
                    Kp.setValueWithoutEvents(heater_pid['gain'])
                    Ki = channel.getParameterByName(channel.KI)
                    Ki.setValueWithoutEvents(heater_pid['integral'])
                    Kd = channel.getParameterByName(channel.KD)
                    Kd.setValueWithoutEvents(heater_pid['ramp_rate'])
                except InstrumentException as e:
                    self.print(f'Error while initializing channel {channel.name}: {e}\nTry to restart.', flag=PRINT.ERROR)
        super().initComplete()

    def readNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if self.ls335:
                value = self.ls335.get_kelvin_reading(channel.id)
                try:
                    self.values[i] = float(value)
                except ValueError as e:
                    self.print(f'Error while reading temp: {e}', flag=PRINT.ERROR)
                    self.errorCount += 1
                    self.values[i] = np.nan

    def fakeNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            # exponentially approach target or room temp + small fluctuation
            if channel.enabled and channel.real:
                self.values[i] = max((self.values[i] + self.rng.uniform(-1, 1)) + 0.1 * ((channel.value if self.controllerParent.isOn() else 300) - self.values[i]), 0)

    def applyValue(self, channel) -> None:
        self.set_control_setpoint(channel)

    def toggleOn(self) -> None:
        super().toggleOn()
        for channel in self.controllerParent.channels:
            if self.ls335:
                if channel.channel_active and self.controllerParent.isOn():
                    # self.ls335.set_heater_pid(output=channel.heater, gain=200, integral=14, derivative=100)  # noqa: ERA001
                    # self.ls335._set_autotune(output=channel.heater, mode=self.ls335.AutotuneMode.P_I_D)  # noqa: ERA001
                    # self.ls335.set_heater_output_mode(output=channel.heater, mode=self.ls335.HeaterOutputMode.CLOSED_LOOP, channel=channel.id)  # noqa: ERA001
                    # self.ls335.set_heater_setup_one(self.ls335.HeaterResistance.HEATER_50_OHM, 0.6, self.ls335.HeaterOutputDisplay.POWER)  # noqa: ERA001
                    self.ls335.set_heater_range(channel.heater, self.ls335.HeaterRange.HIGH)
                    self.set_control_setpoint(channel=channel)
                    # self.ls335.turn_relay_on(relay_number=channel.heater)  # noqa: ERA001
                else:
                    # self.ls335.set_heater_output_mode(output=channel.heater, mode=self.ls335.HeaterOutputMode.OFF, channel=channel.id)  # noqa: ERA001
                    self.ls335.set_heater_range(channel.heater, self.ls335.HeaterRange.OFF)

    def set_control_setpoint(self, channel) -> None:
        """Set the heater and temperature setpoint for the given channel.

        :param channel: The channel for which to set the setpoint.
        :type channel: esibd.code.Channel
        """
        if self.ls335:
            self.ls335.set_control_setpoint(output=channel.heater, value=channel.value)

    def setPID(self, channel: TemperatureChannel) -> None:
        """Set PID values for given Channel.

        :param channel: The channel for which parameters should be set.
        :type channel: TemperatureChannel
        """
        if self.ls335:
            if np.isnan([channel.Kp, channel.Ki, channel.Kd]).any():
                return
            if channel.real and channel.channel_active and self.controllerParent.isOn():
                self.ls335.set_heater_pid(channel.heater, channel.Kp, channel.Ki, channel.Kd)
                self.print(f'Setting PID values of channel {channel.name} to Kp: {channel.Kp}, Ki: {channel.Ki}, Kd: {channel.Kd}.', flag=PRINT.DEBUG)
            else:
                self.print('Could not set PID values. Make sure channel is active, and temperature control is on.', flag=PRINT.WARNING)

    def closeCommunication(self) -> None:
        super().closeCommunication()
        if self.ls335:
            if self.ls335.device_serial:
                with self.lock.acquire_timeout(1, timeoutMessage='Could not acquire lock before closing port.'):
                    self.ls335.disconnect_usb()
            self.ls335 = None
        self.initialized = False
        self.closing = False
