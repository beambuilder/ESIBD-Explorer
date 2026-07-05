# pylint: disable=[missing-module-docstring]  # see class docstrings
import time
from typing import cast

import numpy as np

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, parameterDict
from esibd.devices.com_helper import getComPort
from esibd.devices.lab_telemetry import ChannelThrottle, getLabSink
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [SyringePump]


HK_PUSH_INTERVAL_S = 30  # pump run-state cadence; displaced-volume telemetry stays on ChannelThrottle


class SyringePump(Device):
    """Syringe pump feeding the ESI emitter.

    One channel per pump: the channel VALUE is the flow rate (mL/hr), the On/Off
    logic starts and stops the pump (a start resets the pump's displaced-volume
    counter), and a read-only indicator shows the displaced volume the pump
    reports. Syringe volume and diameter are advanced channel parameters, sent
    to the pump on every start.
    """

    name = 'SyringePump'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = 'mL/hr'
    iconFile = 'SyringePump.png'
    useOnOffLogic = True
    channels: 'list[PumpChannel]'

    # type hints for settings
    comPort: int

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = PumpChannel

    def initGUI(self) -> None:
        super().initGUI()
        self.controller = SyringePumpController(controllerParent=self)

    def getChannels(self) -> 'list[PumpChannel]':
        return cast('list[PumpChannel]', super().getChannels())

    def getDefaultSettings(self) -> dict[str, dict]:
        settings = super().getDefaultSettings()
        settings[f'{self.name}/Interval'][Parameter.VALUE] = 1000
        settings[f'{self.name}/COM Port'] = parameterDict(value=getComPort('Syringe_Pump', default=5), minimum=1, maximum=99,
                                                           toolTip='COM port number of the syringe pump.',
                                                           parameterType=PARAMETERTYPE.INT, attr='comPort')
        return settings

    def closeCommunication(self) -> None:
        self.setOn(False)
        self.controller.toggleOnFromThread(parallel=False)
        super().closeCommunication()


class PumpChannel(Channel):
    """Channel for one syringe pump: flow rate in, displaced volume out."""

    DISPLACED = 'Displaced'
    VOLUME = 'Volume'
    DIAMETER = 'Diameter'
    channelParent: SyringePump

    def getDefaultChannel(self) -> dict[str, dict]:

        self.displaced: float
        self.volume: float
        self.diameter: float

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'Rate (mL/hr)'
        channel[self.DISPLACED] = parameterDict(value=0, parameterType=PARAMETERTYPE.FLOAT, advanced=False, header='V (mL)', indicator=True, attr='displaced',
                                                toolTip='Displaced volume reported by the pump (read-only; resets on start).')
        channel[self.VOLUME] = parameterDict(value=1.0, parameterType=PARAMETERTYPE.FLOAT, advanced=True,
                                             header='Syringe (mL)', toolTip='Syringe volume in mL, sent to the pump on start.', attr='volume')
        channel[self.DIAMETER] = parameterDict(value=4.64, parameterType=PARAMETERTYPE.FLOAT, advanced=True,
                                               header='Dia (mm)', toolTip='Syringe inner diameter in mm, sent to the pump on start.', attr='diameter')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.insertDisplayedParameter(self.DISPLACED, before=self.DISPLAY)
        self.displayedParameters.append(self.VOLUME)
        self.displayedParameters.append(self.DIAMETER)

    def tempParameters(self) -> list[str]:
        return [*super().tempParameters(), self.DISPLACED]

    def realChanged(self) -> None:
        self.getParameterByName(self.DISPLACED).setVisible(self.real)
        self.getParameterByName(self.VOLUME).setVisible(self.real)
        self.getParameterByName(self.DIAMETER).setVisible(self.real)
        super().realChanged()


