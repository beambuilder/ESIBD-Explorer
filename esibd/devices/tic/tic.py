# pylint: disable=[missing-module-docstring]  # see class docstrings
import re
from typing import TYPE_CHECKING, cast

import numpy as np
import serial

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, parameterDict
from esibd.plugins import Device

if TYPE_CHECKING:
    from esibd.plugins import Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [TIC]


class TIC(Device):
    """Read pressure values form an Edwards TIC."""

    name = 'TIC'
    iconFile = 'edwards_tic.png'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.OUTPUTDEVICE
    unit = 'mbar'
    logY = True
    channels: 'list[PressureChannel]'
    controller: 'TICPressureController'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = PressureChannel
        self.controller = TICPressureController(controllerParent=self)

    def getChannels(self) -> 'list[PressureChannel]':
        return cast('list[PressureChannel]', super().getChannels())

    com: str

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings[f'{self.name}/Interval'][Parameter.VALUE] = 500  # overwrite default value
        defaultSettings[f'{self.name}/COM'] = parameterDict(value='COM1', toolTip='COM port.', items=','.join([f'COM{x}' for x in range(1, 25)]),
                                          parameterType=PARAMETERTYPE.COMBO, attr='com')
        defaultSettings[f'{self.name}/{self.MAXDATAPOINTS}'][Parameter.VALUE] = 1E6  # overwrite default value
        return defaultSettings


class PressureChannel(Channel):
    """UI for pressure with integrated functionality."""

    ID = 'ID'
    channelParent: TIC

    def getDefaultChannel(self) -> dict[str, dict]:

        # definitions for type hinting
        self.id: int

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'P (mbar)'
        channel[self.VALUE][Parameter.UNIT] = 'mbar'
        channel[self.ID] = parameterDict(value=1, parameterType=PARAMETERTYPE.INTCOMBO, advanced=True,
                                        items='0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16', attr='id')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.displayedParameters.append(self.ID)


class TICPressureController(DeviceController):

    TICgaugeID = (913, 914, 915, 934, 935, 936)
    controllerParent: TIC

    def runInitialization(self) -> None:
        try:
            self.port = serial.Serial(
                f'{self.controllerParent.com}', baudrate=9600, bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, xonxoff=True, timeout=2)
            TICStatus = self.TICWriteRead(message=902)
            self.print(f'Status: {TICStatus}')  # query status
        except Exception as e:  # pylint: disable=[broad-except]  # noqa: BLE001
            self.closeCommunication()
            self.print(f'Error while initializing: {e}', flag=PRINT.ERROR)
        else:
            if not TICStatus:
                msg = 'TIC did not return status.'
                raise ValueError(msg)
            self.signalComm.initCompleteSignal.emit()
        finally:
            self.initializing = False

    def readNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.active and channel.real:
                if self.initialized:
                    msg = self.TICWriteRead(message=f"{self.TICgaugeID[cast('PressureChannel', channel).id]}", already_acquired=True)
                    try:
                        self.values[i] = float(re.split(r' |;', msg)[1]) / 100  # parse and convert to mbar = 0.01 Pa
                    except Exception as e:  # noqa: BLE001
                        self.print(f'Failed to parse pressure from {msg}: {e}', flag=PRINT.ERROR)
                        self.errorCount += 1
                        self.values[i] = np.nan
                else:
                    self.values[i] = np.nan

    def TICWriteRead(self, message, already_acquired=False) -> str:
        """TIC specific serial write and read.

        :param message: The serial message to be send.
        :type message: str
        :param already_acquired: Indicates if the lock has already been acquired, defaults to False
        :type already_acquired: bool, optional
        :return: The serial response received.
        :rtype: str
        """
        response = ''
        with self.lock.acquire_timeout(2, timeoutMessage=f'Cannot acquire lock for message: {message}', already_acquired=already_acquired) as lock_acquired:
            if lock_acquired and self.port:
                self.serialWrite(self.port, f'?V{message}\r')
                # Note: unlike most other devices TIC terminates messages with \r and not \r\n
                response = self.serialRead(self.port, EOL='\r')  # reads return value
        return response

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
            with self.lock.acquire_timeout(1, timeoutMessage='Could not acquire lock before closing port.'):
                self.port.close()
                self.port = None
        self.initialized = False
        self.closing = False
