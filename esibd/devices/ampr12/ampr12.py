# pylint: disable=[missing-module-docstring]  # see class docstrings
import time
from typing import cast

import numpy as np

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, getTestMode, parameterDict
from esibd.devices.com_helper import getComPort
from esibd.devices.lab_telemetry import ChannelThrottle, getLabSink
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [AMPR12]


HK_PUSH_INTERVAL_S = 30  # electronics housekeeping (temps, enable state) cadence; voltage telemetry stays on ChannelThrottle


class AMPR12(Device):
    """Contains a list of voltage channels from one or multiple CGC AMPR-12 power supplies.

    Each AMPR-12 can have up to 12 modules with 4 output channels each.
    Supports monitor readback and On/Off logic for PSU enable control.
    """

    name = 'AMPR12'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = 'V'
    iconFile = 'AMPR12.png'
    useMonitors = True
    useOnOffLogic = True
    channels: 'list[VoltageChannel]'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = VoltageChannel

    def initGUI(self) -> None:
        super().initGUI()
        self.controller = VoltageController(controllerParent=self)

    def getChannels(self) -> 'list[VoltageChannel]':
        return cast('list[VoltageChannel]', super().getChannels())

    def getDefaultSettings(self) -> dict[str, dict]:
        settings = super().getDefaultSettings()
        settings[f'{self.name}/Interval'][Parameter.VALUE] = 1000
        settings[f'{self.name}/{self.MAXDATAPOINTS}'][Parameter.VALUE] = 1E5
        return settings

    def getCOMs(self) -> list[int]:
        """Get list of unique COM port numbers used by real channels."""
        return list({channel.com for channel in self.channels if channel.real})

    def closeCommunication(self) -> None:
        self.setOn(False)
        self.controller.toggleOnFromThread(parallel=False)
        super().closeCommunication()


class VoltageChannel(Channel):
    """Channel for a single AMPR-12 module output."""

    COM = 'COM'
    MODULE = 'Module'
    CH = 'Ch'
    channelParent: AMPR12

    def getDefaultChannel(self) -> dict[str, dict]:

        self.com: int
        self.module: int
        self.ch: int

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'Voltage (V)'
        channel[self.COM] = parameterDict(value=getComPort('AMPR1000', default=8), minimum=1, maximum=99, parameterType=PARAMETERTYPE.INT, advanced=True,
                                          header='COM', toolTip='COM port number of the AMPR-12.', attr='com')
        channel[self.MODULE] = parameterDict(value=0, minimum=0, maximum=11, parameterType=PARAMETERTYPE.INT, advanced=True,
                                             header='Mod', toolTip='Module address (0-11).', attr='module')
        channel[self.CH] = parameterDict(value=0, minimum=0, maximum=3, parameterType=PARAMETERTYPE.INT, advanced=True,
                                         header='Ch', toolTip='Channel on module (0-3).', attr='ch')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.displayedParameters.append(self.COM)
        self.displayedParameters.append(self.MODULE)
        self.displayedParameters.append(self.CH)

    def monitorChanged(self) -> None:
        self.updateWarningState(self.enabled and self.channelParent.controller.acquiring
                                and ((self.channelParent.isOn() and abs(self.monitor - self.value) > 1)
                                or (not self.channelParent.isOn() and abs(self.monitor - 0) > 1)))

    def realChanged(self) -> None:
        self.getParameterByName(self.COM).setVisible(self.real)
        self.getParameterByName(self.MODULE).setVisible(self.real)
        self.getParameterByName(self.CH).setVisible(self.real)
        super().realChanged()


