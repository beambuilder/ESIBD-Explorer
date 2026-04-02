# pylint: disable=[missing-module-docstring]  # see class docstrings
from typing import cast

import numpy as np

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, getTestMode, parameterDict
from esibd.devices.com_helper import getComPort
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [Chiller]


class Chiller(Device):
    """Lauda chiller temperature controller.

    Manages up to 3 Lauda chillers connected via separate COM ports.
    Supports temperature setpoint, monitor readback, pump level control,
    and On/Off logic for start/stop.
    """

    name = 'Chiller'
    version = '1.0'
    supportedVersion = '0.8'
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = '°C'
    iconFile = 'chiller.png'
    useMonitors = True
    useOnOffLogic = True
    channels: 'list[ChillerChannel]'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = ChillerChannel

    def initGUI(self) -> None:
        super().initGUI()
        self.controller = ChillerController(controllerParent=self)

    def getChannels(self) -> 'list[ChillerChannel]':
        return cast('list[ChillerChannel]', super().getChannels())

    def getDefaultSettings(self) -> dict[str, dict]:
        settings = super().getDefaultSettings()
        settings[f'{self.name}/Interval'][Parameter.VALUE] = 5000
        settings[f'{self.name}/{self.MAXDATAPOINTS}'][Parameter.VALUE] = 1E5
        return settings

    def getCOMs(self) -> list[int]:
        """Get list of unique COM port numbers used by real channels."""
        return list({channel.com for channel in self.channels if channel.real})

    def closeCommunication(self) -> None:
        self.setOn(False)
        self.controller.toggleOnFromThread(parallel=False)
        super().closeCommunication()


class ChillerChannel(Channel):
    """Channel for a single Lauda chiller unit."""

    COM = 'COM'
    PUMP = 'Pump Level'
    channelParent: Chiller

    def getDefaultChannel(self) -> dict[str, dict]:

        self.com: int
        self.pumpLevel: int

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'T (°C)'
        channel[self.COM] = parameterDict(value=getComPort('Chiller_A', default=23), minimum=1, maximum=99, parameterType=PARAMETERTYPE.INT, advanced=True,
                                          header='COM', toolTip='COM port number of the chiller.', attr='com')
        channel[self.PUMP] = parameterDict(value=4, minimum=1, maximum=6, parameterType=PARAMETERTYPE.INT, advanced=False,
                                           header='Pump', toolTip='Pump level (1-6).', attr='pumpLevel',
                                           instantUpdate=False, event=self.setPumpLevel)
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.insertDisplayedParameter(self.PUMP, before=self.MONITOR)
        self.displayedParameters.append(self.COM)

    def setPumpLevel(self) -> None:
        """Set the pump level on the chiller."""
        self.channelParent.controller.setPumpLevel(self)

    def monitorChanged(self) -> None:
        self.updateWarningState(self.enabled and self.channelParent.controller.acquiring
                                and self.channelParent.isOn()
                                and abs(self.monitor - self.value) > 5)

    def realChanged(self) -> None:
        self.getParameterByName(self.COM).setVisible(self.real)
        super().realChanged()


