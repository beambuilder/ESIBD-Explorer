# pylint: disable=[missing-module-docstring]  # see class docstrings

import numpy as np
import serial
from PyQt6.QtWidgets import QMessageBox

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, getDarkMode, parameterDict
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [Temperature]


class Temperature(Device):
    """Reads the temperature of a silicon diode sensor via Sunpower CryoTel controller.

    It allows to switch units between K and °C.
    """

    name = 'Temperature'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = 'K'
    useMonitors = True
    useOnOffLogic = True
    iconFile = 'temperature.png'
    channels: 'list[TemperatureChannel]'
    controller: 'TemperatureController'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = TemperatureChannel
        self.controller = TemperatureController(controllerParent=self)

    def initGUI(self) -> None:
        super().initGUI()
        self.unitAction = self.addStateAction(event=self.changeUnit, toolTipFalse='Change to °C', iconFalse=self.makeIcon('tempC_dark.png'),
                                               toolTipTrue='Change to K', iconTrue=self.makeIcon('tempK_dark.png'), attr='displayC')

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

    CRYOTELCOM: str
    toggleThreshold: int

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings[f'{self.name}/Interval'][Parameter.VALUE] = 5000  # overwrite default value
        defaultSettings[f'{self.name}/CryoTel COM'] = parameterDict(value='COM3', toolTip='COM port of Sunpower CryoTel.', items=','.join([f'COM{x}' for x in range(1, 25)]),
                                          parameterType=PARAMETERTYPE.COMBO, attr='CRYOTELCOM')
        defaultSettings[f'{self.name}/Toggle threshold'] = parameterDict(value=15, toolTip='Cooler is toggled on and off to stay within threshold from set value.',
                                          parameterType=PARAMETERTYPE.INT, attr='toggleThreshold')
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

    def setOn(self, on: 'bool | None' = None) -> None:
        # super().setOn(on)  # do not use super but implement specifically in this case.  # noqa: ERA001
        if on is not None and self.onAction.state is not on:
            self.onAction.state = on
        if self.initialized:
            # updateValues not needed for CryoTel, we use the power which is restored in the device and do not need to update the unused temperature setpoint.
            # TODO: when turning off, updateValues(on=False) caused a dead lock at the com port freezing the entire application until the port is physically disconnected.
            # self.updateValues(apply=True)  # noqa: ERA001
            if self.controller:
                self.controller.toggleOnFromThread(parallel=False)
            else:
                for channel in self.channels:
                    if channel.controller:
                        channel.controller.toggleOnFromThread(parallel=False)
        elif self.isOn():
            self.initializeCommunication()


class TemperatureChannel(Channel):
    """UI for pressure with integrated functionality."""

    CRYOTEL = 'CryoTel'
    POWER = 'Power'
    power: int
    channelParent: Temperature

    def getDefaultChannel(self) -> dict[str, dict]:
        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'Temp (K) DISABLED'
        channel[self.VALUE][Parameter.UNIT] = 'K'
        channel[self.VALUE][Parameter.INDICATOR] = True
        channel[self.VALUE][Parameter.ADVANCED] = True
        channel[self.MONITOR][Parameter.HEADER] = 'Monitor (K)'
        channel[self.MONITOR][Parameter.UNIT] = 'K'
        channel[self.POWER] = parameterDict(value=120, parameterType=PARAMETERTYPE.INT, minimum=80, maximum=180, attr='power', instantUpdate=False,
                                event=self.setPower, unit='W', header='Power (W)')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.insertDisplayedParameter(self.POWER, before=self.MONITOR)

    def setPower(self) -> None:
        """Set the power."""
        self.channelParent.controller.setPower(self)

    def monitorChanged(self) -> None:
        """Use power rather than temperature setpoint."""


