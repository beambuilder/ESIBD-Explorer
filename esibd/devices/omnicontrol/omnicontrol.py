# pylint: disable=[missing-module-docstring]  # see class docstrings
from enum import Enum
from typing import cast

import numpy as np
import serial
from PyQt6.QtWidgets import QPushButton

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, ContextAction, DeviceController, Icon, Parameter, ToolButton, getTestMode, parameterDict
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [OMNICONTROL]


class OMNICONTROL(Device):
    """Reads pressure values and controls turbo pumps from Pfeiffer.

    Can be used with and without Omnicontrol hardware.
    Frequently used pump parameters can be controlled through the GUI.
    Access to less frequently uses pump parameters is provided by the controller and convenience methods of the channel (e.g. acknError, setRS485Adr, setStdbySVal, getOpHrsPump).
    These can also be accessed directly through the context menu of the corresponding parameters.
    Additional parameters can be added following the same pattern if needed.
    """

    name = 'OMNICONTROL'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.OUTPUTDEVICE
    unit = 'mbar'
    iconFile = 'pfeiffer_omni.png'
    logY = True
    channels: 'list[OmniChannel]'
    controller: 'OmniController'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = OmniChannel
        self.controller = OmniController(controllerParent=self)

    def getChannels(self) -> 'list[OmniChannel]':
        return cast('list[OmniChannel]', super().getChannels())

    com: str

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings[f'{self.name}/Interval'][Parameter.VALUE] = 500  # overwrite default value
        defaultSettings[f'{self.name}/COM'] = parameterDict(value='COM1', toolTip='COM port.', items=','.join([f'COM{x}' for x in range(1, 25)]),
                                          parameterType=PARAMETERTYPE.COMBO, attr='com')
        defaultSettings[f'{self.name}/{self.MAXDATAPOINTS}'][Parameter.VALUE] = 1E6  # overwrite default value
        return defaultSettings