class SyringePumpController(DeviceController):
    """Controller for the syringe pump (esibd_bs SyringePump on SerialDeviceBase)."""

    controllerParent: SyringePump

    def __init__(self, controllerParent: SyringePump) -> None:
        super().__init__(controllerParent=controllerParent)
        self.pump = None  # esibd_bs SyringePump instance
        self.telemetryThrottle = ChannelThrottle()
        self.hkPushTime = 0.0  # last run-state push (epoch s)

    def initializeValues(self, reset: bool = False) -> None:  # noqa: ARG002
        """Values array holds the displaced volume (mL) per channel."""
        channels = self.controllerParent.getChannels()
        if channels:
            self.values = np.full(len(channels), fill_value=np.nan, dtype=np.float32)

    def runInitialization(self) -> None:
        try:
            from devices.syringe_pump import SyringePump as SyringePumpDevice

            com = self.controllerParent.comPort
            sink = getLabSink()
            if sink is None:
                self.print('Telemetry sink unavailable (LAB_CONFIG not set?) — running without telemetry.db writes.', flag=PRINT.DEBUG)
            self.print(f'Connecting to syringe pump on COM{com}...')
            self.pump = SyringePumpDevice(device_id='Syringe_Pump', port=f'COM{com}', sink=sink)

            if not self.pump.connect():
                self.print(f'Failed to connect to syringe pump on COM{com}.', flag=PRINT.ERROR)
                self.pump = None
                return

            self.print(f'Syringe pump connected on COM{com}.')
            self.signalComm.initCompleteSignal.emit()
        except Exception as e:  # noqa: BLE001
            self.print(f'Error initializing syringe pump: {e}', flag=PRINT.ERROR)
            self.pump = None
        finally:
            self.initializing = False

    def fakeInitialization(self) -> None:
        """Create a simulated esibd_bs device before faking init, so Test Mode telemetry lands in the shared db with sim=1."""
        self.pump = None
        try:
            from devices.syringe_pump import SyringePump as SyringePumpDevice

            sink = getLabSink()
            if sink is not None:
                self.pump = SyringePumpDevice(device_id='Syringe_Pump', port=f'COM{self.controllerParent.comPort}', sink=sink, test_mode=True)
                self.pump.connect()
        except Exception as e:  # noqa: BLE001
            self.print(f'Test Mode runs without telemetry device: {e}', flag=PRINT.DEBUG)
        super().fakeInitialization()

    def _pushTelemetry(self) -> None:
        """Write the displaced volume to the telemetry sink, throttled per channel (>=5 s)."""
        if self.pump is None or self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if not (channel.enabled and channel.real and i < len(self.values)):
                continue
            value = float(self.values[i])
            if not np.isfinite(value) or not self.telemetryThrottle.ready(channel.name):
                continue
            self.pump.log_sample('Displaced_Vol', value, 'mL', '.3f')
        self._pushHousekeeping()

    def _pushHousekeeping(self, force: bool = False) -> None:
        """Push the pump run state to the telemetry sink every HK_PUSH_INTERVAL_S.

        Rides the read loop (which already holds the controller lock) — never the esibd_bs hk worker.
        force=True skips the time gate (final push on closeCommunication; caller must hold the controller lock).
        """
        if self.pump is None or (not force and time.time() - self.hkPushTime < HK_PUSH_INTERVAL_S):
            return
        self.hkPushTime = time.time()
        try:
            status = self.pump.get_pump_status()
            if status:
                self.pump.log_sample('Pump_Running', 1 if 'pump' in status[0].lower() else 0)
        except Exception as e:  # noqa: BLE001
            self.print(f'Run-state push failed: {e}', flag=PRINT.DEBUG)

    def applyValue(self, channel: PumpChannel) -> None:
        """Send a changed flow rate to the pump; takes effect live while pumping."""
        if self.pump is None:
            return
        with self.lock.acquire_timeout(1, timeoutMessage=f'Cannot acquire lock to set {channel.name}.') as lock_acquired:
            if lock_acquired:
                self.pump.pump_rate = channel.value
                self.pump.set_rate(channel.value)
                self.print(f'Set {channel.name} rate to {channel.value:.3f} mL/hr', flag=PRINT.TRACE)

    def readNumbers(self) -> None:
        """Poll the displaced volume the pump reports (e.g. "0.923 mL")."""
        if self.pump is None or self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if not (channel.enabled and channel.real and i < len(self.values)):
                continue
            try:
                displaced = self.pump.get_displaced_volume()
                self.values[i] = float(displaced[0].split()[0]) if displaced else np.nan
                self.errorCount = 0
            except (ValueError, IndexError):
                self.values[i] = np.nan
            except Exception as e:  # noqa: BLE001
                self.print(f'Error reading displaced volume: {e}', flag=PRINT.ERROR)
                self.errorCount += 1
        self._pushTelemetry()

    def fakeNumbers(self) -> None:
        if self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real:
                if self.controllerParent.isOn():
                    base = self.values[i] if np.isfinite(self.values[i]) else 0.0
                    self.values[i] = base + channel.value / 3600.0  # ~1 s of flow per tick
                else:
                    self.values[i] = self.values[i] if np.isfinite(self.values[i]) else 0.0
        self._pushTelemetry()

    def updateValues(self) -> None:
        if self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real and i < len(self.values) and np.isfinite(self.values[i]):
                channel.displaced = float(self.values[i])

    def toggleOn(self) -> None:
        super().toggleOn()
        if self.pump is None:
            return
        on = self.controllerParent.isOn()
        try:
            with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock to toggle the syringe pump.') as lock_acquired:
                if not lock_acquired:
                    return
                if on:
                    channel = next((ch for ch in self.controllerParent.getChannels() if ch.real and ch.enabled), None)
                    if channel is None:
                        self.print('No enabled real channel — pump not started.', flag=PRINT.WARNING)
                        return
                    # stop first: a start resets the pump's displaced-volume counter
                    self.pump.stop_pump()
                    self.pump.volume = channel.volume
                    self.pump.diameter = channel.diameter
                    self.pump.apply_parameters(rate=channel.value)
                    self.pump.start_pump()
                    self.print(f'Syringe pump started at {channel.value:.3f} mL/hr '
                               f'(syringe {channel.volume} mL, dia {channel.diameter} mm).')
                else:
                    self.pump.stop_pump()
                    self.print('Syringe pump stopped.')
            self.hkPushTime = 0.0  # next read cycle pushes the new run state right away
        except Exception as e:  # noqa: BLE001
            self.print(f'Error toggling the syringe pump: {e}', flag=PRINT.ERROR)

    def closeCommunication(self) -> None:
        super().closeCommunication()  # stops acquisition first
        if self.pump is not None:
            with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock to close the syringe pump.'):
                try:
                    self.pump.stop_pump()
                except Exception:  # noqa: BLE001
                    pass
                # final push so telemetry records the stopped state
                self._pushHousekeeping(force=True)
                try:
                    self.pump.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            self.pump = None
        self.initialized = False