class VoltageController(DeviceController):
    """Controller for AMPR-12 devices. Manages one AMPR instance per unique COM port."""

    controllerParent: AMPR12

    def __init__(self, controllerParent: AMPR12) -> None:
        super().__init__(controllerParent=controllerParent)
        self.amprs = {}  # COM port -> AMPR instance
        self.telemetryThrottle = ChannelThrottle()
        self.hkPushTimes = {}  # COM port -> last housekeeping push (epoch s)
        self.initCOMs()

    def _labDeviceName(self, com: int) -> str:
        """Telemetry device name for a COM port: the canonical com_ports key (AMPR500/AMPR1000) where known."""
        names = {getComPort(key, default=-1): key for key in ('AMPR500', 'AMPR1000')}
        return names.get(com, f'AMPR12_COM{com}')

    def initCOMs(self) -> None:
        """Initialize COM port list."""
        self.COMs = self.controllerParent.getCOMs() or [7]

    def initializeValues(self, reset: bool = False) -> None:  # noqa: ARG002
        """Initialize values array: one entry per channel for monitor readback."""
        self.COMs = self.controllerParent.getCOMs() or [7]
        channels = self.controllerParent.getChannels()
        if channels:
            self.values = np.full(len(channels), fill_value=np.nan, dtype=np.float32)

    def runInitialization(self) -> None:
        self.initCOMs()
        try:
            from devices.cgc import AMPR

            sink = getLabSink()
            if sink is None:
                self.print('Telemetry sink unavailable (LAB_CONFIG not set?) — running without telemetry.db writes.', flag=PRINT.DEBUG)
            self.amprs = {}
            for com in self.COMs:
                self.print(f'Connecting to AMPR-12 on COM{com}...')
                ampr = AMPR(device_id=self._labDeviceName(com), com=com, baudrate=230400, sink=sink)
                if not ampr.connect():
                    self.print(f'Failed to connect to AMPR-12 on COM{com}.', flag=PRINT.ERROR)
                    return
                self.amprs[com] = ampr
                self.print(f'AMPR-12 on COM{com} connected.')

                # Discover present modules so we can warn if a configured channel points at a missing module.
                present_status, _, max_module, presence_list = ampr.get_module_presence()
                if present_status == ampr.NO_ERR:
                    present_modules = [i for i in range(max_module + 1) if presence_list[i] == ampr.MODULE_PRESENT]
                    self.print(f'AMPR-12 on COM{com}: modules present {present_modules}')
                    configured_modules = {ch.module for ch in self.controllerParent.getChannels() if ch.real and ch.com == com}
                    missing = configured_modules - set(present_modules)
                    if missing:
                        self.print(f'AMPR-12 on COM{com}: configured channels reference missing modules {sorted(missing)}', flag=PRINT.WARNING)
                else:
                    self.print(f'AMPR-12 on COM{com}: get_module_presence failed (status {present_status})', flag=PRINT.WARNING)

            if self.controllerParent.isOn():
                for com, ampr in self.amprs.items():
                    psu_status, enabled = ampr.enable_psu(True)
                    if psu_status != ampr.NO_ERR:
                        self.print(f'Failed to enable PSU on COM{com}: status {psu_status}', flag=PRINT.WARNING)
                    else:
                        self.print(f'AMPR-12 on COM{com}: PSU enabled ({enabled})')
                # Give the device a moment to settle before set_module_voltage calls.
                time.sleep(0.2)

            self.signalComm.initCompleteSignal.emit()
        except Exception as e:  # noqa: BLE001
            self.print(f'Error initializing AMPR-12: {e}', flag=PRINT.ERROR)
        finally:
            self.initializing = False

    def fakeInitialization(self) -> None:
        """Create simulated esibd_bs devices before faking init, so Test Mode telemetry lands in the shared db with sim=1."""
        self.initCOMs()
        self.amprs = {}
        try:
            from devices.cgc import AMPR

            sink = getLabSink()
            if sink is not None:
                for com in self.COMs:
                    ampr = AMPR(device_id=self._labDeviceName(com), com=com, baudrate=230400, sink=sink, test_mode=True)
                    ampr.connect()
                    self.amprs[com] = ampr
        except Exception as e:  # noqa: BLE001
            self.print(f'Test Mode runs without telemetry devices: {e}', flag=PRINT.DEBUG)
        super().fakeInitialization()

    def _pushTelemetry(self) -> None:
        """Write the freshly read channel values to the telemetry sink, throttled per channel (>=5 s)."""
        if not self.amprs or self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if not (channel.enabled and channel.real and i < len(self.values)):
                continue
            value = float(self.values[i])
            ampr = self.amprs.get(channel.com)
            if ampr is None or not np.isfinite(value) or not self.telemetryThrottle.ready(channel.name):
                continue
            ampr.log_sample(channel.name, value, self.controllerParent.unit)
        self._pushHousekeeping()

    def _pushHousekeeping(self) -> None:
        """Push electronics housekeeping (internal temps + PSU enable state) to the telemetry sink every HK_PUSH_INTERVAL_S.

        Rides the read loop (which already holds the controller lock) — never a second polling thread on the DLL.
        DB-only: these are not Explorer channels and never appear in the GUI; the dashboard's Electronics tab reads them.
        """
        now = time.time()
        for com, ampr in self.amprs.items():
            if now - self.hkPushTimes.get(com, 0.0) < HK_PUSH_INTERVAL_S:
                continue
            self.hkPushTimes[com] = now
            self._pushHousekeepingUnit(com, ampr)

    def _pushHousekeepingUnit(self, com: int, ampr) -> None:
        """One housekeeping read+push for one unit. Caller must hold the controller lock (or ride the locked read loop)."""
        try:
            (status, _volt_12v, _volt_5v0, _volt_3v3, _volt_agnd, _volt_12vp, _volt_12vn,
             _volt_hvp, _volt_hvn, temp_cpu, temp_adc, temp_av, temp_hvp, temp_hvn,
             _line_freq) = ampr.get_housekeeping()
            if status == ampr.NO_ERR:
                ampr.log_sample('Temp_CPU', temp_cpu, 'degC', '.1f')
                ampr.log_sample('Temp_ADC', temp_adc, 'degC', '.1f')
                ampr.log_sample('Temp_AV', temp_av, 'degC', '.1f')
                ampr.log_sample('Temp_HV_P', temp_hvp, 'degC', '.1f')
                ampr.log_sample('Temp_HV_N', temp_hvn, 'degC', '.1f')
            state_status, state_hex, _names = ampr.get_device_state()
            if state_status == ampr.NO_ERR:
                ampr.log_sample('PSU_Enabled', 1 if int(state_hex, 16) & 1 else 0)
        except Exception as e:  # noqa: BLE001
            self.print(f'Housekeeping push failed for COM{com}: {e}', flag=PRINT.DEBUG)

    def applyValue(self, channel: VoltageChannel) -> None:
        ampr = self.amprs.get(channel.com)
        if ampr is None:
            return
        voltage = channel.value if (channel.enabled and self.controllerParent.isOn()) else 0
        with self.lock.acquire_timeout(1, timeoutMessage=f'Cannot acquire lock to set {channel.name}.') as lock_acquired:
            if lock_acquired:
                status = ampr.set_module_voltage(channel.module, channel.ch, voltage)
                if status != ampr.NO_ERR:
                    self.print(f'Error setting {channel.name}: status {status}', flag=PRINT.WARNING)
                    self.errorCount += 1
                else:
                    self.print(f'Set {channel.name} to {voltage:.3f} V (COM{channel.com} mod{channel.module} ch{channel.ch})', flag=PRINT.TRACE)

    def readNumbers(self) -> None:
        """Read measured voltages from all AMPR modules for monitor feedback."""
        channels = self.controllerParent.getChannels()
        # Group channels by (COM, module) for efficient bulk reads
        module_groups: dict[tuple[int, int], list[tuple[int, VoltageChannel]]] = {}
        for i, ch in enumerate(channels):
            if ch.enabled and ch.real:
                key = (ch.com, ch.module)
                if key not in module_groups:
                    module_groups[key] = []
                module_groups[key].append((i, ch))

        for (com, module), ch_list in module_groups.items():
            ampr = self.amprs.get(com)
            if ampr is None:
                continue
            try:
                status, measured = ampr.get_measured_module_output_voltages(module)
                if status == ampr.NO_ERR:
                    for idx, ch in ch_list:
                        if 0 <= ch.ch < len(measured):
                            self.values[idx] = measured[ch.ch]
                    self.errorCount = 0
                else:
                    self.errorCount += 1
            except Exception as e:  # noqa: BLE001
                self.print(f'Error reading COM{com} module {module}: {e}', flag=PRINT.ERROR)
                self.errorCount += 1
        self._pushTelemetry()

    def fakeNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real:
                if self.controllerParent.isOn() and channel.enabled:
                    self.values[i] = channel.value + 5 * self.rng.choice([0, 1], p=[0.98, 0.02]) + self.rng.random() - 0.5
                else:
                    self.values[i] = 5 * self.rng.choice([0, 1], p=[0.9, 0.1]) + self.rng.random() - 0.5
        self._pushTelemetry()

    def updateValues(self) -> None:
        if self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real and i < len(self.values):
                channel.monitor = np.nan if channel.waitToStabilize else self.values[i]

    def toggleOn(self) -> None:
        super().toggleOn()
        on = self.controllerParent.isOn()
        for com, ampr in self.amprs.items():
            try:
                # The DLL exchange rides the same serial channel as readNumbers/applyValue —
                # an unlocked call garbles whatever exchange is in flight (status -13 storms
                # observed on real hardware 2026-07-05), so every DLL call must hold the lock.
                with self.lock.acquire_timeout(2, timeoutMessage=f'Cannot acquire lock to toggle PSU on COM{com}.') as lock_acquired:
                    if not lock_acquired:
                        continue
                    psu_status, enabled = ampr.enable_psu(on)
                if psu_status != ampr.NO_ERR:
                    self.print(f'Failed to {"enable" if on else "disable"} PSU on COM{com}: status {psu_status}', flag=PRINT.WARNING)
                else:
                    self.print(f'AMPR-12 on COM{com}: PSU {"enabled" if enabled else "disabled"}')
                    self.hkPushTimes[com] = 0.0  # next read cycle pushes the new enable state to telemetry right away
                    if on and not enabled:
                        self.print(f'AMPR-12 on COM{com}: device refused PSU enable — check the interlock.', flag=PRINT.WARNING)
            except Exception as e:  # noqa: BLE001
                self.print(f'Error toggling PSU on COM{com}: {e}', flag=PRINT.ERROR)
        if on:
            # Give the device a moment to settle before pushing channel values.
            time.sleep(0.2)
            for channel in self.controllerParent.getChannels():
                if channel.real:
                    self.applyValueFromThread(channel)

    def closeCommunication(self) -> None:
        super().closeCommunication()  # stops acquisition first
        for com, ampr in self.amprs.items():
            with self.lock.acquire_timeout(2, timeoutMessage=f'Cannot acquire lock to close COM{com}.'):
                try:
                    ampr.enable_psu(False)
                except Exception:  # noqa: BLE001
                    pass
                # final hk push so telemetry records the disabled state
                # (the read loop is already stopped; dashboard staleness
                # covers a crashed Explorer, this covers a clean close)
                self._pushHousekeepingUnit(com, ampr)
                try:
                    ampr.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        self.amprs = {}
        self.initialized = False
