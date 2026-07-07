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
    return [PSU]


HK_PUSH_INTERVAL_S = 30  # electronics housekeeping (temps, enable states) cadence; voltage telemetry stays on ChannelThrottle
P_LIMIT_W = 100.0  # CGC dissipation limit per switch channel (campaign 2026-07-06: swB hit 98.8 W at the envelope top)
V_MAX = 350.0  # absolute output ceiling (HV-PSU-CTRL-2D full range)
UNIT_KEYS = ('PSU1', 'PSU2', 'PSU3', 'PSU4')  # canonical com_ports keys; dll device index = key number - 1 (notebook 025)
# Which switch each supply feeds (notebook 023 wiring map): plugin name -> the Explorer plugin that arms that switch.
# CGC bring-up order is binding: switch armed FIRST, then PSU — an unarmed switch turns the rail into DC on the chain.
SWITCH_PLUGIN_FOR_UNIT = {'PSU1': ('SW', 'swB'), 'PSU2': ('SW', 'swB'), 'PSU3': ('SWHR', 'swA'), 'PSU4': ('SWHR', 'swA')}
BASELINE_CONFIG = 63  # campaign baseline NVM slot: 10 V / 100 mA, all enables on
STANDBY_CONFIG = 0  # park slot: everything off
CONFIG_CACHE_FILE = 'psu_nvm_configs.json'  # last known NVM lists per COM, seeds the Config dropdowns before enumeration
TESTMODE_CONFIG_ITEMS = ('0: STANDBY', '19: LADDER_0V', '54: LADDER_350V', '63: BASELINE_10V_100MA')  # canned campaign slots for Test Mode


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
    """Persist the NVM config lists so the next session's dropdowns seed with real items."""
    try:
        (getValidConfigPath() / CONFIG_CACHE_FILE).write_text(json.dumps(cache, indent=2), encoding='utf-8')
    except OSError:
        pass