class TemperatureController(DeviceController):

    controllerParent: Temperature

    def __init__(self, controllerParent) -> None:
        super().__init__(controllerParent)
        self.messageBox = QMessageBox(QMessageBox.Icon.Information, 'Water cooling!', 'Water cooling!', buttons=QMessageBox.StandardButton.Ok)
        self.toggleCounter = 0

    def runInitialization(self) -> None:
        try:
            self.port = serial.Serial(
                self.controllerParent.CRYOTELCOM,
                baudrate=9600,  # used to be 4800
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                xonxoff=False,
                timeout=3)
            # self.CryoTelWriteRead('SET TBAND=5')  # set temperature band  # noqa: ERA001
            # self.CryoTelWriteRead('SET PID=2')# set temperature control mode # noqa: ERA001
            # self.CryoTelWriteRead('SET SSTOPM=0')  # enable use of SET SSTOP # noqa: ERA001
            # self.CryoTelWriteRead('SENSOR')  # test if configured for correct temperature sensor DT-670 # noqa: ERA001
            # self.CryoTelWriteRead('SENSOR=DT-670')  # set Sensor if applicable # noqa: ERA001
            self.signalComm.initCompleteSignal.emit()
        except Exception as e:  # pylint: disable=[broad-except]  # noqa: BLE001
            self.print(f'Error while initializing: {e}', flag=PRINT.ERROR)
        finally:
            self.initializing = False

    def readNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real:
                value = self.CryoTelWriteRead(message='TC')  # Display Cold-Tip Temperature (same on old and new controller)
                if not value:
                    self.print('CryoTel returned empty string. Message: TC', flag=PRINT.TRACE)
                    return
                try:
                    self.values[i] = float(value)
                except ValueError as e:
                    self.print(f'Error while reading temp: {e}', flag=PRINT.ERROR)
                    self.errorCount += 1
                    self.values[i] = np.nan

        # toggle cryo on off to stabilize at temperatures above what is possible with minimal power
        # temporary mode. to be replaced by temperature regulation using heater.
        # only test once a minute as cooler takes 30 s to turn on or off
        # in case of over current error the cooler won't turn on and there is no need for additional safety check
        self.toggleCounter += 1
        if (self.controllerParent.isOn() and np.mod(self.toggleCounter, int(60000 / self.controllerParent.interval)) == 0 and
            self.controllerParent.getChannels()[0].monitor != 0 and not np.isnan(self.controllerParent.getChannels()[0].monitor)):
            if self.controllerParent.getChannels()[0].monitor < self.controllerParent.getChannels()[0].value - self.controllerParent.toggleThreshold:
                self.print(f'Toggle cooler off. {self.controllerParent.getChannels()[0].monitor} K is under lower threshold '
                           f'of {self.controllerParent.getChannels()[0].value - self.controllerParent.toggleThreshold} K.')
                self.CryoTelWriteRead(message='COOLER=OFF')
            elif self.controllerParent.getChannels()[0].monitor > self.controllerParent.getChannels()[0].value + self.controllerParent.toggleThreshold:
                if self.CryoTelWriteRead('COOLER') != 'POWER':  # avoid sending command repeatedly
                    self.print(f'Toggle cooler on. {self.controllerParent.getChannels()[0].monitor} K is over upper threshold '
                               f'of {self.controllerParent.getChannels()[0].value + self.controllerParent.toggleThreshold} K.')
                    self.CryoTelWriteRead(message='COOLER=POWER')

    def fakeNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            # exponentially approach target or room temp + small fluctuation
            if channel.enabled and channel.real:
                self.values[i] = max((self.values[i] + self.rng.uniform(-1, 1)) + 0.1 * ((channel.value if self.controllerParent.isOn() else 300) - self.values[i]), 0)

    def applyValue(self, channel: TemperatureChannel) -> None:
        self.CryoTelWriteRead(message=f'TTARGET={channel.value}')  # used to be SET TTARGET=

    def toggleOn(self) -> None:
        super().toggleOn()
        if self.controllerParent.isOn():
            self.CryoTelWriteRead(message='COOLER=POWER')  # 'COOLER=ON' start (used to be 'SET SSTOP=0')
        else:
            self.CryoTelWriteRead(message='COOLER=OFF')  # stop (used to be 'SET SSTOP=1')
        self.messageBox.setText(f"Remember to turn water cooling {'on' if self.controllerParent.isOn() else 'off'}"
                                f" and gas ballast {'off' if self.controllerParent.isOn() else 'on'}!")
        self.messageBox.setWindowIcon(self.controllerParent.getIcon())
        if not self.controllerParent.testing:
            self.messageBox.open()  # show non blocking, defined outside so it does not get eliminated when the function completes.
            self.messageBox.raise_()
        self.controllerParent.processEvents()

    def setPower(self, channel: TemperatureChannel) -> None:
        """Set the power.

        :param channel: The channel for which to change the power.
        :type channel: TemperatureChannel
        """
        self.CryoTelWriteRead(message=f'PWOUT={channel.power}')

    def closeCommunication(self) -> None:
        self.print('closeCommunication', flag=PRINT.DEBUG)
        self.closing = True
        if self.acquiring:
            self.stopAcquisition()
        if self.port:
            with self.lock.acquire_timeout(1, timeoutMessage='Could not acquire lock before closing port.'):
                self.port.close()
                self.port = None
        self.initialized = False
        self.closing = False

    def CryoTelWriteRead(self, message: str) -> str:
        """CryoTel specific serial write and read.

        :param message: The serial message to be send.
        :type message: str
        :return: The serial response received.
        :rtype: str
        """
        # TODO: lock is not working as expected, possibly as a consequence of hardware issue with deadlocked com port
        # with self.lock.acquire_timeout(1, timeoutMessage=f'Cannot acquire lock for message: {message}') as lock_acquired:
        #    if lock_acquired:
        self.CryoTelWrite(message)
        return self.CryoTelRead()

    def CryoTelWrite(self, message: str) -> None:
        """CryoTel specific serial write.

        :param message: The serial message to be send.
        :type message: str
        """
        if self.port:
            self.serialWrite(self.port, f'{message}\r')
            self.CryoTelRead()  # repeats query

    def CryoTelRead(self) -> str:
        """TPG specific serial read.

        :return: The response received.
        :rtype: str
        """
        if self.port:
            return self.serialRead(self.port)
        return ''
