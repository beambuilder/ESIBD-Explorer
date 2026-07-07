# pylint: disable=[missing-module-docstring]  # see class docstrings
import json
import threading
import time
from typing import cast

import numpy as np
from PyQt6.QtCore import pyqtSignal

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, getTestMode, getValidConfigPath, parameterDict
from esibd.devices.com_helper import getComPort
from esibd.devices.lab_telemetry import ChannelThrottle, getLabSink
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [SW]


HK_PUSH_INTERVAL_S = 30  # electronics housekeeping (temps, enable state) cadence; frequency telemetry stays on ChannelThrottle
F_MAX_KHZ = 1000.0  # proven envelope top (campaign 2026-07-06: swB open to 1 MHz at 350 V within the CGC limits)
BASELINE_CONFIG = 89  # campaign baseline NVM slot: SwitchSym 1 MHz working set, 023-proven
STANDBY_CONFIG = 0  # park slot: everything off
CONFIG_CACHE_FILE = 'sw_nvm_configs.json'  # last known NVM list, seeds the Config dropdown before enumeration
TESTMODE_CONFIG_ITEMS = ('0: Standby', '39: SwitchSym 1 kHz', '49: SwitchSym 10 kHz', '59: SwitchSym 100 kHz',
                         '79: SwitchSym 500 kHz', '89: SwitchSym 1 MHz')  # canned campaign ladder for Test Mode


def configIndex(item) -> 'int | None':
    """Return the leading integer of a '<index>: <name>' dropdown item (or a bare index string)."""
    try:
        return int(str(item).split(':', 1)[0])
    except ValueError:
        return None