class ChillerController(DeviceController):
    """Controller for Lauda chillers. Manages one Chiller instance per unique COM port."""

    controllerParent: Chiller

    def __init__(self, controllerParent: Chiller) -> None:
        super().__init__(controllerParent=controllerParent)
        self.chillers = {}  # COM port -> Chiller instance
        self.initCOMs()

    def initCOMs(self) -> None:
        """Initialize COM port list."""
        self.COMs = self.controllerParent.getCOMs() or [23]

    def initializeValues(self, reset: bool = False) -> None:  # noqa: ARG002
        """Initialize values array: one entry per channel for monitor readback."""
        self.COMs = self.controllerParent.getCOMs() or [23]
        channels = self.controllerParent.getChannels()
        if channels:
            self.values = np.full(len(channels), fill_value=np.nan, dtype=np.float32)

    def runInitialization(self) -> None:
        self.initCOMs()
        try:
            from devices.chiller import Chiller as ChillerDev

            self.chillers = {}
            for com in self.COMs:
                self.print(f'Connecting to chiller on COM{com}...')
                chiller = ChillerDev(device_id=f'chiller_com{com}', port=f'COM{com}', baudrate=9600)
                if not chiller.connect():
                    self.print(f'Failed to connect to chiller on COM{com}.', flag=PRINT.ERROR)
                    return
                self.chillers[com] = chiller
                self.print(f'Chiller on COM{com} connected.')

            if self.controllerParent.isOn():
                for com, chiller in self.chillers.items():
                    try:
                        chiller.start_device()
                    except Exception as e:  # noqa: BLE001
                        self.print(f'Failed to start chiller on COM{com}: {e}', flag=PRINT.WARNING)

            self.signalComm.initCompleteSignal.emit()
        except Exception as e:  # noqa: BLE001
            self.print(f'Error initializing chiller: {e}', flag=PRINT.ERROR)
        finally:
            self.initializing = False

    def applyValue(self, channel: ChillerChannel) -> None:
        chiller = self.chillers.get(channel.com)
        if chiller is None:
            return
        temp = channel.value if (channel.enabled and self.controllerParent.isOn()) else 20
        with self.lock.acquire_timeout(1, timeoutMessage=f'Cannot acquire lock to set {channel.name}.') as lock_acquired:
            if lock_acquired:
                try:
                    chiller.set_temperature(temp)
                    self.print(f'Set {channel.name} to {temp:.1f} °C (COM{channel.com})', flag=PRINT.TRACE)
                except Exception as e:  # noqa: BLE001
                    self.print(f'Error setting {channel.name}: {e}', flag=PRINT.WARNING)
                    self.errorCount += 1

    def readNumbers(self) -> None:
        """Read current temperatures from all chillers."""
        channels = self.controllerParent.getChannels()
        for i, ch in enumerate(channels):
            if ch.enabled and ch.real:
                chiller = self.chillers.get(ch.com)
                if chiller is None:
                    continue
                try:
                    self.values[i] = chiller.read_temp()
                    self.errorCount = 0
                except Exception as e:  # noqa: BLE001
                    self.print(f'Error reading chiller on COM{ch.com}: {e}', flag=PRINT.ERROR)
                    self.errorCount += 1

    def fakeNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real:
                if self.controllerParent.isOn():
                    # Exponentially approach target temperature with small fluctuation
                    self.values[i] = max((self.values[i] + self.rng.uniform(-0.2, 0.2)) + 0.05 * (channel.value - self.values[i]), -10)
                else:
                    self.values[i] = 22 + self.rng.uniform(-0.5, 0.5)

    def updateValues(self) -> None:
        if self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real and i < len(self.values):
                channel.monitor = np.nan if channel.waitToStabilize else self.values[i]

    def toggleOn(self) -> None:
        super().toggleOn()
        on = self.controllerParent.isOn()
        for com, chiller in self.chillers.items():
            try:
                if on:
                    chiller.start_device()
                else:
                    chiller.stop_device()
            except Exception as e:  # noqa: BLE001
                self.print(f'Error {"starting" if on else "stopping"} chiller on COM{com}: {e}', flag=PRINT.ERROR)
        if on:
            for channel in self.controllerParent.getChannels():
                if channel.real:
                    self.applyValueFromThread(channel)

    def setPumpLevel(self, channel: ChillerChannel) -> None:
        """Set the pump level on a chiller.

        :param channel: The channel for which to change the pump level.
        :type channel: ChillerChannel
        """
        chiller = self.chillers.get(channel.com)
        if chiller is None:
            return
        with self.lock.acquire_timeout(1, timeoutMessage=f'Cannot acquire lock for pump level on {channel.name}.') as lock_acquired:
            if lock_acquired:
                try:
                    chiller.set_pump_level(channel.pumpLevel)
                    self.print(f'Set {channel.name} pump level to {channel.pumpLevel} (COM{channel.com})', flag=PRINT.TRACE)
                except Exception as e:  # noqa: BLE001
                    self.print(f'Error setting pump level on {channel.name}: {e}', flag=PRINT.WARNING)

    def closeCommunication(self) -> None:
        super().closeCommunication()
        for com, chiller in self.chillers.items():
            try:
                chiller.stop_device()
            except Exception:  # noqa: BLE001
                pass
            try:
                chiller.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self.chillers = {}
        self.initialized = False