class OmniChannel(Channel):  # noqa: PLR0904
    """UI for pressure with integrated functionality."""

    channelParent: OMNICONTROL
    ID = 'ID'
    OMNITYPE = 'OMNITYPE'
    OMNIICON = 'OMNIICON'
    PumpStatn = 'PumpStatn'
    Standby = 'Standby'
    DrvPower = 'DrvPower'
    TempPump = 'TempPump'
    ERRORLED = 'ERRORLED'

    class OMNITYPESTATE(Enum):
        """States for the Stack Action."""

        PRESSURE = 'Pressure Sensor'
        TMP = 'Turbo Pump'

    def initGUI(self, item: dict) -> None:
        super().initGUI(item)
        omniIcon = self.getParameterByName(self.OMNIICON)
        omniIcon.widget = QPushButton()
        omniIcon.widget.setStyleSheet('QPushButton{border:none;}')
        omniIcon.applyWidget()

        pumpStatn = self.getParameterByName(self.PumpStatn)
        initialValue = pumpStatn.value or False
        pumpStatn.widget = ToolButton()  # hard to spot checked QCheckBox. QPushButton is too wide -> overwrite internal widget to QToolButton
        pumpStatn.applyWidget()
        pumpStatn.widget.setMaximumHeight(pumpStatn.rowHeight)  # default too high
        pumpStatn.widget.setText('ON')
        pumpStatn.widget.setMinimumWidth(5)
        pumpStatn.widget.setCheckable(True)
        pumpStatn.value = initialValue

        standby = self.getParameterByName(self.Standby)
        initialValue = standby.value or False
        standby.widget = ToolButton()  # hard to spot checked QCheckBox. QPushButton is too wide -> overwrite internal widget to QToolButton
        standby.applyWidget()
        standby.widget.setMaximumHeight(standby.rowHeight)  # default too high
        standby.widget.setText(self.Standby.title())
        standby.widget.setMinimumWidth(5)
        standby.widget.setCheckable(True)
        standby.value = initialValue

        self.realChanged()
        self.omniTypeChanged()

    def getDefaultChannel(self) -> dict[str, dict]:

        # definitions for type hinting
        self.id: int
        self.omniType: str
        self.pumpStatn: bool
        self.standby: bool
        self.drvPower: float
        self.tempPump: float
        self.notes: str
        self.errorLED: bool

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'P (mbar) / S (Hz)'
        channel[self.ID] = parameterDict(value=1, parameterType=PARAMETERTYPE.INTCOMBO, advanced=True,
                                        items='0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16', attr='id')
        channel[self.OMNIICON] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, advanced=False,
                                                 toolTip='Device type.', header=' ')
        channel[self.OMNITYPE] = parameterDict(value=self.OMNITYPESTATE.PRESSURE.value, parameterType=PARAMETERTYPE.COMBO, advanced=True,
                                               toolTip='Select which type of device this channel is controlling.',
                                        items=f'{self.OMNITYPESTATE.PRESSURE.value}, {self.OMNITYPESTATE.TMP.value}', attr='omniType', header='Type', event=self.omniTypeChanged)
        channel[self.PumpStatn] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, advanced=True,
                                        attr='pumpStatn', restore=False, event=self.setPumpStatn, header='State')
        channel[self.Standby] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, advanced=True,
                                        attr='standby', restore=False, event=self.setStandby)
        channel[self.DrvPower] = parameterDict(value=np.nan, parameterType=PARAMETERTYPE.FLOAT, advanced=True, indicator=True,
                                        attr='drvPower', restore=False, header='Power (W)', recorded=True, unit='W')
        channel[self.TempPump] = parameterDict(value=np.nan, parameterType=PARAMETERTYPE.FLOAT, advanced=True, indicator=True,
                                        attr='tempPump', restore=False, header='Temp (°C)', recorded=True, unit='°C')
        channel[self.ERRORLED] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, advanced=True, indicator=True,
                                        header='Err', toolTip='Indicates errors.', attr='errorLED', restore=False)
        channel[self.NOTES] = parameterDict(value='', parameterType=PARAMETERTYPE.LABEL, advanced=True, attr='notes', restore=False, indicator=True)
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.insertDisplayedParameter(self.OMNIICON, before=self.ENABLED)
        self.insertDisplayedParameter(self.OMNITYPE, before=self.ENABLED)
        self.insertDisplayedParameter(self.PumpStatn, before=self.VALUE)
        self.insertDisplayedParameter(self.Standby, before=self.VALUE)
        self.insertDisplayedParameter(self.DrvPower, before=self.EQUATION)
        self.insertDisplayedParameter(self.TempPump, before=self.EQUATION)
        self.insertDisplayedParameter(self.ERRORLED, before=self.EQUATION)
        self.displayedParameters.append(self.ID)
        self.displayedParameters.append(self.NOTES)

    def tempParameters(self) -> list[str]:
        return [*super().tempParameters(), self.OMNIICON]

    def realChanged(self) -> None:
        super().realChanged()
        self.hideParameters()

    def hideParameters(self) -> None:
        """Hide parameters based in channel type and presence of hardware."""
        self.getParameterByName(self.PumpStatn).setVisible(self.isPump)
        self.getParameterByName(self.Standby).setVisible(self.real and self.isPump)
        self.getParameterByName(self.DrvPower).setVisible(self.real and self.isPump)
        self.getParameterByName(self.TempPump).setVisible(self.real and self.isPump)

    def omniTypeChanged(self) -> None:
        """Update GUI chen changing between sensor and pump channels."""
        self.hideParameters()
        omniIconPushButton = cast('QPushButton', self.getParameterByName(self.OMNIICON).getWidget())
        omniIconPushButton.setIcon(self.getIcon())
        self.getParameterByName(self.OMNIICON).toolTip = self.omniType
        oldValue = self.value
        value = self.getParameterByName(self.VALUE)
        value.parameterType = PARAMETERTYPE.FLOAT if self.isPump else PARAMETERTYPE.EXP
        value.unit = self.unit
        value.displayDecimals = 0  # Note: integers cannot be represented as nan, thus using float with 0 decimals
        value.applyWidget()
        self.scalingChanged()
        self.updateColor()
        self.value = oldValue
        self.logY = not self.isPump
        self.getParameterByName(self.PumpStatn).extraContextActions = ([ContextAction(text='Get operating hours via Console.', event=self.getOpHrsPumpConsole)] if self.isPump
                                                                       else [])
        self.getParameterByName(self.ERRORLED).extraContextActions = [ContextAction(text='Acknowledge Error', event=self.acknError)] if self.isPump else []
        self.getParameterByName(self.ID).extraContextActions = [ContextAction(text='Set address via Console', event=self.setRS485AdrConsole)] if self.isPump else []
        self.getParameterByName(self.Standby).extraContextActions = [ContextAction(text='Set Standby Speed via Console', event=self.setStdbySValConsole)] if self.isPump else []

        display = self.getParameterByName(self.DISPLAY)
        display.extraContextActions = []
        if self.isPump:
            for parameter in self.getRecordedParameters():
                display.extraContextActions.append(ContextAction(text=f'Toggle display of {parameter.name}', event=parameter.updateDisplay))

    def getIcon(self, desaturate: bool = False) -> Icon:  # pylint: disable = missing-param-doc
        """Return Icon depending on the channel type."""
        return self.channelParent.makeIcon(file='turbo.png' if self.isPump else 'sensor.png', desaturate=desaturate)

    @property
    def unit(self) -> str:
        """The unit depending on device type."""
        return 'Hz' if self.isPump else 'mbar'

    @property
    def isPump(self) -> bool:
        """Check if this channel represents a pump or a Sensor."""
        return self.omniType == self.OMNITYPESTATE.TMP.value

    def checkPumpChannel(self) -> bool:
        """Check and notify if this is not a Pump."""
        if not self.isPump:
            self.print(f'{self.name} is not a {self.OMNITYPESTATE.TMP.value}.')
        return self.isPump

    def checkSensorChannel(self) -> bool:
        """Check and notify if this is not a Sensor."""
        if self.isPump:
            self.print(f'{self.name} is not a {self.OMNITYPESTATE.PRESSURE.value}.')
        return not self.isPump

    def getPressure(self) -> None:
        """Get pressure from gauge in mbar."""
        if not self.checkSensorChannel():
            return
        self.channelParent.controller.getPressure(addr=self.id)

    def setPumpStatn(self) -> None:
        """Set the pump station state of the turbo pump, i.e. ON or OFF."""
        if not self.checkPumpChannel():
            return
        self.channelParent.controller.setPumpStatn(addr=self.id, on=self.pumpStatn)

    def setStandby(self) -> None:
        """Set the standby state of the turbo pump."""
        if not self.checkPumpChannel():
            return
        self.channelParent.controller.setStandby(addr=self.id, on=self.standby)

    def getStandby(self) -> None:
        """Get the standby state of the turbo pump."""
        if not self.checkPumpChannel():
            return
        self.channelParent.controller.getStandby(addr=self.id)

    def getStdbySVal(self) -> float:
        """Get the standby speed value of the turbo pump."""
        if not self.checkPumpChannel():
            return np.nan
        return self.channelParent.controller.getStdbySVal(addr=self.id)

    def getOpHrsPump(self) -> float:
        """Get the number of operating hours of the turbo pump."""
        if not self.checkPumpChannel():
            return np.nan
        return self.channelParent.controller.getOpHrsPump(addr=self.id)

    def getOpHrsPumpConsole(self) -> None:
        """Allow user to set Standby Speed via console."""
        self.pluginManager.Console.addToNamespace('channel', self)
        self.pluginManager.Console.execute(command='channel')
        self.pluginManager.Console.mainConsole.input.setText('channel.getOpHrsPump()')

    def setStdbySVal(self, value: float) -> None:
        """Set the standby speed value of the turbo pump.

        :param value: New standby speed. Between 20 and 80 %.
        :type value: float
        """
        if not self.checkPumpChannel():
            return
        self.channelParent.controller.setStdbySVal(addr=self.id, value=value)

    def setStdbySValConsole(self) -> None:
        """Allow user to set Standby Speed via console."""
        self.pluginManager.Console.addToNamespace('channel', self)
        self.pluginManager.Console.execute(command='channel')
        self.pluginManager.Console.mainConsole.input.setText('channel.setStdbySVal(value=-->newValue<--)  # Enter value between 20 and 80 %.')

    def acknError(self) -> None:
        """Acknowledge Error."""
        if not self.checkPumpChannel():
            return
        self.channelParent.controller.acknError(addr=self.id)

    def setRS485Adr(self, newAddr: int) -> None:
        """Set the RS485 address of the device.

        :param newAddr: New address. Needs to be unique when using multiple pumps.
        :type newAddr: int
        """
        if not self.checkPumpChannel():
            return
        response = self.channelParent.controller.setRS485Adr(addr=self.id, newAddr=newAddr)
        if response == newAddr:
            self.id = newAddr

    def setRS485AdrConsole(self) -> None:
        """Allow user to set RS485 Address via console."""
        self.pluginManager.Console.addToNamespace('channel', self)
        self.pluginManager.Console.execute(command='channel')
        self.pluginManager.Console.mainConsole.input.setText('channel.setRS485Adr(newAddr=-->newAddr<--)  # Enter new address.')