def loadConfigCache() -> dict:
    """Return the last known NVM config lists per COM port (written after every real enumeration)."""
    file = getValidConfigPath() / CONFIG_CACHE_FILE
    try:
        if file.exists():
            return json.loads(file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    return {}


def saveConfigCache(cache: dict) -> None:
    """Persist the NVM config lists so the next session's dropdown seeds with real items."""
    try:
        (getValidConfigPath() / CONFIG_CACHE_FILE).write_text(json.dumps(cache, indent=2), encoding='utf-8')
    except OSError:
        pass


def mergedConfigItems() -> tuple[list[str], str]:
    """Return the Config dropdown seed (items, default item).

    Union of the cached lists, deduplicated by index, plus bare fallbacks. The union
    guarantees that any saved channel selection finds its item at channel-file load time
    (a COMBO value missing from the items resets to item 0); the fresh enumeration
    replaces the list right after the switch connects.
    """
    seen = set()
    items = []
    for cachedList in loadConfigCache().values():
        for item in cachedList:
            index = configIndex(item)
            if index is not None and index not in seen:
                seen.add(index)
                items.append(item)
    for fallback in (BASELINE_CONFIG, STANDBY_CONFIG):
        if fallback not in seen:
            seen.add(fallback)
            items.append(str(fallback))
    items.sort(key=lambda item: configIndex(item) or 0)
    default = next((item for item in items if configIndex(item) == BASELINE_CONFIG), items[0])
    return items, default


class SW(Device):
    """Contains the RF frequency channel of the CGC HV-AMX-CTRL-4ED switch controller (swB).

    swB drives two RF chains from ONE oscillator: outputs 0/1 feed Quadrupole 1 (psu2)
    and outputs 2/3 the Ion Funnels (psu1) — both chains physically share the frequency,
    so this plugin exposes ONE channel (value = frequency in kHz, campaign-proven up to
    1000). Per-chain duty would need pulser rerouting and is a v2 item.

    Every frequency move runs the campaign width re-fit (esibd_bs SW.set_rf): the NVM
    configs fix the pulser width REGISTER, so the width goes minimal first, then the
    period moves, then the duty (Duty column, in percent of the period) is re-fit —
    otherwise the duty drags with every move and width >= period sticks the output HIGH
    (DC on the load). The monoflop rule is validated before anything is written.

    On loads the selected NVM config (Config column; fresh channels default to 89 =
    SwitchSym 1 MHz, the campaign baseline working set — bare enables arm nothing) and
    then re-applies the channel frequency/duty; Off parks the switch in standby config
    0. Selecting a config while On loads it immediately the same way (same design
    choice as the PSU plugin, see the KB cgc-psu page). The dropdown lists 'index:
    name' read from the device NVM at every initialization (cached between sessions).

    Safety (CGC, binding): ramp VOLTAGE at 1 kHz first, then frequency at full voltage
    — the voltage lives in the PSU plugin, so mind the order across plugins. The PSU
    soft watchdog (300 mA for Q1 / 150 mA elsewhere, 100 W) catches over-current.

    swB sensor 0 is broken and never logged. Never run this plugin and a switch
    notebook at the same time — same COM port.
    """

    name = 'SW'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = 'kHz'
    iconFile = 'SW.png'
    useMonitors = True
    useOnOffLogic = True
    channels: 'list[RFChannel]'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = RFChannel
        self.syncingConfig = False  # guards the Config-mirroring event against recursion and programmatic updates

    def initGUI(self) -> None:
        super().initGUI()
        self.addAction(event=lambda: self.controller.purgeUnit(), toolTip=f'Purge the COM buffers of the {self.name} controller.', icon='purge.png')
        self.addAction(event=lambda: self.controller.listConfigs(), toolTip='List the NVM config slots (index + name) of the switch in the Console.',
                       icon='configs.png')
        self.controller = SWController(controllerParent=self)

    def getChannels(self) -> 'list[RFChannel]':
        return cast('list[RFChannel]', super().getChannels())

    def getDefaultSettings(self) -> dict[str, dict]:
        settings = super().getDefaultSettings()
        settings[f'{self.name}/Interval'][Parameter.VALUE] = 1000
        settings[f'{self.name}/{self.MAXDATAPOINTS}'][Parameter.VALUE] = 1E5
        return settings

    def onConfigSelected(self, channel: 'RFChannel') -> None:
        """Mirror the Config selection to every real row (one physical unit); load it right away if On."""
        if self.syncingConfig or not channel.real:
            return
        self.syncingConfig = True
        try:
            for sibling in self.getChannels():
                if sibling is not channel and sibling.real and sibling.configuration != channel.configuration:
                    sibling.getParameterByName(RFChannel.CONFIG).value = channel.configuration
        finally:
            self.syncingConfig = False
        if self.isOn():
            self.controller.applyConfigFromThread()

    def closeCommunication(self) -> None:
        self.setOn(False)
        self.controller.toggleOnFromThread(parallel=False)
        super().closeCommunication()


class RFChannel(Channel):
    """Channel for the switch RF frequency (one oscillator drives both chains)."""

    COM = 'COM'
    DUTY = 'Duty'
    PULSER = 'Pulser'
    CONFIG = 'Config'
    channelParent: SW

    def getDefaultChannel(self) -> dict[str, dict]:

        self.com: int
        self.duty: float
        self.pulser: int
        self.configuration: str

        configItems, configDefault = mergedConfigItems()
        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'f (kHz)'
        channel[self.VALUE][Parameter.MIN] = 1
        channel[self.VALUE][Parameter.MAX] = F_MAX_KHZ
        channel[self.MONITOR][Parameter.HEADER] = 'Measured f (kHz)'
        channel[self.COM] = parameterDict(value=getComPort('swB', default=10), minimum=1, maximum=99, parameterType=PARAMETERTYPE.INT, advanced=True,
                                          header='COM', toolTip='COM port number of the switch controller (swB = 10). One switch, one port — '
                                                                'every real channel is a view on the same oscillator.', attr='com')
        channel[self.DUTY] = parameterDict(value=50.0, minimum=1.0, maximum=99.0, parameterType=PARAMETERTYPE.FLOAT, advanced=False, header='Duty (%)',
                                           toolTip='Pulser duty cycle in percent of the period, re-fit on every frequency move (campaign width re-fit).\n'
                                                   'Changing it re-applies immediately while On. The monoflop rule is validated before every write.',
                                           attr='duty', event=self.dutyChanged)
        channel[self.PULSER] = parameterDict(value=0, minimum=0, maximum=3, parameterType=PARAMETERTYPE.INT, advanced=True, header='Pulser',
                                             toolTip='Pulser the duty re-fit targets (both chains ride pulser 0 in the campaign configs).', attr='pulser')
        channel[self.CONFIG] = parameterDict(value=configDefault, parameterType=PARAMETERTYPE.COMBO, items=', '.join(configItems), fixedItems=True,
                                             advanced=False, header='Config', attr='configuration', event=self.configChanged,
                                             toolTip='NVM config of the switch (index: name) — the full working set incl. enables and trigger routing.\n'
                                                     'Loaded on On (then the channel frequency/duty is applied on top); selecting while On loads it '
                                                     'immediately the same way. List refreshes from the device NVM at every initialization.')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.insertDisplayedParameter(self.CONFIG, before=self.DISPLAY)
        self.insertDisplayedParameter(self.DUTY, before=self.DISPLAY)
        self.displayedParameters.append(self.PULSER)
        self.displayedParameters.append(self.COM)

    def dutyChanged(self) -> None:
        """Re-apply the channel while On so a duty edit takes effect immediately (frequency edits go through the normal value path)."""
        if self.real and self.channelParent.isOn():
            self.channelParent.controller.applyValueFromThread(self)

    def configChanged(self) -> None:
        """Hand the user's Config selection to the device for mirroring and (if On) live-apply."""
        self.channelParent.onConfigSelected(self)

    def monitorChanged(self) -> None:
        # Only meaningful while On: parked (standby config) the oscillator register holds
        # whatever the config left there — comparing it to 0 or to the setpoint is noise.
        # Tolerance: the period register quantizes to ~1 percent at the 1 MHz end.
        self.updateWarningState(self.enabled and self.channelParent.controller.acquiring and self.channelParent.isOn()
                                and abs(self.monitor - self.value) > max(2, 0.02 * self.value))

    def realChanged(self) -> None:
        for name in (self.COM, self.DUTY, self.PULSER, self.CONFIG):
            self.getParameterByName(name).setVisible(self.real)
        super().realChanged()


class SWController(DeviceController):
    """Controller for the swB switch. One esibd_bs SW instance, one COM port."""

    controllerParent: SW

    class SignalCommunicate(DeviceController.SignalCommunicate):
        """Bundle pyqtSignals."""

        configListsChangedSignal = pyqtSignal()
        """Signal that transfers the freshly enumerated NVM config list from the init thread to the Config dropdowns."""

    def __init__(self, controllerParent: SW) -> None:
        super().__init__(controllerParent=controllerParent)
        self.sw = None  # esibd_bs SW device instance
        self.telemetryThrottle = ChannelThrottle()
        self.hkPushTime = 0.0  # last housekeeping push (epoch s)
        self.configItems = None  # list of 'index: name' NVM config items (None = not enumerated yet / failed)
        self.signalComm.configListsChangedSignal.connect(self.updateConfigCombos)

    def initializeValues(self, reset: bool = False) -> None:  # noqa: ARG002
        """Initialize values array: one entry per channel for the frequency readback."""
        channels = self.controllerParent.getChannels()
        if channels:
            self.values = np.full(len(channels), fill_value=np.nan, dtype=np.float32)

    def _comPort(self) -> int:
        """COM port from the first real channel (one switch, one port; fallback: the com_ports.json key)."""
        for channel in self.controllerParent.getChannels():
            if channel.real:
                return channel.com
        return getComPort('swB', default=10)

    def runInitialization(self) -> None:
        try:
            from devices.cgc import SW as SWDevice

            com = self._comPort()
            sink = getLabSink()
            if sink is None:
                self.print('Telemetry sink unavailable (LAB_CONFIG not set?) — running without telemetry.db writes.', flag=PRINT.DEBUG)
            self.print(f'Connecting to swB on COM{com}...')
            self.sw = SWDevice(device_id='swB', com=com, port=0, baudrate=230400, sink=sink, skip_sensors=(0,))
            if not self.sw.connect():
                self.print(f'Failed to connect to swB on COM{com}.', flag=PRINT.ERROR)
                self.sw = None
                return
            self.print(f'swB on COM{com} connected.')
            realChannels = [channel for channel in self.controllerParent.getChannels() if channel.real]
            if len(realChannels) > 1:
                self.print('More than one real channel configured — swB has ONE oscillator; every row drives the same frequency '
                           'and the first row wins for the Config selection.', flag=PRINT.WARNING)
            if any(channel.com != com for channel in realChannels):
                self.print(f'Real channels disagree on the COM port — using COM{com} from the first real row.', flag=PRINT.WARNING)
            self.configItems = self._enumerateConfigs(self.sw)
            # emitted before initCompleteSignal so the dropdown is refreshed before initComplete can trigger the On bring-up
            self.signalComm.configListsChangedSignal.emit()

            if self.controllerParent.isOn():
                with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock for the initial bring-up.') as lock_acquired:
                    if lock_acquired:
                        self._bringUp()
                time.sleep(0.2)

            self.signalComm.initCompleteSignal.emit()
        except Exception as e:  # noqa: BLE001
            self.print(f'Error initializing swB: {e}', flag=PRINT.ERROR)
            self.sw = None
        finally:
            self.initializing = False

    def fakeInitialization(self) -> None:
        """Create a simulated esibd_bs device before faking init, so Test Mode telemetry lands in the shared db with sim=1."""
        self.sw = None
        try:
            from devices.cgc import SW as SWDevice

            sink = getLabSink()
            if sink is not None:
                self.sw = SWDevice(device_id='swB', com=self._comPort(), port=0, baudrate=230400, sink=sink,
                                   test_mode=True, skip_sensors=(0,))
                self.sw.connect()
        except Exception as e:  # noqa: BLE001
            self.print(f'Test Mode runs without telemetry device: {e}', flag=PRINT.DEBUG)
        # canned campaign ladder — the sim never loads the vendor DLL, so real NVM enumeration is impossible
        self.configItems = list(TESTMODE_CONFIG_ITEMS)
        self.signalComm.configListsChangedSignal.emit()
        super().fakeInitialization()

    def _enumerateConfigs(self, sw) -> 'list[str] | None':
        """Read the populated NVM config slots (index + name) of the switch.

        Returns None on failure so the dropdown keeps its last known list. Commas in
        device-stored names are replaced (the COMBO items string is comma-separated).
        """
        try:
            status, _active, valid = sw.call_with_retry(sw.get_config_list)
            if status != sw.NO_ERR:
                self.print(f'get_config_list failed (status {status}) — keeping the last known config list.', flag=PRINT.WARNING)
                return None
            items = []
            for index, is_valid in enumerate(valid):
                if not is_valid:
                    continue
                # retry net on every name read too — one corrupted exchange in this
                # 100+-query loop would otherwise desync the wire for good (EMI, -13)
                name_status, name = sw.call_with_retry(sw.get_config_name, index)
                items.append(f'{index}: {name.replace(",", ";")}' if name_status == sw.NO_ERR and name else f'{index}: <unnamed>')
        except Exception as e:  # noqa: BLE001
            self.print(f'NVM enumeration failed: {e}', flag=PRINT.WARNING)
            return None
        return items or None

    def updateConfigCombos(self) -> None:
        """Replace the Config dropdown items with the freshly enumerated NVM list (runs in the main thread).

        Selections are re-matched by config index (a renamed slot updates silently); a
        vanished index falls back to the baseline with a warning. Real enumerations
        refresh the on-disk cache that seeds the dropdown at the next start.
        """
        items = self.configItems
        if not items:
            return
        if not getTestMode():
            cache = loadConfigCache()
            cache[str(self._comPort())] = items
            saveConfigCache(cache)
        device = self.controllerParent
        device.syncingConfig = True
        try:
            for channel in device.getChannels():
                if not channel.real:
                    continue
                parameter = channel.getParameterByName(RFChannel.CONFIG)
                previous = str(parameter.value)
                previousIndex = configIndex(previous)
                parameter.combo.blockSignals(True)
                parameter.combo.clear()
                for item in items:
                    parameter.combo.insertItem(parameter.combo.count(), item)
                match = next((k for k, item in enumerate(items) if configIndex(item) == previousIndex), -1)
                if match == -1:
                    match = next((k for k, item in enumerate(items) if configIndex(item) == BASELINE_CONFIG), 0)
                    self.print(f"{channel.name}: stored config '{previous}' not found in the switch NVM — "
                               f"falling back to '{items[match]}'.", flag=PRINT.WARNING)
                parameter.combo.setCurrentIndex(match)
                parameter.combo.blockSignals(False)
        finally:
            device.syncingConfig = False

    def _selectedConfig(self) -> int:
        """Return the selected config index (first real row; fallback: campaign baseline)."""
        for channel in self.controllerParent.getChannels():
            if channel.real:
                index = configIndex(channel.configuration)
                if index is not None:
                    return index
                break
        return BASELINE_CONFIG

    def applyConfigFromThread(self) -> None:
        """Load the selected config and re-apply the channel values (thread safe)."""
        if not getTestMode() and self.initialized:
            threading.Thread(target=self.applyConfig, name=f'{self.controllerParent.name} applyConfigThread', daemon=True).start()

    def applyConfig(self) -> None:
        """Live config change — same sequence as the On bring-up (load, then re-apply frequency/duty on top)."""
        with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock to load the switch config.') as lock_acquired:
            if not lock_acquired:
                return
            self._bringUp()
        time.sleep(0.2)
        for channel in self.controllerParent.getChannels():
            if channel.real:
                self.applyValueFromThread(channel)

    def _bringUp(self) -> None:
        """Campaign bring-up: load the selected NVM config — the FULL working set (device/
        oscillator/pulser enables + trigger routing); bare enables arm nothing. Caller
        must hold the controller lock. The channel frequency/duty is re-applied by the caller."""
        if self.sw is None:
            return
        config = self._selectedConfig()
        status = self.sw.call_with_retry(self.sw.load_current_config, config)
        if status != self.sw.NO_ERR:
            self.print(f'Failed to load config {config} on the switch: status {status}', flag=PRINT.ERROR)
            return
        self.print(f'Loaded config {config} on the switch.')
        self.hkPushTime = 0.0  # next read cycle pushes the new enable state to telemetry right away

    def _standby(self) -> None:
        """Campaign teardown: park the switch in standby config 0 (loads the all-off working set)."""
        if self.sw is None:
            return
        status = self.sw.call_with_retry(self.sw.load_current_config, STANDBY_CONFIG)
        if status != self.sw.NO_ERR:
            self.print(f'PARK NOT CONFIRMED: standby config load returned {status} — verify at the bench.', flag=PRINT.ERROR)
        self.hkPushTime = 0.0

    def _pushTelemetry(self) -> None:
        """Write the freshly read frequency to the telemetry sink, throttled per channel (>=5 s)."""
        if self.sw is None or self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if not (channel.enabled and channel.real and i < len(self.values)):
                continue
            value = float(self.values[i])
            if not np.isfinite(value) or not self.telemetryThrottle.ready(channel.name):
                continue
            self.sw.log_sample(channel.name, value, 'kHz')
        self._pushHousekeeping()

    def _pushHousekeeping(self, force: bool = False) -> None:
        """Push electronics housekeeping (internal temps + device enable) to the telemetry sink every HK_PUSH_INTERVAL_S.

        Rides the read loop (which already holds the controller lock) — never a second polling thread on the DLL,
        and never the esibd_bs hk worker. DB-only: these are not Explorer channels; the dashboard's Electronics tab
        reads them. force=True skips the time gate (final push on closeCommunication; caller must hold the lock).
        """
        if self.sw is None or (not force and time.time() - self.hkPushTime < HK_PUSH_INTERVAL_S):
            return
        self.hkPushTime = time.time()
        try:
            status, _volt_12v, _volt_5v0, _volt_3v3, temp_cpu = self.sw.get_housekeeping()
            if status == self.sw.NO_ERR:
                self.sw.log_sample('Temp_CPU', temp_cpu, 'degC', '.1f')
            status, temp0, temp1, temp2 = self.sw.get_sensor_data()
            if status == self.sw.NO_ERR:
                for i, temp in enumerate((temp0, temp1, temp2)):
                    if i in self.sw.skip_sensors:  # swB sensor 0 is broken
                        continue
                    self.sw.log_sample(f'Temp_Sensor{i}', temp, 'degC', '.1f')
            status, enabled = self.sw.get_device_enable()
            if status == self.sw.NO_ERR:
                self.sw.log_sample('Device_Enabled', 1 if enabled else 0)
        except Exception as e:  # noqa: BLE001
            self.print(f'Housekeeping push failed: {e}', flag=PRINT.DEBUG)

    def applyValue(self, channel: RFChannel) -> None:
        if self.sw is None or not (channel.enabled and self.controllerParent.isOn()):
            return  # parked = standby config; there is no '0 kHz' to write (contrast: PSU writes 0 V)
        frequency = max(1.0, min(float(channel.value), F_MAX_KHZ))
        with self.lock.acquire_timeout(1, timeoutMessage=f'Cannot acquire lock to set {channel.name}.') as lock_acquired:
            if lock_acquired:
                # set_rf validates (monoflop, stuck-HIGH) and runs the width re-fit under the
                # device's own thread_lock with purge-retry; the controller lock and the device
                # lock are independent, so holding both here is safe.
                status = self.sw.set_rf(frequency, duty=channel.duty / 100.0, pulser=channel.pulser)
                if status != self.sw.NO_ERR:
                    self.print(f'Error setting {channel.name} to {frequency:g} kHz at {channel.duty:g} percent duty: status {status}', flag=PRINT.WARNING)
                    self.errorCount += 1
                else:
                    self.print(f'Set {channel.name} to {frequency:g} kHz at {channel.duty:g} percent duty (pulser {channel.pulser})', flag=PRINT.TRACE)

    def readNumbers(self) -> None:
        """Read the oscillator period and derive the frequency readback for every real channel (one oscillator)."""
        if self.sw is None or self.values is None:
            return
        try:
            status, period = self.sw.call_with_retry(self.sw.get_oscillator_period)
            if status == self.sw.NO_ERR:
                # Standby (config 0) zeroes the period register — the device answered fine,
                # there is just no oscillation to report (real swB, 2026-07-07). NEVER an error:
                # a parked switch otherwise counts to 25 and the framework closes communication.
                frequency_khz = self.sw.CLOCK / (period + self.sw.OSC_OFFSET) / 1000.0 if period > 0 else np.nan
                self.errorCount = 0
            else:
                frequency_khz = np.nan
                self.errorCount += 1
                self.print(f'get_oscillator_period failed: status {status}', flag=PRINT.WARNING)
            for i, channel in enumerate(self.controllerParent.getChannels()):
                if channel.enabled and channel.real and i < len(self.values):
                    self.values[i] = frequency_khz
        except Exception as e:  # noqa: BLE001
            self.print(f'Error reading the oscillator period: {e}', flag=PRINT.ERROR)
            self.errorCount += 1
        self._pushTelemetry()

    def fakeNumbers(self) -> None:
        if self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real:
                if self.controllerParent.isOn():
                    self.values[i] = channel.value + self.rng.random() * 0.2 - 0.1
                else:
                    self.values[i] = 1.0 + self.rng.random() * 0.01  # standby: sim oscillator idles at its 1 kHz default
        self._pushTelemetry()

    def updateValues(self) -> None:
        if self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real and i < len(self.values):
                channel.monitor = np.nan if channel.waitToStabilize else self.values[i]

    def toggleOn(self) -> None:
        super().toggleOn()
        if self.sw is None:
            return
        on = self.controllerParent.isOn()
        try:
            # Every DLL call must hold the controller lock — an unlocked call garbles the
            # in-flight exchange of the locked read/set threads (-13 storms, 2026-07-05/06).
            with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock to toggle the switch.') as lock_acquired:
                if not lock_acquired:
                    return
                if on:
                    self._bringUp()
                else:
                    self._standby()
        except Exception as e:  # noqa: BLE001
            self.print(f'Error toggling the switch: {e}', flag=PRINT.ERROR)
        if on:
            # Give the device a moment to settle before pushing the channel frequency over the config values.
            time.sleep(0.2)
            for channel in self.controllerParent.getChannels():
                if channel.real:
                    self.applyValueFromThread(channel)

    def purgeUnit(self) -> None:
        """Manual COM-buffer purge (recovery from EMI serial hiccups under HV switching; user button)."""
        def purge() -> None:
            with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock to purge the switch port.') as lock_acquired:
                if not lock_acquired:
                    return
                try:
                    if self.sw.test_mode:
                        self.print('Purge skipped (Test Mode).')
                    else:
                        self.sw.purge()
                        self.print('Switch COM buffers purged.')
                except Exception as e:  # noqa: BLE001
                    self.print(f'Purge failed: {e}', flag=PRINT.WARNING)
        if self.sw is not None:
            threading.Thread(target=purge, name=f'{self.controllerParent.name} purgeThread', daemon=True).start()
        else:
            self.print('Switch not connected.', flag=PRINT.WARNING)

    def listConfigs(self) -> None:
        """List the populated NVM config slots (python index + device-stored name) in the Console and refresh the Config dropdown."""
        def enumerate_configs() -> None:
            if self.sw.test_mode:
                self.print('NVM enumeration needs real hardware (Test Mode active). '
                           'Campaign ladder: 89 = SwitchSym 1 MHz baseline, 0 = standby, 39-79 = 1-500 kHz rungs.')
                return
            with self.lock.acquire_timeout(5, timeoutMessage='Cannot acquire lock to read the switch configs.') as lock_acquired:
                if not lock_acquired:
                    return
                items = self._enumerateConfigs(self.sw)
            if items is None:
                return
            for item in items:
                self.print(f'config {item}')
            self.print(f'{len(items)} populated NVM config slots.')
            self.configItems = items
            self.signalComm.configListsChangedSignal.emit()
        if self.sw is not None:
            threading.Thread(target=enumerate_configs, name=f'{self.controllerParent.name} configListThread', daemon=True).start()
        else:
            self.print('Switch not connected.', flag=PRINT.WARNING)

    def closeCommunication(self) -> None:
        super().closeCommunication()  # stops acquisition first
        if self.sw is not None:
            with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock to close the switch.'):
                try:
                    self._standby()
                except Exception:  # noqa: BLE001
                    pass
                # final hk push so telemetry records the parked state
                # (read loop stopped; dashboard staleness covers a crash)
                self._pushHousekeeping(force=True)
                try:
                    self.sw.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            self.sw = None
        self.initialized = False
