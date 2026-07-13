# pylint: disable=[missing-module-docstring]  # see class docstrings
import queue
import threading
import time
from typing import cast

import numpy as np
from PyQt6.QtCore import pyqtSignal

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, getTestMode, parameterDict
from esibd.devices.com_helper import getComPort
from esibd.devices.lab_telemetry import ChannelThrottle, getLabSink
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [AMPR12]


HK_PUSH_INTERVAL_S = 10  # electronics housekeeping (temps, enable state) cadence; voltage telemetry stays on ChannelThrottle
APPLY_GAP_S = 0.05  # breathing room between queued voltage sets — back-to-back frames draw status -10 (no response) from the device


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
        self.setOn(False)  # Device.setOn already runs toggleOnFromThread(parallel=False) when initialized — no second toggle here
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
    """Controller for AMPR-12 devices. Manages one AMPR instance per unique COM port.

    All voltage sets are serialized through a single apply-worker thread: the framework
    spawns one thread per channel on every toggle/apply-all (core.applyValueFromThread),
    and with many channels that herd starves the one serial lock — sets time out and are
    silently lost (real HW 2026-07-13).
    """

    controllerParent: AMPR12

    class SignalCommunicate(DeviceController.SignalCommunicate):
        """Bundle pyqtSignals."""

        revertOnSignal = pyqtSignal()
        """Signal that reverts the device On toggle in the main thread after a failed PSU enable."""

    def __init__(self, controllerParent: AMPR12) -> None:
        super().__init__(controllerParent=controllerParent)
        self.amprs = {}  # COM port -> AMPR instance
        self.telemetryThrottle = ChannelThrottle()
        self.hkPushTimes = {}  # COM port -> last housekeeping push (epoch s)
        self.applyQueue = queue.Queue()  # channels waiting for the apply worker
        self._pendingApplies = set()  # channels queued but not yet applied (dedupe)
        self._applyDispatchLock = threading.Lock()
        self.applyWorker = None
        self.signalComm.revertOnSignal.connect(self._revertOn)
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

    def applyValueFromThread(self, channel: VoltageChannel) -> None:
        # Queue for the single apply worker instead of spawning a thread per channel (see class docstring).
        if getTestMode() or not self.initialized:
            return
        with self._applyDispatchLock:
            if id(channel) in self._pendingApplies:  # id key: Channel extends QTreeWidgetItem, which is unhashable in PyQt6
                return  # already queued; the worker reads channel.value live at apply time
            self._pendingApplies.add(id(channel))
            self.applyQueue.put(channel)
            if self.applyWorker is None or not self.applyWorker.is_alive():
                self.applyWorker = threading.Thread(target=self._runApplyWorker, name=f'{self.controllerParent.name} applyWorkerThread', daemon=True)
                self.applyWorker.start()

    def _runApplyWorker(self) -> None:
        """Drain the apply queue, one serial exchange at a time; exits when idle (recreated on demand)."""
        while True:
            try:
                channel = self.applyQueue.get(timeout=5)
            except queue.Empty:
                with self._applyDispatchLock:
                    if self.applyQueue.empty():
                        self.applyWorker = None  # deregister under the dispatch lock so a concurrent put spawns a fresh worker instead of getting lost
                        return
                continue
            with self._applyDispatchLock:
                self._pendingApplies.discard(id(channel))
            if self.initialized:
                self.applyValue(channel)
                time.sleep(APPLY_GAP_S)

    def applyValue(self, channel: VoltageChannel) -> None:
        ampr = self.amprs.get(channel.com)
        if ampr is None:
            return
        voltage = channel.value if (channel.enabled and self.controllerParent.isOn()) else 0
        for _ in range(2):  # a busy read-loop/housekeeping cycle may hold the lock; a dropped set is worse than a late one
            with self.lock.acquire_timeout(2) as lock_acquired:
                if lock_acquired:
                    # call_with_retry purges the port and retries once on a nonzero status — a -10 (no response)
                    # leaves stale bytes in the RX buffer that would desync the next exchange ([[cgc-ampr]])
                    status = ampr.call_with_retry(ampr.set_module_voltage, channel.module, channel.ch, voltage)
                    if status != ampr.NO_ERR:
                        self.print(f'Error setting {channel.name}: status {status}', flag=PRINT.WARNING)
                        self.errorCount += 1
                    else:
                        self.print(f'Set {channel.name} to {voltage:.3f} V (COM{channel.com} mod{channel.module} ch{channel.ch})', flag=PRINT.TRACE)
                    return
        self.print(f'Could not acquire lock to set {channel.name} — value NOT applied. Change the value again to retry.', flag=PRINT.ERROR)

    def runAcquisition(self) -> None:
        """Apply-aware framework loop: pending voltage sets get the serial lock first.

        A read cycle that cannot get the lock (or finds sets pending) is skipped silently —
        the next cycle, one interval later, covers it. The framework loop instead printed
        'Could not acquire lock to acquire data' whenever a set burst held the lock, and its
        bulk read made every live voltage edit wait out a full read cycle first.
        """
        while self.acquiring:
            if self.applyQueue.empty():
                with self.lock.acquire_timeout(1) as lock_acquired:
                    if lock_acquired:
                        self.fakeNumbers() if getTestMode() else self.readNumbers()
                        self.signalComm.updateValuesSignal.emit()
            time.sleep(self.getDevice().interval / 1000)

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
        failed = []
        for com, ampr in self.amprs.items():
            try:
                if not self._setPSUEnable(com, ampr, on=on):
                    failed.append(com)
            except Exception as e:  # noqa: BLE001
                self.print(f'Error toggling PSU on COM{com}: {e}', flag=PRINT.ERROR)
                failed.append(com)
        if not on:
            return
        if failed:
            self._abortEnable(failed)
            return
        # Give the device a moment to settle before pushing channel values.
        time.sleep(0.2)
        for channel in self.controllerParent.getChannels():
            if channel.real:
                self.applyValueFromThread(channel)  # queued: applied one by one by the apply worker

    def _abortEnable(self, failed: list[int]) -> None:
        """Disable the units that did enable and revert the On toggle — never pretend On.

        :param failed: COM ports whose PSU enable failed.
        :type failed: list[int]
        """
        for com, ampr in self.amprs.items():
            if com not in failed:
                try:
                    self._setPSUEnable(com, ampr, on=False)
                except Exception as e:  # noqa: BLE001
                    self.print(f'Error disabling PSU on COM{com}: {e}', flag=PRINT.ERROR)
        self.print(f'PSU enable failed on COM{failed} — reverting to Off.', flag=PRINT.ERROR)
        self.signalComm.revertOnSignal.emit()

    def _setPSUEnable(self, com: int, ampr, on: bool) -> bool:
        """Send the PSU enable/disable for one unit. True only when the device confirms the requested state.

        The DLL exchange rides the same serial channel as readNumbers/applyValue —
        an unlocked call garbles whatever exchange is in flight (status -13 storms
        observed on real hardware 2026-07-05), so every DLL call must hold the lock.

        :param com: COM port of the unit.
        :type com: int
        :param ampr: esibd_bs AMPR instance for that port.
        :param on: Requested PSU enable state.
        :type on: bool
        :return: True if the device confirmed the requested state.
        :rtype: bool
        """
        for _ in range(3):  # the read loop may hold the lock through a housekeeping cycle — retry instead of silently skipping the enable
            with self.lock.acquire_timeout(2) as lock_acquired:
                if not lock_acquired:
                    continue
                psu_status, enabled = ampr.enable_psu(on)
                if psu_status != ampr.NO_ERR:
                    self.print(f'Failed to {"enable" if on else "disable"} PSU on COM{com}: status {psu_status}', flag=PRINT.WARNING)
                    return False
                self.print(f'AMPR-12 on COM{com}: PSU {"enabled" if enabled else "disabled"}')
                self.hkPushTimes[com] = 0.0  # next read cycle pushes the new enable state to telemetry right away
                if on and not enabled:
                    self.print(f'AMPR-12 on COM{com}: device refused PSU enable — check the interlock '
                               '(after a module change, run the vendor module init first).', flag=PRINT.WARNING)
                    return False
                return True
        self.print(f'Cannot acquire lock to {"enable" if on else "disable"} PSU on COM{com}.', flag=PRINT.ERROR)
        return False

    def _revertOn(self) -> None:
        """Flip the On toggle back Off after a failed enable (runs in the main thread)."""
        self.controllerParent.setOn(False)

    def closeCommunication(self) -> None:
        with self._applyDispatchLock:
            # pending sets are pointless once the PSUs go down — drop them so the worker cannot touch a disconnecting device
            while not self.applyQueue.empty():
                try:
                    self.applyQueue.get_nowait()
                except queue.Empty:
                    break
            self._pendingApplies.clear()
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