def mergedConfigItems() -> tuple[list[str], str]:
    """Return the Config dropdown seed (items, default item).

    Union of all cached per-unit lists, deduplicated by index, plus bare fallbacks.
    The union guarantees that any saved channel selection finds its item at channel-file
    load time (a COMBO value missing from the items resets to item 0); the fresh
    per-unit enumeration replaces the list right after the units connect.
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


class PSU(Device):
    """Contains a list of output channels of the four CGC HV-PSU-CTRL-2D supplies feeding the RF switches.

    Each supply has two outputs (POS/NEG) that form the rails of one RF chain:
    psu2 -> Q1, psu1 -> Ion Funnels (both via swB), psu3 -> Q2/QMS, psu4 -> Q3/4 (via swA).
    Channel values set the output voltage (V); the PLOTTED/RECORDED channel data is the
    measured output current in mA — the physically interesting signal for RF chains
    (current grows with switching frequency; ESI plugin precedent). The voltage readback
    stays as the monitor (deviation warning).

    On loads each unit's selected NVM config (Config column, mirrored across the two
    rows of one supply; fresh channels default to 63 = 10 V / 100 mA, all enables on —
    campaign-proven bring-up; bare enables arm nothing) and then re-applies every
    channel's voltage + current limit; Off parks the units in standby config 0.
    Selecting a config while On loads it immediately, sets the enables from the E
    checkboxes and re-applies the channel values — the channel table stays the voltage
    truth (design choice 2026-07-07, see the KB cgc-psu page for the alternatives).
    The dropdown lists 'index: name' read from the device NVM at every initialization
    (cached between sessions so saved selections survive the restart).
    A soft watchdog on the readback enforces the campaign limits (per-channel I_lim,
    100 W): first breach steps the setpoint down 10 percent, a second consecutive breach
    disables both outputs of that supply.

    Switch interlock (CGC bring-up order, binding): On is refused unless every switch
    that a checked channel feeds is armed first — psu1/psu2 need swB (SW plugin),
    psu3/psu4 need swA (SWHR plugin): plugin loaded, initialized, On, and not parked
    in standby (the switch plugins' isRunning API). Unchecked chains impose no
    requirement. Energizing a rail into an unarmed switch puts DC on the chain.
    Teardown is the mirror: PSUs off first, then the switches.

    Recommended channel set (build once, saved with your config) — 8 real channels:
    IF_POS/IF_NEG (COM 15, I_lim 150), Q1_POS/Q1_NEG (COM 16, I_lim 300),
    QMS_POS/QMS_NEG (COM 17, I_lim 150), Q34_POS/Q34_NEG (COM 18, I_lim 150);
    plus one VIRTUAL amplitude knob per chain (IF_Amp, Q1_Amp, QMS_Amp, Q34_Amp —
    uncheck R): give each real channel the equation of its chain knob (e.g. 'IF_Amp'
    on IF_POS and IF_NEG, then uncheck A) so one value drives both rails. Asymmetric
    rails later = edit the equations (e.g. 'IF_Amp + IF_Offset') — no code needed.

    Never run this plugin and a PSU notebook at the same time — same COM ports.
    """

    name = 'PSU'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = 'mA'  # unit of the plotted/recorded channel data (measured current); set values are volts (see PSUChannel headers)
    iconFile = 'PSU.png'
    useMonitors = True
    useOnOffLogic = True
    channels: 'list[PSUChannel]'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = PSUChannel
        self.syncingConfig = False  # guards the Config-mirroring event against recursion and programmatic updates

    def initGUI(self) -> None:
        super().initGUI()
        self.addAction(event=lambda: self.controller.purgeUnits(), toolTip=f'Purge the COM buffers of all connected {self.name} units.', icon='purge.png')
        self.addAction(event=lambda: self.controller.listConfigs(), toolTip='List the NVM config slots (index + name) of all connected units in the Console.',
                       icon='configs.png')
        self.controller = PSUController(controllerParent=self)

    def getChannels(self) -> 'list[PSUChannel]':
        return cast('list[PSUChannel]', super().getChannels())

    def getDefaultSettings(self) -> dict[str, dict]:
        settings = super().getDefaultSettings()
        settings[f'{self.name}/Interval'][Parameter.VALUE] = 1000
        settings[f'{self.name}/{self.MAXDATAPOINTS}'][Parameter.VALUE] = 1E5
        return settings

    def onConfigSelected(self, channel: 'PSUChannel') -> None:
        """Mirror the Config selection to all rows of the same unit; load it right away if On.

        Design choice 2026-07-07 (Option A): live-select performs load_current_config,
        then enables from the E checkboxes, then re-applies channel V + I_lim — the
        channel table stays the voltage truth. While Off the selection is stored only.
        """
        if self.syncingConfig or not channel.real:
            return
        self.syncingConfig = True
        try:
            for sibling in self.getChannels():
                if sibling is not channel and sibling.real and sibling.com == channel.com and sibling.configuration != channel.configuration:
                    sibling.getParameterByName(PSUChannel.CONFIG).value = channel.configuration
        finally:
            self.syncingConfig = False
        if self.isOn():
            self.controller.applyConfigFromThread(channel.com)

    def requiredSwitchPlugins(self) -> dict[str, str]:
        """Return {switch plugin name: switch id} for every unit with a real, checked channel.

        Only chains that On would actually energize impose a requirement — an unchecked
        channel's output enable stays off, so its switch does not have to be armed.
        """
        unitByCom = {getComPort(key, default=-1): key for key in UNIT_KEYS}
        required = {}
        for channel in self.getChannels():
            if channel.real and channel.enabled:
                unit = unitByCom.get(channel.com)
                if unit in SWITCH_PLUGIN_FOR_UNIT:
                    pluginName, switchId = SWITCH_PLUGIN_FOR_UNIT[unit]
                    required[pluginName] = switchId
        return required

    def switchInterlockBlockers(self) -> list[str]:
        """Return one human-readable line per required switch that is NOT armed (empty = clear to enable)."""
        blockers = []
        for pluginName, switchId in sorted(self.requiredSwitchPlugins().items()):
            plugin = next((plugin for plugin in self.pluginManager.plugins if plugin.name == pluginName), None)
            if plugin is None:
                blockers.append(f'{switchId} ({pluginName} plugin) is not loaded — enable the plugin or uncheck its channels')
            elif not (getattr(plugin, 'controller', None) and plugin.controller.initialized):
                blockers.append(f'{switchId} ({pluginName} plugin) communication is not initialized')
            elif not plugin.isOn():
                blockers.append(f'{switchId} ({pluginName} plugin) is not enabled — toggle it On first')
            elif hasattr(plugin, 'isRunning') and not plugin.isRunning():
                blockers.append(f'{switchId} ({pluginName} plugin) is parked in standby — load an RF config first')
        return blockers

    def setOn(self, on: 'bool | None' = None) -> None:
        """Refuse to enable the PSUs unless every switch their checked channels feed is armed (CGC bring-up order)."""
        requested = on if on is not None else self.isOn()
        if requested:
            blockers = self.switchInterlockBlockers()
            if blockers:
                if self.isOn():
                    self.onAction.state = False  # revert the click; setChecked fires no event
                self.print('PSU enable blocked (CGC order: switch first, then PSU — an unarmed switch turns the rail into DC on the chain): '
                           + '; '.join(blockers) + '.', flag=PRINT.ERROR)
                return
        super().setOn(on)

    def getCOMs(self) -> list[int]:
        """Get list of unique COM port numbers used by real channels."""
        return list({channel.com for channel in self.channels if channel.real})

    def closeCommunication(self) -> None:
        self.setOn(False)
        self.controller.toggleOnFromThread(parallel=False)
        super().closeCommunication()


class PSUChannel(Channel):
    """Channel for a single HV-PSU-CTRL-2D output (one rail of one RF chain)."""

    COM = 'COM'
    POLARITY = 'Out'
    ILIM = 'I_lim'
    CURRENT = 'Current'
    POWER = 'Power'
    CONFIG = 'Config'
    channelParent: PSU

    def getDefaultChannel(self) -> dict[str, dict]:

        self.com: int
        self.polarity: str
        self.ilim: float
        self.current: float
        self.power: float
        self.configuration: str

        configItems, configDefault = mergedConfigItems()
        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'Voltage (V)'
        channel[self.MONITOR][Parameter.HEADER] = 'Measured U (V)'  # voltage readback; device unit is mA (plotted currents)
        channel[self.COM] = parameterDict(value=getComPort('PSU1', default=15), minimum=1, maximum=99, parameterType=PARAMETERTYPE.INT, advanced=True,
                                          header='COM', toolTip='COM port number of the supply (PSU1-4 = 15-18).', attr='com')
        channel[self.POLARITY] = parameterDict(value='POS', parameterType=PARAMETERTYPE.COMBO, items='POS, NEG', advanced=False, header='Out',
                                               toolTip='Which of the two outputs of the supply: POS (psu 0) or NEG (psu 1).', attr='polarity')
        channel[self.ILIM] = parameterDict(value=150.0, minimum=0.0, maximum=300.0, parameterType=PARAMETERTYPE.FLOAT, advanced=False, header='I_lim (mA)',
                                           toolTip='Hardware current limit AND soft-watchdog threshold (campaign: 300 mA for Q1, 150 mA elsewhere).\n'
                                                   'Applied together with every voltage set and on every On.', attr='ilim')
        channel[self.CURRENT] = parameterDict(value=0.0, parameterType=PARAMETERTYPE.FLOAT, advanced=False, header='I (mA)', indicator=True, attr='current',
                                              toolTip='Measured output current (read-only). This is what the plot and the recorded data show.')
        channel[self.POWER] = parameterDict(value=0.0, parameterType=PARAMETERTYPE.FLOAT, advanced=False, header='P (W)', indicator=True, attr='power',
                                            toolTip='Measured output power V*I (read-only). CGC limit: < 100 W per switch channel.')
        channel[self.CONFIG] = parameterDict(value=configDefault, parameterType=PARAMETERTYPE.COMBO, items=', '.join(configItems), fixedItems=True,
                                             advanced=False, header='Config', attr='configuration', event=self.configChanged,
                                             toolTip='NVM config of this supply (index: name), mirrored between its POS and NEG row.\n'
                                                     'Loaded on On (then the channel voltages + I_lims are applied on top); selecting while On '
                                                     'loads it immediately the same way. List refreshes from the device NVM at every initialization.')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.insertDisplayedParameter(self.CONFIG, before=self.DISPLAY)
        self.insertDisplayedParameter(self.ILIM, before=self.DISPLAY)
        self.insertDisplayedParameter(self.CURRENT, before=self.DISPLAY)
        self.insertDisplayedParameter(self.POWER, before=self.DISPLAY)
        self.displayedParameters.append(self.POLARITY)
        self.displayedParameters.append(self.COM)

    def configChanged(self) -> None:
        """Hand the user's Config selection to the device for mirroring and (if On) live-apply."""
        self.channelParent.onConfigSelected(self)

    def tempParameters(self) -> list[str]:
        return [*super().tempParameters(), self.CURRENT, self.POWER]

    @property
    def psu_num(self) -> int:
        """DLL output index: POS = 0, NEG = 1."""
        return 0 if self.polarity == 'POS' else 1

    def appendValue(self, lenT: int, nan: bool = False) -> None:
        """Append the measured output current (mA) as the channel data.

        The chain currents are the signal of interest for RF operation (I grows with
        frequency; the campaign envelope was current-limited) — plotted and recorded
        instead of the voltage readback (ESI plugin precedent). The voltage readback
        remains the monitor and keeps driving the deviation warning. NaN markers and
        virtual channels fall through to the base class.
        """
        if not nan and self.enabled and self.real:
            self.values.add(x=self.current, lenT=lenT)
            if self.useBackgrounds:
                self.backgrounds.add(x=self.background, lenT=lenT)
            for parameter in self.getRecordedParameters():
                if isinstance(parameter.value, (float, int)):
                    parameter.values.add(x=parameter.value, lenT=lenT)
        else:
            super().appendValue(lenT, nan=nan)

    def monitorChanged(self) -> None:
        self.updateWarningState(self.enabled and self.channelParent.controller.acquiring
                                and ((self.channelParent.isOn() and abs(self.monitor - self.value) > 2)
                                or (not self.channelParent.isOn() and abs(self.monitor - 0) > 2)))

    def realChanged(self) -> None:
        for name in (self.COM, self.POLARITY, self.ILIM, self.CURRENT, self.POWER, self.CONFIG):
            self.getParameterByName(name).setVisible(self.real)
        super().realChanged()


class PSUController(DeviceController):
    """Controller for the four HV-PSU-CTRL-2D units. Manages one esibd_bs PSU instance per unique COM port."""

    controllerParent: PSU

    class SignalCommunicate(DeviceController.SignalCommunicate):
        """Bundle pyqtSignals."""

        configListsChangedSignal = pyqtSignal()
        """Signal that transfers freshly enumerated NVM config lists from the init thread to the Config dropdowns."""

    def __init__(self, controllerParent: PSU) -> None:
        super().__init__(controllerParent=controllerParent)
        self.psus = {}  # COM port -> esibd_bs PSU instance
        self.currents = None  # measured output currents (mA), parallel to values
        self.powers = None  # measured output powers (W), parallel to values
        self.telemetryThrottle = ChannelThrottle()
        self.hkPushTimes = {}  # COM port -> last housekeeping push (epoch s)
        self.breachCounts = {}  # (COM, psu_num) -> consecutive soft-watchdog breaches
        self.configLists = {}  # COM port -> list of 'index: name' NVM config items (None = enumeration failed)
        self.signalComm.configListsChangedSignal.connect(self.updateConfigCombos)
        self.initCOMs()

    def _unitFor(self, com: int) -> tuple[str, int]:
        """Canonical (device_id, dll device index) for a COM port.

        The four supplies share one loaded DLL; each export carries a device index.
        Notebook 025 mapping: PSU<n> -> index n-1. Unknown COMs get a stable fallback
        index beyond the known units so two unknowns can never collide with them.
        """
        known = {getComPort(key, default=-1): (key, i) for i, key in enumerate(UNIT_KEYS)}
        if com in known:
            return known[com]
        fallback_index = len(UNIT_KEYS) + sorted(c for c in self.COMs if c not in known).index(com)
        return f'PSU_COM{com}', fallback_index

    def initCOMs(self) -> None:
        """Initialize COM port list."""
        self.COMs = self.controllerParent.getCOMs() or [getComPort('PSU1', default=15)]

    def initializeValues(self, reset: bool = False) -> None:  # noqa: ARG002
        """Initialize values array: one entry per channel for monitor readback (+ current/power indicators)."""
        self.COMs = self.controllerParent.getCOMs() or [getComPort('PSU1', default=15)]
        channels = self.controllerParent.getChannels()
        if channels:
            self.values = np.full(len(channels), fill_value=np.nan, dtype=np.float32)
            self.currents = np.full(len(channels), fill_value=np.nan, dtype=np.float32)
            self.powers = np.full(len(channels), fill_value=np.nan, dtype=np.float32)

    def runInitialization(self) -> None:
        self.initCOMs()
        try:
            from devices.cgc import PSU as PSUDevice

            sink = getLabSink()
            if sink is None:
                self.print('Telemetry sink unavailable (LAB_CONFIG not set?) — running without telemetry.db writes.', flag=PRINT.DEBUG)
            self.psus = {}
            self.breachCounts = {}
            for com in self.COMs:
                device_id, dll_port = self._unitFor(com)
                self.print(f'Connecting to {device_id} on COM{com} (device index {dll_port})...')
                psu = PSUDevice(device_id=device_id, com=com, port=dll_port, baudrate=230400, sink=sink)
                if not psu.connect():
                    self.print(f'Failed to connect to {device_id} on COM{com}.', flag=PRINT.ERROR)
                    return
                self.psus[com] = psu
                self.print(f'{device_id} on COM{com} connected.')
                self.configLists[com] = self._enumerateConfigs(psu, com)

            # emitted before initCompleteSignal so the dropdowns are refreshed before initComplete can trigger the On bring-up
            self.signalComm.configListsChangedSignal.emit()

            if self.controllerParent.isOn():
                for com in self.psus:
                    self._bringUp(com)
                time.sleep(0.2)

            self.signalComm.initCompleteSignal.emit()
        except Exception as e:  # noqa: BLE001
            self.print(f'Error initializing PSU units: {e}', flag=PRINT.ERROR)
        finally:
            self.initializing = False

    def fakeInitialization(self) -> None:
        """Create simulated esibd_bs devices before faking init, so Test Mode telemetry lands in the shared db with sim=1."""
        self.initCOMs()
        self.psus = {}
        self.breachCounts = {}
        try:
            from devices.cgc import PSU as PSUDevice

            sink = getLabSink()
            if sink is not None:
                for com in self.COMs:
                    device_id, dll_port = self._unitFor(com)
                    psu = PSUDevice(device_id=device_id, com=com, port=dll_port, baudrate=230400, sink=sink, test_mode=True)
                    psu.connect()
                    self.psus[com] = psu
        except Exception as e:  # noqa: BLE001
            self.print(f'Test Mode runs without telemetry devices: {e}', flag=PRINT.DEBUG)
        # canned campaign slots — the sim never loads the vendor DLL, so real NVM enumeration is impossible
        self.configLists = {com: list(TESTMODE_CONFIG_ITEMS) for com in self.COMs}
        self.signalComm.configListsChangedSignal.emit()
        super().fakeInitialization()

    def _enumerateConfigs(self, psu, com: int) -> 'list[str] | None':
        """Read the populated NVM config slots (index + name) of one unit.

        Returns None on failure so the dropdown keeps its last known list. Commas in
        device-stored names are replaced (the COMBO items string is comma-separated).
        """
        try:
            status, _active, valid = psu.call_with_retry(psu.get_config_list)
            if status != psu.NO_ERR:
                self.print(f'COM{com}: get_config_list failed (status {status}) — keeping the last known config list.', flag=PRINT.WARNING)
                return None
            items = []
            for index, is_valid in enumerate(valid):
                if not is_valid:
                    continue
                name_status, name = psu.get_config_name(index)
                items.append(f'{index}: {name.replace(",", ";")}' if name_status == psu.NO_ERR and name else f'{index}: <unnamed>')
        except Exception as e:  # noqa: BLE001
            self.print(f'COM{com}: NVM enumeration failed: {e}', flag=PRINT.WARNING)
            return None
        return items or None

    def updateConfigCombos(self) -> None:
        """Replace the Config dropdown items with the freshly enumerated NVM lists (runs in the main thread).

        Selections are re-matched by config index (a renamed slot updates silently); a
        vanished index falls back to the baseline with a warning. Real enumerations
        refresh the on-disk cache that seeds the dropdowns at the next start.
        """
        fresh = {com: items for com, items in self.configLists.items() if items}
        if not fresh:
            return
        if not getTestMode():
            cache = loadConfigCache()
            cache.update({str(com): items for com, items in fresh.items()})
            saveConfigCache(cache)
        device = self.controllerParent
        device.syncingConfig = True
        try:
            for channel in device.getChannels():
                items = fresh.get(channel.com) if channel.real else None
                if not items:
                    continue
                parameter = channel.getParameterByName(PSUChannel.CONFIG)
                previous = str(parameter.value)
                previousIndex = configIndex(previous)
                parameter.combo.blockSignals(True)
                parameter.combo.clear()
                for item in items:
                    parameter.combo.insertItem(parameter.combo.count(), item)
                match = next((k for k, item in enumerate(items) if configIndex(item) == previousIndex), -1)
                if match == -1:
                    match = next((k for k, item in enumerate(items) if configIndex(item) == BASELINE_CONFIG), 0)
                    self.print(f"{channel.name}: stored config '{previous}' not found in the NVM of COM{channel.com} — "
                               f"falling back to '{items[match]}'.", flag=PRINT.WARNING)
                parameter.combo.setCurrentIndex(match)
                parameter.combo.blockSignals(False)
        finally:
            device.syncingConfig = False

    def _selectedConfig(self, com: int) -> int:
        """Return the config index selected for one unit (mirrored Config column; fallback: campaign baseline)."""
        for channel in self.controllerParent.getChannels():
            if channel.real and channel.com == com:
                index = configIndex(channel.configuration)
                if index is not None:
                    return index
                break
        return BASELINE_CONFIG

    def applyConfigFromThread(self, com: int) -> None:
        """Load the selected config on one unit and re-apply its channel values (thread safe)."""
        if not getTestMode() and self.initialized:
            threading.Thread(target=self.applyConfig, args=(com,), name=f'{self.controllerParent.name} applyConfigThread', daemon=True).start()

    def applyConfig(self, com: int) -> None:
        """Live config change for one unit — same sequence as the On bring-up.

        Design choice 2026-07-07 (Option A): load_current_config, enables from the E
        checkboxes, then re-apply every channel's V + I_lim — the channel table stays
        the voltage truth.
        """
        with self.lock.acquire_timeout(2, timeoutMessage=f'Cannot acquire lock to load config on COM{com}.') as lock_acquired:
            if not lock_acquired:
                return
            self._bringUp(com)
        time.sleep(0.2)
        for channel in self.controllerParent.getChannels():
            if channel.real and channel.com == com:
                self.applyValueFromThread(channel)

    def _bringUp(self, com: int) -> None:
        """Campaign bring-up for one unit: load its selected NVM config (the full working
        set incl. enables — bare enables arm nothing), then reflect the channel E
        checkboxes in the per-output enables. Caller must hold the controller lock or
        run before acquisition starts. Channel voltages/I_lims are re-applied by the caller."""
        psu = self.psus.get(com)
        if psu is None:
            return
        config = self._selectedConfig(com)
        status = psu.call_with_retry(psu.load_current_config, config)
        if status != psu.NO_ERR:
            self.print(f'Failed to load config {config} on COM{com}: status {status}', flag=PRINT.ERROR)
            return
        self.print(f'Loaded config {config} on COM{com}.')
        enables = {0: False, 1: False}
        for channel in self.controllerParent.getChannels():
            if channel.real and channel.com == com:
                enables[channel.psu_num] = enables[channel.psu_num] or channel.enabled
        status = psu.call_with_retry(psu.set_psu_enable, enables[0], enables[1])
        if status != psu.NO_ERR:
            self.print(f'Failed to set output enables on COM{com}: status {status}', flag=PRINT.WARNING)
        self.hkPushTimes[com] = 0.0  # next read cycle pushes the new enable state to telemetry right away

    def _standby(self, com: int) -> None:
        """Campaign teardown for one unit: outputs to 0 V, then park in standby config 0."""
        psu = self.psus.get(com)
        if psu is None:
            return
        for psu_num in (0, 1):
            psu.call_with_retry(psu.set_psu_output_voltage, psu_num, 0)
        status = psu.call_with_retry(psu.load_current_config, 0)
        if status != psu.NO_ERR:
            self.print(f'PARK NOT CONFIRMED on COM{com}: standby config load returned {status} — verify at the bench.', flag=PRINT.ERROR)
        self.hkPushTimes[com] = 0.0

    def _pushTelemetry(self) -> None:
        """Write the freshly read monitor voltages to the telemetry sink, throttled per channel (>=5 s)."""
        if not self.psus or self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if not (channel.enabled and channel.real and i < len(self.values)):
                continue
            value = float(self.values[i])
            psu = self.psus.get(channel.com)
            if psu is None or not np.isfinite(value) or not self.telemetryThrottle.ready(channel.name):
                continue
            # explicit 'V': self.values are voltage readbacks; the device unit is 'mA' (plotted currents)
            psu.log_sample(channel.name, value, 'V')
        self._pushHousekeeping()

    def _pushHousekeeping(self) -> None:
        """Push electronics housekeeping (internal temps + enable states) to the telemetry sink every HK_PUSH_INTERVAL_S.

        Rides the read loop (which already holds the controller lock) — never a second polling thread on the DLL.
        DB-only: these are not Explorer channels and never appear in the GUI; the dashboard's Electronics tab reads them.
        """
        now = time.time()
        for com, psu in self.psus.items():
            if now - self.hkPushTimes.get(com, 0.0) < HK_PUSH_INTERVAL_S:
                continue
            self.hkPushTimes[com] = now
            self._pushHousekeepingUnit(com, psu)

    def _pushHousekeepingUnit(self, com: int, psu) -> None:
        """One housekeeping read+push for one unit. Caller must hold the controller lock (or ride the locked read loop)."""
        try:
            status, _volt_rect, _volt_5v0, _volt_3v3, temp_cpu = psu.get_housekeeping()
            if status == psu.NO_ERR:
                psu.log_sample('Temp_CPU', temp_cpu, 'degC', '.1f')
            status, temp0, temp1, temp2 = psu.get_sensor_data()
            if status == psu.NO_ERR:
                for i, temp in enumerate((temp0, temp1, temp2)):
                    psu.log_sample(f'Temp_Sensor{i}', temp, 'degC', '.1f')
            status, psu0, psu1 = psu.get_psu_enable()
            if status == psu.NO_ERR:
                psu.log_sample('PSU0_Enabled', 1 if psu0 else 0)
                psu.log_sample('PSU1_Enabled', 1 if psu1 else 0)
            status, enabled = psu.get_device_enable()
            if status == psu.NO_ERR:
                psu.log_sample('Device_Enabled', 1 if enabled else 0)
        except Exception as e:  # noqa: BLE001
            self.print(f'Housekeeping push failed for COM{com}: {e}', flag=PRINT.DEBUG)

    def applyValue(self, channel: PSUChannel) -> None:
        psu = self.psus.get(channel.com)
        if psu is None:
            return
        voltage = channel.value if (channel.enabled and self.controllerParent.isOn()) else 0
        voltage = max(0.0, min(float(voltage), V_MAX))
        with self.lock.acquire_timeout(1, timeoutMessage=f'Cannot acquire lock to set {channel.name}.') as lock_acquired:
            if lock_acquired:
                # current limit rides along with every voltage set so an edited I_lim takes effect without a re-On
                ilim_status = psu.call_with_retry(psu.set_psu_output_current, channel.psu_num, channel.ilim)
                if ilim_status != psu.NO_ERR:
                    self.print(f'Error setting {channel.name} current limit: status {ilim_status}', flag=PRINT.WARNING)
                status = psu.call_with_retry(psu.set_psu_output_voltage, channel.psu_num, voltage)
                if status != psu.NO_ERR:
                    self.print(f'Error setting {channel.name}: status {status}', flag=PRINT.WARNING)
                    self.errorCount += 1
                else:
                    self.print(f'Set {channel.name} to {voltage:.3f} V (COM{channel.com} out {channel.polarity})', flag=PRINT.TRACE)

    def readNumbers(self) -> None:
        """Read measured V/I from every configured output; soft watchdog on the readback (campaign limits)."""
        if not self.psus or self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if not (channel.enabled and channel.real and i < len(self.values)):
                continue
            psu = self.psus.get(channel.com)
            if psu is None:
                continue
            try:
                result = psu.call_with_retry(psu.get_psu_data, channel.psu_num)
                status, voltage, current_a, _dropout = result
                if status == psu.NO_ERR:
                    self.values[i] = voltage
                    self.currents[i] = current_a * 1000.0  # A -> mA
                    self.powers[i] = voltage * current_a
                    self.errorCount = 0
                    self._checkLimits(channel, psu, voltage, current_a)
                else:
                    self.errorCount += 1
            except Exception as e:  # noqa: BLE001
                self.print(f'Error reading {channel.name}: {e}', flag=PRINT.ERROR)
                self.errorCount += 1
        self._pushTelemetry()

    def _checkLimits(self, channel: PSUChannel, psu, voltage: float, current_a: float) -> None:
        """Soft watchdog on one fresh readback (logic = campaign.PSUWatchdog, per-channel I_lim).

        First breach steps the output setpoint down 10 % (hardware only — the channel
        target stays, so the deviation warning flags the intervention); a second
        consecutive breach disables BOTH outputs of that supply. Caller already rides
        the locked read loop.
        """
        key = (channel.com, channel.psu_num)
        current_ma = current_a * 1000.0
        power_w = voltage * current_a
        if current_ma <= channel.ilim and power_w <= P_LIMIT_W:
            self.breachCounts[key] = 0
            return
        count = self.breachCounts.get(key, 0) + 1
        self.breachCounts[key] = count
        self.print(f'LIMIT BREACH on {channel.name}: {current_ma:.1f} mA / {power_w:.1f} W '
                   f'(limits {channel.ilim:.0f} mA / {P_LIMIT_W:.0f} W), consecutive #{count}', flag=PRINT.ERROR)
        if count == 1:
            status, v_set, _v_limit = psu.get_psu_set_output_voltage(channel.psu_num)
            if status == psu.NO_ERR:
                psu.call_with_retry(psu.set_psu_output_voltage, channel.psu_num, round(v_set * 0.9, 3))
                self.print(f'{channel.name}: setpoint stepped down 10 % to {v_set * 0.9:.1f} V', flag=PRINT.WARNING)
        else:
            psu.call_with_retry(psu.set_psu_enable, False, False)
            self.print(f'{channel.name}: second consecutive breach — BOTH outputs of COM{channel.com} disabled.', flag=PRINT.ERROR)

    def fakeNumbers(self) -> None:
        if self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real:
                if self.controllerParent.isOn():
                    self.values[i] = channel.value + self.rng.random() * 0.1 - 0.05
                    self.currents[i] = channel.value * 0.2 + self.rng.random() * 0.1  # sim load ~0.2 mA per volt
                else:
                    self.values[i] = self.rng.random() * 0.1 - 0.05
                    self.currents[i] = 0.0
                self.powers[i] = self.values[i] * self.currents[i] / 1000.0
        self._pushTelemetry()

    def updateValues(self) -> None:
        if self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real and i < len(self.values):
                channel.monitor = np.nan if channel.waitToStabilize else self.values[i]
                if self.currents is not None and np.isfinite(self.currents[i]):
                    channel.current = float(self.currents[i])
                if self.powers is not None and np.isfinite(self.powers[i]):
                    channel.power = float(self.powers[i])

    def toggleOn(self) -> None:
        super().toggleOn()
        on = self.controllerParent.isOn()
        for com in self.psus:
            try:
                # Every DLL call must hold the controller lock — an unlocked call garbles the
                # in-flight exchange of the locked read/set threads (-13 storms, 2026-07-05/06).
                with self.lock.acquire_timeout(2, timeoutMessage=f'Cannot acquire lock to toggle PSU on COM{com}.') as lock_acquired:
                    if not lock_acquired:
                        continue
                    if on:
                        self._bringUp(com)
                    else:
                        self._standby(com)
            except Exception as e:  # noqa: BLE001
                self.print(f'Error toggling PSU on COM{com}: {e}', flag=PRINT.ERROR)
        if on:
            # Give the devices a moment to settle before pushing channel targets over the baseline config values.
            time.sleep(0.2)
            for channel in self.controllerParent.getChannels():
                if channel.real:
                    self.applyValueFromThread(channel)

    def purgeUnits(self) -> None:
        """Manual COM-buffer purge on every connected unit (recovery from EMI serial hiccups; user button)."""
        def purge() -> None:
            for com, psu in self.psus.items():
                with self.lock.acquire_timeout(2, timeoutMessage=f'Cannot acquire lock to purge COM{com}.') as lock_acquired:
                    if not lock_acquired:
                        continue
                    try:
                        if psu.test_mode:
                            self.print(f'COM{com}: purge skipped (Test Mode).')
                        else:
                            psu.purge()
                            self.print(f'COM{com}: buffers purged.')
                    except Exception as e:  # noqa: BLE001
                        self.print(f'COM{com}: purge failed: {e}', flag=PRINT.WARNING)
        if self.psus:
            threading.Thread(target=purge, name=f'{self.controllerParent.name} purgeThread', daemon=True).start()
        else:
            self.print('No connected units to purge.', flag=PRINT.WARNING)

    def listConfigs(self) -> None:
        """List the populated NVM config slots (python index + device-stored name) of every unit in the Console and refresh the Config dropdowns."""
        def enumerate_configs() -> None:
            changed = False
            for com, psu in self.psus.items():
                if psu.test_mode:
                    self.print(f'COM{com}: NVM enumeration needs real hardware (Test Mode active). '
                               'Campaign slots: 63 = 10 V / 100 mA baseline, 0 = standby, 19-54 = 0-350 V ladder.')
                    continue
                with self.lock.acquire_timeout(5, timeoutMessage=f'Cannot acquire lock to read configs on COM{com}.') as lock_acquired:
                    if not lock_acquired:
                        continue
                    items = self._enumerateConfigs(psu, com)
                if items is None:
                    continue
                for item in items:
                    self.print(f'COM{com} config {item}')
                self.print(f'COM{com}: {len(items)} populated NVM config slots.')
                self.configLists[com] = items
                changed = True
            if changed:
                self.signalComm.configListsChangedSignal.emit()
        if self.psus:
            threading.Thread(target=enumerate_configs, name=f'{self.controllerParent.name} configListThread', daemon=True).start()
        else:
            self.print('No connected units.', flag=PRINT.WARNING)

    def closeCommunication(self) -> None:
        super().closeCommunication()  # stops acquisition first
        for com, psu in self.psus.items():
            with self.lock.acquire_timeout(2, timeoutMessage=f'Cannot acquire lock to close COM{com}.'):
                try:
                    self._standby(com)
                except Exception:  # noqa: BLE001
                    pass
                # final hk push so telemetry records the parked state
                # (the read loop is already stopped; dashboard staleness
                # covers a crashed Explorer, this covers a clean close)
                self._pushHousekeepingUnit(com, psu)
                try:
                    psu.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        self.psus = {}
        self.initialized = False
