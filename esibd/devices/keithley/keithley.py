# pylint: disable=[missing-module-docstring]  # see class docstrings
import time

import numpy as np
import pyvisa

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, parameterDict
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [KEITHLEY]


class KEITHLEY(Device):
    """Contains a list of current channels, each corresponding to a single KEITHLEY 6487 picoammeter."""

    name = 'KEITHLEY'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.OUTPUTDEVICE
    unit = 'pA'
    iconFile = 'keithley.png'
    useOnOffLogic = True
    useBackgrounds = True  # record backgrounds for data correction
    channels: 'list[CurrentChannel]'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = CurrentChannel

    def initGUI(self) -> None:
        super().initGUI()
        self.addAction(event=self.resetCharge, toolTip=f'Reset accumulated charge for {self.name}.', icon='battery-empty.png')

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings[f'{self.name}/Interval'][Parameter.VALUE] = 100  # overwrite default value
        return defaultSettings

    def resetCharge(self) -> None:
        """Reset the charge of each channel."""
        for channel in self.channels:
            channel.resetCharge()


class CurrentChannel(Channel):
    """UI for picoammeter with integrated functionality."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.controller = CurrentController(controllerParent=self)
        self.preciseCharge = 0  # store independent of spin box precision to avoid rounding errors

    CHARGE = 'Charge'
    ADDRESS = 'Address'
    VOLTAGE = 'Voltage'
    channelParent: KEITHLEY
    controller: 'CurrentController'

    def getDefaultChannel(self) -> dict[str, dict]:

        # definitions for type hinting
        self.charge: float
        self.address: str
        self.voltage: float

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'I (pA)'
        channel[self.VALUE][Parameter.UNIT] = 'pA'
        channel[self.BACKGROUND][Parameter.HEADER] = 'BG (pA)'
        channel[self.BACKGROUND][Parameter.UNIT] = 'pA'
        channel[self.CHARGE] = parameterDict(value=0, parameterType=PARAMETERTYPE.FLOAT, advanced=False, header='C (pAh)', unit='pAh', indicator=True, attr='charge')
        channel[self.ADDRESS] = parameterDict(value='GPIB0::22::INSTR', parameterType=PARAMETERTYPE.TEXT, advanced=True, attr='address')
        channel[self.VOLTAGE] = parameterDict(value=0, parameterType=PARAMETERTYPE.FLOAT, advanced=False, attr='voltage', recorded=True, header='Voltage (V)', unit='V',
                                              minimum=-50, maximum=50, event=lambda: self.controller.applyVoltage())  # noqa: PLW0108 lambda is used to defer evaluation until defined
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.insertDisplayedParameter(self.CHARGE, before=self.DISPLAY)
        self.insertDisplayedParameter(self.VOLTAGE, before=self.DISPLAY)
        self.displayedParameters.append(self.ADDRESS)

    def tempParameters(self) -> list[str]:
        return [*super().tempParameters(), self.CHARGE]

    def enabledChanged(self) -> None:
        super().enabledChanged()
        if self.channelParent.liveDisplayActive() and self.channelParent.recording:
            if self.enabled:
                self.controller.initializeCommunication()
            elif self.controller.acquiring:
                self.controller.stopAcquisition()

    def appendValue(self, lenT, nan=False) -> None:
        super().appendValue(lenT, nan=nan)
        if not nan and not np.isnan(self.value) and not np.isinf(self.value):
            chargeIncrement = (self.value - self.background) * self.channelParent.interval / 1000 / 3600 if self.values.size > 1 else 0
            self.preciseCharge += chargeIncrement  # display accumulated charge  # don't use np.sum(self.charges) to allow
            self.charge = self.preciseCharge  # pylint: disable=[attribute-defined-outside-init]  # attribute defined dynamically

    def clearHistory(self) -> None:
        super().clearHistory()
        self.resetCharge()

    def resetCharge(self) -> None:
        """Reset the charge."""
        self.charge = 0  # pylint: disable=[attribute-defined-outside-init]  # attribute defined dynamically
        self.preciseCharge = 0

    def realChanged(self) -> None:
        self.getParameterByName(self.ADDRESS).setVisible(self.real)
        super().realChanged()


class CurrentController(DeviceController):
    """Implements visa communication with KEITHLEY 6487."""

    port: 'pyvisa.resources.gpib.GPIBInstrument | None'
    controllerParent: CurrentChannel

    def __init__(self, controllerParent) -> None:
        super().__init__(controllerParent=controllerParent)
        self.port = None
        self.phase = self.rng.random() * 10  # used in test mode
        self.omega = self.rng.random()  # used in test mode
        self.offset = self.rng.random() * 10  # used in test mode

    def initializeCommunication(self) -> None:
        if self.controllerParent.enabled and self.controllerParent.active and self.controllerParent.real:
            super().initializeCommunication()
        else:
            self.stopAcquisition()

    def runInitialization(self) -> None:
        try:
            # use rm.list_resources() to check for available resources
            self.rm = pyvisa.ResourceManager()
            self.port = self.rm.open_resource(self.controllerParent.address)  # type: ignore  # noqa: PGH003
            self.KeithleyWrite('*RST')
            self.controllerParent.print(self.KeithleyQuery('*IDN?'))
            self.KeithleyWrite('SYST:ZCH OFF')
            self.KeithleyWrite('CURR:NPLC 6')
            self.KeithleyWrite('SOUR:VOLT:RANG 50')
            self.signalComm.initCompleteSignal.emit()
        except Exception:  # noqa: BLE001
            self.signalComm.updateValuesSignal.emit()
        finally:
            self.initializing = False

    def startAcquisition(self) -> None:
        if self.controllerParent.active:
            super().startAcquisition()

    def readNumbers(self) -> None:
        if not self.controllerParent.pluginManager.closing and self.controllerParent.enabled and self.controllerParent.active and self.controllerParent.real:
            try:
                self.KeithleyWrite('INIT')
                self.values = np.array([float(self.KeithleyQuery('FETCh?').split(',')[0][:-1]) * 1E12])
            except (pyvisa.errors.VisaIOError, pyvisa.errors.InvalidSession, AttributeError) as e:
                self.print(f'Error while reading current {e}', flag=PRINT.ERROR)
                self.errorCount += 1
                self.values[0] = np.nan

    def fakeNumbers(self) -> None:
        if not self.controllerParent.pluginManager.closing and self.controllerParent.enabled and self.controllerParent.active and self.controllerParent.real:
            self.values[0] = np.sin(self.omega * time.time() / 5 + self.phase) * 10 + self.rng.random() + self.offset

    def toggleOn(self) -> None:
        super().toggleOn()
        self.applyVoltage()  # apply voltages before turning power supply on or off
        self.KeithleyWrite(f"SOUR:VOLT:STAT {'ON' if self.controllerParent.channelParent.isOn() else 'OFF'}")

    def applyVoltage(self) -> None:
        # NOTE this is different from the general applyValue function as this is not setting the channel value but an additional custom channel parameter
        """Apply voltage value."""
        if self.port:
            self.KeithleyWrite(f'SOUR:VOLT {self.controllerParent.voltage}')

    def closeCommunication(self) -> None:
        super().closeCommunication()
        if self.port:
            with self.lock.acquire_timeout(1, timeoutMessage='Could not acquire lock before closing port.'):
                self.port.close()
                self.port = None
        self.initialized = False
        self.closing = False

    def KeithleyWrite(self, message: str) -> None:
        """KEITHLEY specific pyvisa write.

        :param message: The message to be send.
        :type message: str
        """
        if self.port:
            self.print('KeithleyWrite message: ' + message.replace('\r', '').replace('\n', ''), flag=PRINT.TRACE)
            self.port.write(message)

    def KeithleyQuery(self, query: str) -> str:
        """KEITHLEY specific pyvisa query.

        :param query: The query to be queried.
        :type query: str
        """
        if self.port:
            response = self.port.query(query)
            self.print('KeithleyQuery query: ' + query.replace('\r', '').replace('\n', '') +
                    ', response: ' + response.replace('\r', '').replace('\n', ''), flag=PRINT.TRACE)
            return response
        return ''
