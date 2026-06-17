# pylint: disable=[missing-module-docstring]  # see class docstrings
import ctypes
from typing import cast

import numpy as np

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, getDarkMode, getTestMode, parameterDict
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [PICO]


class PICO(Device):
    """Reads the temperature of sensors attached to a pico PT-104.

    It allows to switch units between K and °C.
    """

    name = 'PICO'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.OUTPUTDEVICE
    unit = 'K'
    iconFile = 'pico_104.png'
    channels: 'list[TemperatureChannel]'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = TemperatureChannel
        self.controller = TemperatureController(controllerParent=self)

    def initGUI(self) -> None:
        super().initGUI()
        self.unitAction = self.addStateAction(event=self.changeUnit, toolTipFalse='Change to °C', iconFalse=self.makeIcon('tempC_dark.png'),
                                               toolTipTrue='Change to K', iconTrue=self.makeIcon('tempK_dark.png'), attr='displayC')

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
            self.liveDisplay.plot(apply=True)
        if self.staticDisplayActive():
            self.staticDisplay.plot()

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings[f'{self.name}/Interval'][Parameter.VALUE] = 5000  # overwrite default value
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

    CHANNEL = 'Channel'
    DATATYPE = 'Datatype'
    NOOFWIRES = 'noOfWires'
    channelParent: PICO

    def getDefaultChannel(self) -> dict[str, dict]:

        # definitions for type hinting
        self.channel: str
        self.datatype: str
        self.noOfWires: str

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'Temp (K)'
        channel[self.VALUE][Parameter.UNIT] = 'K'
        channel[self.VALUE][Parameter.VALUE] = np.nan  # undefined until communication established
        channel[self.CHANNEL] = parameterDict(value='USBPT104_CHANNEL_1', parameterType=PARAMETERTYPE.COMBO, advanced=True,
                                    attr='channel', items='USBPT104_CHANNEL_1, USBPT104_CHANNEL_2, USBPT104_CHANNEL_3, USBPT104_CHANNEL_4')
        channel[self.DATATYPE] = parameterDict(value='USBPT104_PT100', parameterType=PARAMETERTYPE.COMBO, advanced=True,
                                    attr='datatype', items='USBPT104_PT100')
        channel[self.NOOFWIRES] = parameterDict(value='4', parameterType=PARAMETERTYPE.COMBO, advanced=True,
                                    attr='noOfWires', items='2, 3, 4')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.displayedParameters.append(self.CHANNEL)
        self.displayedParameters.append(self.DATATYPE)
        self.displayedParameters.append(self.NOOFWIRES)

    def realChanged(self) -> None:
        self.getParameterByName(self.CHANNEL).setVisible(self.real)
        self.getParameterByName(self.DATATYPE).setVisible(self.real)
        self.getParameterByName(self.NOOFWIRES).setVisible(self.real)


class TemperatureController(DeviceController):

    chandle = ctypes.c_int16()
    controllerParent: PICO

    def __init__(self, controllerParent) -> None:
        super().__init__(controllerParent)
        # Download PicoSDK as described here https://github.com/picotech/picosdk-python-wrappers/tree/master
        # If needed, add SDK installation path to PATH (e.g. C:\Program Files\Pico Technology\SDK)
        # Importing modules here makes sure that the module is loaded without errors and
        # missing SDK is only raised if users enable this plugin.
        from picosdk.functions import assert_pico_ok  # noqa: PLC0415
        from picosdk.usbPT104 import usbPt104  # noqa: PLC0415
        self.usbPt104 = usbPt104
        self.assert_pico_ok = assert_pico_ok

    def runInitialization(self) -> None:
        try:
            self.assert_pico_ok(self.usbPt104.UsbPt104OpenUnit(ctypes.byref(self.chandle), 0))  # type: ignore  # noqa: PGH003
            for channel in self.controllerParent.getChannels():
                self.assert_pico_ok(self.usbPt104.UsbPt104SetChannel(self.chandle, self.usbPt104.PT104_CHANNELS[channel.channel],  # type: ignore  # noqa: PGH003
                                                        self.usbPt104.PT104_DATA_TYPE[channel.datatype], ctypes.c_int16(int(channel.noOfWires))))  # type: ignore  # noqa: PGH003
            self.signalComm.initCompleteSignal.emit()
        except Exception as e:  # pylint: disable=[broad-except]  # noqa: BLE001
            self.print(f'Error while initializing: {e}', flag=PRINT.ERROR)
        finally:
            self.initializing = False

    def readNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.active and channel.real:
                try:
                    meas = ctypes.c_int32()
                    self.usbPt104.UsbPt104GetValue(self.chandle, self.usbPt104.PT104_CHANNELS[channel.channel], ctypes.byref(meas), 1)  # type: ignore  # noqa: PGH003
                    self.print(f'UsbPt104GetValue channel.channel: {channel.channel}, response {meas.value}', flag=PRINT.TRACE)
                    if meas.value != ctypes.c_long(0).value:  # 0 during initialization phase
                        self.values[i] = float(meas.value) / 1000 + 273.15  # always return Kelvin
                    else:
                        self.values[i] = np.nan
                except ValueError as e:
                    self.print(f'Error while reading temp: {e}', flag=PRINT.ERROR)
                    self.errorCount += 1
                    self.values[i] = np.nan

    def fakeNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.active and channel.real:
                # exponentially approach target or room temp + small fluctuation
                self.values[i] = float(self.rng.integers(1, 300)) if np.isnan(self.values[i]) else self.values[i] * self.rng.uniform(.99, 1.01)  # allow for small fluctuation

    def closeCommunication(self) -> None:
        super().closeCommunication()
        if not getTestMode() and self.initialized and self.lock:
            # typically we would check for usbPt104 is not None instead of getTestMode,
            # but here usbPt104 is imported as an object and cannot be reinstantiated and should thus never set to None.
            with self.lock.acquire_timeout(1, timeoutMessage='Cannot acquire lock to close PT-104.'):
                self.usbPt104.UsbPt104CloseUnit(self.chandle)  # type: ignore  # noqa: PGH003
        self.initialized = False
        self.closing = False
