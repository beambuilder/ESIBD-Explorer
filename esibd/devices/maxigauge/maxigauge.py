# pylint: disable=[missing-module-docstring]  # see class docstrings
from typing import cast

import numpy as np
import serial

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, parameterDict
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [MAXIGAUGE]


class MAXIGAUGE(Device):
    """Reads pressure values form a Pfeiffer MaxiGauge."""

    name = 'MAXIGAUGE'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.OUTPUTDEVICE
    unit = 'mbar'
    iconFile = 'pfeiffer_maxi.png'
    logY = True
    channels: 'list[PressureChannel]'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = PressureChannel
        self.controller = PressureController(controllerParent=self)

    def getChannels(self) -> 'list[PressureChannel]':
        return cast('list[PressureChannel]', super().getChannels())

    COM: str

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings[f'{self.name}/Interval'][Parameter.VALUE] = 500  # overwrite default value
        defaultSettings[f'{self.name}/COM'] = parameterDict(value='COM1', toolTip='COM port.', items=','.join([f'COM{x}' for x in range(1, 25)]),
                                          parameterType=PARAMETERTYPE.COMBO, attr='COM')
        defaultSettings[f'{self.name}/{self.MAXDATAPOINTS}'][Parameter.VALUE] = 1E6  # overwrite default value
        return defaultSettings


class PressureChannel(Channel):
    """UI for pressure with integrated functionality."""

    ID = 'ID'
    channelParent: MAXIGAUGE

    def getDefaultChannel(self) -> dict[str, dict]:

        # definitions for type hinting
        self.id: int

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'P (mbar)'
        channel[self.VALUE][Parameter.UNIT] = 'mbar'
        channel[self.ID] = parameterDict(value=1, parameterType=PARAMETERTYPE.INTCOMBO, advanced=True,
                                        items='0, 1, 2, 3, 4, 5, 6', attr='id')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.displayedParameters.append(self.ID)


class PressureController(DeviceController):

    controllerParent: MAXIGAUGE
    PRESSURE_READING_STATUS = {  # noqa: RUF012
      0: 'Measurement data okay',
      1: 'Underrange',
      2: 'Overrange',
      3: 'Sensor error',
      4: 'Sensor off',
      5: 'No sensor',
      6: 'Identification error',
    }

    def runInitialization(self) -> None:
        try:
            self.port = serial.Serial(f'{self.controllerParent.COM}', baudrate=9600, bytesize=serial.EIGHTBITS,
                                    parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, xonxoff=False, timeout=2)
            TPGStatus = self.TPGWriteRead(message='TID')
            self.print(f'MaxiGauge Status: {TPGStatus}')  # gauge identification
        except Exception as e:  # pylint: disable=[broad-except]  # noqa: BLE001
            self.print(f'TPG Error while initializing: {e}', flag=PRINT.ERROR)
        else:
            if not TPGStatus:
                msg = 'TPG did not return status.'
                raise ValueError(msg)
            self.signalComm.initCompleteSignal.emit()
        finally:
            self.initializing = False

    def readNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.active and channel.real:
                if self.initialized:
                    msg = self.TPGWriteRead(message=f'PR{channel.id}', already_acquired=True)
                    try:
                        status, pressure = msg.split(',')
                        if status == '0':
                            self.values[i] = float(pressure)  # set unit to mbar on device
                        else:
                            self.print(f'Could not read pressure for {channel.name}: {self.PRESSURE_READING_STATUS[int(status)]}.', flag=PRINT.DEBUG)
                            self.values[i] = np.nan
                    except Exception as e:  # noqa: BLE001
                        self.print(f'Failed to parse pressure from {msg}: {e}', flag=PRINT.ERROR)
                        self.errorCount += 1
                        self.values[i] = np.nan
                else:
                    self.values[i] = np.nan

    def rndPressure(self) -> float:
        """Return a random pressure."""
        exp = float(self.rng.integers(-11, 3))
        significand = 0.9 * self.rng.random() + 0.1
        return significand * 10**exp

    def fakeNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.active and channel.real:
                self.values[i] = self.rndPressure() if np.isnan(self.values[i]) else self.values[i] * self.rng.uniform(.99, 1.01)  # allow for small fluctuation

    def closeCommunication(self) -> None:
        super().closeCommunication()
        if self.port:
            with self.lock.acquire_timeout(1, timeoutMessage='Could not acquire lock before closing port.') as lock_acquired:
                if lock_acquired:
                    self.port.close()
                    self.port = None
        self.initialized = False
        self.closing = False

    def TPGWrite(self, message: str) -> None:
        """TPG specific serial write.

        :param message: The serial message to be send.
        :type message: str
        """
        if self.port:
            self.serialWrite(self.port, f'{message}\r', encoding='ascii')
            self.serialRead(self.port, encoding='ascii')  # read acknowledgment

    def TPGRead(self) -> str:
        """TPG specific serial read.

        :return: The serial response received.
        :rtype: str
        """
        if self.port:
            self.serialWrite(self.port, '\x05\r', encoding='ascii')  # Enquiry prompts sending return from previously send mnemonic
            enq = self.serialRead(self.port, encoding='ascii')  # response
            self.serialRead(self.port, encoding='ascii')  # followed by NAK
            return enq
        return ''

    def TPGWriteRead(self, message: str, already_acquired: bool = False) -> str:
        """TPG specific serial write and read.

        :param message: The serial message to be send.
        :type message: str
        :param already_acquired: Indicates if the lock has already been acquired, defaults to False
        :type already_acquired: bool, optional
        :return: The serial response received.
        :rtype: str
        """
        response = ''
        with self.lock.acquire_timeout(2, timeoutMessage=f'Cannot acquire lock for message: {message}', already_acquired=already_acquired) as lock_acquired:
            if lock_acquired:
                self.TPGWrite(message)
                response = self.TPGRead()  # reads return value
        return response