class OmniController(DeviceController):  # noqa: PLR0904

    controllerParent: OMNICONTROL

    def runInitialization(self) -> None:
        try:
            self.port = serial.Serial(self.controllerParent.com, timeout=1)
            self.signalComm.initCompleteSignal.emit()
        except Exception as e:  # pylint: disable=[broad-except]  # noqa: BLE001
            self.print(f'Error while initializing: {e}', flag=PRINT.ERROR)
        finally:
            self.initializing = False

    def initializeValues(self, reset: bool = False) -> None:
        super().initializeValues(reset)
        self.pumpStatn = [False] * len(self.controllerParent.getChannels())  # initializing values, overwrite if needed
        self.standby = [False] * len(self.controllerParent.getChannels())  # initializing values, overwrite if needed
        self.drvPower = np.array([np.nan] * len(self.controllerParent.getChannels()))  # initializing values, overwrite if needed
        self.tempPump = np.array([np.nan] * len(self.controllerParent.getChannels()))  # initializing values, overwrite if needed
        self.errorCode = [''] * len(self.controllerParent.getChannels())  # initializing values, overwrite if needed

    def readNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.active and channel.real:
                try:
                    if channel.isPump:
                        speed = self.getActualSpd(addr=channel.id, already_acquired=True)
                        self.pumpStatn[i] = self.getPumpStatn(addr=channel.id, already_acquired=True)
                        self.standby[i] = self.getStandby(addr=channel.id, already_acquired=True)
                        self.drvPower[i] = self.getDrvPower(addr=channel.id, already_acquired=True)
                        self.tempPump[i] = self.getTempPump(addr=channel.id, already_acquired=True)
                        self.errorCode[i] = self.getErrorCode(addr=channel.id, already_acquired=True)
                        self.values[i] = speed
                    else:
                        pressure = self.getPressure(addr=channel.id, already_acquired=True)
                        self.print(f'readNumbers channel.id: {channel.id}, response {pressure}', flag=PRINT.TRACE)
                        self.values[i] = np.nan if pressure == 0 else pressure
                except ValueError as e:
                    self.print(f"""Error while reading {'pump speed or other parameter' if channel.isPump else 'sensor pressure'}"""
                               f""" from channel {channel.name} at address {channel.id}.\nMake sure you are using the correct channel address and device type."""
                               f""" Error message: {e}""", flag=PRINT.WARNING)
                    self.errorCount += 1
                    self.values[i] = np.nan
                    self.drvPower[i] = np.nan
                    self.tempPump[i] = np.nan

    def rndPressure(self) -> float:
        """Return a random pressure."""
        exp = float(self.rng.integers(-11, 3))
        significand = 0.9 * self.rng.random() + 0.1
        return significand * 10**exp

    def fakeNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.active and channel.real:
                if channel.isPump:
                    self.values[i] = int(0 if np.isnan(self.values[i]) else  # simulate acceleration and deceleration and allow for small fluctuation
                    max(0, min(1500, (self.values[i] + (25 if channel.pumpStatn and (channel.value < (1000 if channel.standby else 1500)) else - 25) *
                                      self.controllerParent.interval / 1000))) + self.rng.uniform(-5, 5))
                    self.pumpStatn[i] = channel.pumpStatn
                    self.standby[i] = channel.standby
                    self.drvPower[i] = self.values[i] / 30 * self.rng.uniform(.99, 1.01)
                    self.tempPump[i] = 25 + self.values[i] / 60 * self.rng.uniform(.99, 1.01)
                    self.errorCode[i] = '000000'
                else:
                    self.values[i] = self.rndPressure() if np.isnan(self.values[i]) else self.values[i] * self.rng.uniform(.99, 1.01)  # allow for small fluctuation

    def updateValues(self) -> None:
        super().updateValues()
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real and channel.isPump:
                channel.pumpStatn = self.pumpStatn[i]
                channel.standby = self.standby[i]
                channel.drvPower = self.drvPower[i]
                channel.tempPump = self.tempPump[i]
                if self.errorCode[i] == '000000':
                    channel.notes = 'No Error'
                    channel.errorLED = False
                else:
                    channel.notes = f'{self.errorCode[i]} See controller manual for error codes.'
                    channel.errorLED = True

    def closeCommunication(self) -> None:
        super().closeCommunication()
        if self.port:
            with self.lock.acquire_timeout(1, timeoutMessage='Could not acquire lock before closing port.'):
                self.port.close()
                self.port = None
        self.initialized = False
        self.closing = False

    def getPressure(self, addr: int, already_acquired=False) -> float:  # pylint: disable = missing-param-doc
        """Get pressure from gauge in mbar.

        :param addr: Device address.
        :type addr: int
        :param already_acquired: Indicate if a lock has already been acquired, True inside readNumbers, defaults to False
        :type already_acquired: bool, optional
        :return: Pressure reading in mbar.
        :rtype: float
        """
        response = self.OmniWriteRead(addr=addr, param_num=740, already_acquired=already_acquired)
        mantissa = int(response[:4])
        exponent = int(response[4:])
        return float(mantissa * 10 ** (exponent - 26)) * 1000

    def getActualSpd(self, addr: int, already_acquired=False) -> float:  # pylint: disable = missing-param-doc
        """Get the actual speed of the turbo pump."""
        response = self.OmniWriteRead(addr=addr, param_num=309, already_acquired=already_acquired)
        if response:
            return int(response)
        self.print(f'Got empty response while reading ActualSpd on address {addr}.', flag=PRINT.WARNING)
        return np.nan

    def getDrvPower(self, addr: int, already_acquired=False) -> float:  # pylint: disable = missing-param-doc
        """Get the drive power of the turbo pump."""
        response = self.OmniWriteRead(addr=addr, param_num=316, already_acquired=already_acquired)
        if response:
            return int(response)
        self.print(f'Got empty response while reading DrvPower on address {addr}.', flag=PRINT.WARNING)
        return np.nan

    def getPumpStatn(self, addr: int, already_acquired=False) -> bool:  # pylint: disable = missing-param-doc
        """Get the pump station state of the turbo pump, i.e. ON or OFF."""
        return self.OmniWriteRead(addr=addr, param_num=10, already_acquired=already_acquired) == '111111'

    def setPumpStatn(self, addr: int, on: bool, already_acquired=False) -> bool:  # pylint: disable = missing-param-doc
        """Set the pump station state of the turbo pump, i.e. ON or OFF.

        :param on: New pump station state.
        :type on: bool
        :return: New pump station state as confirmation.
        :rtype: bool
        """
        return self.OmniWriteRead(addr=addr, param_num=10, value=111111 if on else 0, already_acquired=already_acquired) == '111111'

    def getTempPump(self, addr: int, already_acquired=False) -> float:  # 384 TempRotor, 346 TempMotor, 330 TempPmpBot  # pylint: disable = missing-param-doc
        """Get the temperature of the turbo pump."""
        response = self.OmniWriteRead(addr=addr, param_num=330, already_acquired=already_acquired)
        if response:
            return int(response)
        self.print(f'Got empty response while reading TempPump on address {addr}.', flag=PRINT.WARNING)
        return np.nan

    def getStandby(self, addr: int, already_acquired=False) -> bool:  # pylint: disable = missing-param-doc
        """Get the standby state of the turbo pump."""
        return self.OmniWriteRead(addr=addr, param_num=2, already_acquired=already_acquired) == '111111'

    def setStandby(self, addr: int, on: bool, already_acquired=False) -> bool:  # pylint: disable = missing-param-doc
        """Set the standby state of the turbo pump.

        :param on: New standby state.
        :type on: bool
        :return: New standby state as confirmation.
        :rtype: bool
        """
        return self.OmniWriteRead(addr=addr, param_num=2, value=111111 if on else 0, already_acquired=already_acquired) == '111111'

    def getErrorCode(self, addr: int, already_acquired=False) -> str:  # pylint: disable = missing-param-doc
        """Get the error code of the turbo pump."""
        return self.OmniWriteRead(addr=addr, param_num=303, already_acquired=already_acquired)

    def getStdbySVal(self, addr: int, already_acquired=False) -> float:  # pylint: disable = missing-param-doc
        """Get the standby speed value of the turbo pump."""
        if getTestMode():
            return 66
        response = self.OmniWriteRead(addr=addr, param_num=717, already_acquired=already_acquired)
        if response:
            return float(response) / 100
        self.print(f'Got empty response while reading StdbySVal on address {addr}.', flag=PRINT.WARNING)
        return np.nan

    def setStdbySVal(self, addr: int, value: float, already_acquired=False) -> float:  # pylint: disable = missing-param-doc
        """Set the standby speed value of the turbo pump.

        :param addr: Device address
        :type addr: int
        :param value: New standby speed. Between 20 and 80 %.
        :type value: float
        :return: New speed as confirmation.
        :rtype: float
        """
        if value < 20 or value > 100:  # noqa: PLR2004
            self.print(f'Standby speed value {value} invalid. Value needs to be between 20 and 100.')
            return self.getStdbySVal(addr=addr)
        if getTestMode():
            return 66
        response = self.OmniWriteRead(addr=addr, param_num=717, value=value * 100, already_acquired=already_acquired)
        if response:
            return float(response) / 100
        self.print(f'Got empty response while setting StdbySVal on address {addr}.', flag=PRINT.WARNING)
        return np.nan

    def getOpHrsPump(self, addr: int, already_acquired=False) -> float:  # pylint: disable = missing-param-doc
        """Get the standby speed value of the turbo pump."""
        if getTestMode():
            return 0
        response = self.OmniWriteRead(addr=addr, param_num=311, already_acquired=already_acquired)
        if response:
            return int(response)
        self.print(f'Got empty response while reading OpHrsPump on address {addr}.', flag=PRINT.WARNING)
        return np.nan

    def setRS485Adr(self, addr: int, newAddr: int, already_acquired=False) -> float:  # pylint: disable = missing-param-doc
        """Set the RS485 address of the device.

        :param addr: Current address, usually 1 for turbo pumps.
        :type addr: int
        :param newAddr: New address. Needs to be unique when using multiple pumps.
        :type newAddr: int
        :return: New address for confirmation.
        :rtype: int
        """
        if getTestMode():
            return newAddr
        response = self.OmniWriteRead(addr=addr, param_num=797, value=newAddr, already_acquired=already_acquired)
        if response:
            return int(response)
        self.print(f'Got empty response while setting RS485Adr on address {addr}.', flag=PRINT.WARNING)
        return np.nan

    def acknError(self, addr: int, already_acquired=False) -> None:  # pylint: disable = missing-param-doc
        """Acknowledge Error."""
        self.OmniWriteRead(addr=addr, param_num=9, value=1, already_acquired=already_acquired)

    def makeOmniMessage(self, addr: int, param_num: int, value=None) -> str:
        """Generate a message to query or set the specified parameter.

        :param addr: the address of the device
        :type addr: int
        :param param_num: Parameter number
        :type param_num: int
        :param value: set value. If this is None, this returns a query, otherwise a set command.
        :type value: int
        :return: The query or set message.
        :rtype: str
        """
        message = ''
        if value is None:
            message = f'{addr:03d}00{param_num:03d}02=?'
            message += f'{sum(ord(x) for x in message) % 256:03d}\r'
        else:
            message = f'{addr:03d}10{param_num:03d}06{value:06d}'
            message += f'{sum(ord(x) for x in message) % 256:03d}\r'
        return message

    def parseOmniResponse(self, message: str) -> str:
        """Parse a message into its address, action, parameter number, and data.

        :param message: Message to be parsed.
        :type message: str
        :return: data
        :rtype: str
        """
        # Evaluate the checksum
        if int(message[-3:]) != (sum(ord(x) for x in message[:-3]) % 256):
            self.print(f'Invalid checksum in response {message}.', flag=PRINT.WARNING)

        addr = int(message[:3])
        action = int(message[3:4])
        param_num = int(message[5:8])
        data = message[10:-3]
        self.print(f'Parsed response {message}: addr {addr}, action {action}, param_num {param_num}, data {data}.', flag=PRINT.TRACE)
        return data

    def OmniWrite(self, message: str) -> None:
        """Pfeiffer specific serial write.

        :param message: The serial message to be send.
        :type message: str
        """
        if self.port:
            self.serialWrite(self.port, f'{message}\r', encoding='ascii')

    def OmniRead(self) -> str:
        """Pfeiffer specific serial read.

        :return: The serial response received.
        :rtype: str
        """
        if self.port:
            return self.serialRead(self.port, EOL='\r', encoding='ascii')  # response
        return ''

    def OmniWriteRead(self, addr: int, param_num: int, value=None, already_acquired: bool = False) -> str:
        """Pfeiffer specific serial write and read.

        :param addr: the address of the device
        :type addr: int
        :param param_num: Parameter number
        :type param_num: int
        :param value: set value. If this is None, this returns a query, otherwise a set command.
        :type value: int
        :param already_acquired: Indicates if the lock has already been acquired, defaults to False
        :type already_acquired: bool, optional
        :return: The serial response received.
        :rtype: str
        """
        response = ''
        message = self.makeOmniMessage(addr=addr, param_num=param_num, value=value)
        with self.lock.acquire_timeout(2, timeoutMessage=f'Cannot acquire lock for message: {message}', already_acquired=already_acquired) as lock_acquired:
            if lock_acquired:
                self.OmniWrite(message)
                response = self.OmniRead()  # reads return value
                if response:
                    response = self.parseOmniResponse(response)
        return response
