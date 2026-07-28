# pylint: disable=[missing-module-docstring]  # see class docstrings
import json
import threading
import time
from typing import cast

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QSpinBox

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, getTestMode, getValidConfigPath, parameterDict
from esibd.devices.com_helper import getComPort
from esibd.devices.lab_telemetry import ChannelThrottle, getLabSink
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [ESI]


HK_PUSH_INTERVAL_S = 30  # electronics housekeeping (temps, activation state) cadence; voltage telemetry stays on ChannelThrottle
OFF_CONFIG = 0  # NVM park slot (DLL 0-based: 0 = 'Off' — device + all modules off); park target after init, on Enable-off and on close (user 2026-07-28)
STANDBY_CONFIG = 1  # Config-dropdown default/fallback slot ('Standby' — device off, HV modules enabled, output 0); NOT the park slot — a fallback
#                     to 'Off' would make Enable a silent no-op (the SW plugin's historical dropdown-resolved-to-standby bug)
NVM_SETTLE_S = 2  # NVM writes (save slot, set name) may keep the controller busy — no traffic during this window (SW precedent; skipped in Test Mode)
MAX_CONFIG_SLOT = 1022  # DLL MAX_CONFIG = 1023 slots, 0-based
CONFIG_NAME_MAX = 201  # DLL CONFIG_NAME_SIZE = 202 incl. the terminating NUL
HEATER_MAX_C = 175.0  # top of the shipped 'Heat 30..175deg' config ladder
HEATER_SAFE_TEMP_C = 50.0  # below this a manual target needs the reduced power limit first (overshoot, CGC 2026-07-21)
HEATER_SAFE_POWER_W = 20.0  # power limit applied before sub-50 degC manual targets (10-30 W rule; Standby leaves 180 W armed)
CONFIG_CACHE_FILE = 'esi_nvm_configs.json'  # last known NVM list, seeds the Config dropdown before enumeration


def configIndex(item) -> 'int | None':
    """Return the leading integer of a '<index>: <name>' dropdown item (or a bare index string)."""
    try:
        return int(str(item).split(':', 1)[0])
    except ValueError:
        return None


def loadConfigCache() -> dict:
    """Return the last known NVM config list per COM port (written after every real enumeration)."""
    file = getValidConfigPath() / CONFIG_CACHE_FILE
    try:
        if file.exists():
            return json.loads(file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    return {}


def saveConfigCache(cache: dict) -> None:
    """Persist the NVM config list so the next session's dropdown seeds with real items."""
    try:
        (getValidConfigPath() / CONFIG_CACHE_FILE).write_text(json.dumps(cache, indent=2), encoding='utf-8')
    except OSError:
        pass


def mergedConfigItems() -> tuple[list[str], str]:
    """Return the Config dropdown seed (items, default item).

    Union of the cached lists, deduplicated by index, plus a bare Standby fallback.
    The union guarantees that a saved channel selection finds its item at channel-file
    load time (a COMBO value missing from the items resets to item 0); the fresh
    enumeration replaces the list right after the controller connects.
    """
    seen = set()
    items = []
    for cachedList in loadConfigCache().values():
        for item in cachedList:
            index = configIndex(item)
            if index is not None and index not in seen:
                seen.add(index)
                items.append(item)
    if STANDBY_CONFIG not in seen:
        items.append(str(STANDBY_CONFIG))
    items.sort(key=lambda item: configIndex(item) or 0)
    default = next((item for item in items if configIndex(item) == STANDBY_CONFIG), items[0])
    return items, default


class ESI(Device):
    """Contains a list of HV channels of the CGC ESI controller (electrospray high voltage).

    The controller carries up to 4 module slots; the lab uses HV supplies on addresses 2
    (inlet) and 3 (emitter) plus the HTCTRL-24-10 heat controller on address 0 (notebook
    032, firmware 1-00). Channel values set the target voltage (V); the PLOTTED/RECORDED
    channel data is the measured module output current in nA — the physically interesting
    signal (user, 2026-07-06). The voltage readback stays as the monitor (number +
    deviation warning).

    Firmware 1-00 semantics (device-level activation API is GONE; notebook-032
    hardware run 2026-07-27): operation is configuration-driven. On loads the NVM
    config selected in the Config column (a working config takes effect immediately —
    heating starts and HV is applied, no enable step); selecting a config while On
    loads it the same way. THE CONFIG IS THE TRUTH (user decision 2026-07-27): after
    every load the channel voltages and the heater-temperature setting sync FROM the
    device (a load clobbers manual targets — hardware-proven), then user edits write
    through live. The device parks in the Off config (slot 0: device + all modules
    off; user decision 2026-07-28, supersedes Standby slot 1 of 2026-07-27) at ALL
    park points — right after init (the esibd_bs bring-up leaves the device enabled),
    on Enable-off and on close — with set_enable(False) as the loud fallback if the
    park load fails. The heater target lives in the device settings ('Heater
    temperature'); a manual target below 50 degC applies the reduced 20 W power limit
    first (overshoot rule — the Standby trap leaves 180 W armed). The Save toolbar
    action stores the LIVE working set to a user-chosen NVM slot + name (SW-plugin
    precedent; requires On — a parked device would store the Off working set).

    The ESI-CTRL DLL is SINGLE-INSTANCE per process (no port argument in its exports):
    one controller, one COM port, and never this plugin and an ESI notebook at the
    same time — the esibd_bs class enforces this with a connect guard.
    """

    name = 'ESI'
    version = '1.2'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = 'nA'  # unit of the plotted/recorded channel data (measured current); set values are volts (see HVChannel headers)
    iconFile = 'ESI.png'
    useMonitors = True
    useOnOffLogic = True
    channels: 'list[HVChannel]'

    # type hints for settings
    comPort: int
    heaterTemp: float
    heaterTempMeasured: float

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = HVChannel
        self.syncingConfig = False  # guards the Config-mirroring event against recursion and programmatic updates

    def initGUI(self) -> None:
        super().initGUI()
        self.addAction(event=lambda: self.controller.listConfigs(), toolTip='List the NVM config slots (index + name) of the ESI controller in the Console.',
                       icon='configs.png')
        self.addAction(event=lambda: self.saveConfigDialog(), toolTip='Save the CURRENT working set (HV targets, heater temperature, module enables)\n'
                       'to an NVM config slot. Requires On — a parked device would store the Off working set.',
                       icon='saveconfig.png')
        self.controller = ESIController(controllerParent=self)

    def saveConfigDialog(self) -> None:
        """Ask for slot number + name, then store the current working set in the controller NVM (user request 2026-07-28; SW-plugin precedent)."""
        if not self.controller.initialized or self.controller.esi is None:
            self.print('Initialize communication first — the save action stores the LIVE working set of the ESI controller.', flag=PRINT.WARNING)
            return
        if not self.isOn():
            self.print('Device is Off (parked in the Off config) — saving now would store the parked working set. '
                       'Switch On and set up the working set first.', flag=PRINT.WARNING)
            return
        items = self.controller.configItems or []
        occupants = {configIndex(item): item for item in items}
        dialog = QDialog(self, Qt.WindowType.WindowStaysOnTopHint)
        dialog.setWindowTitle('Save working set to NVM config')
        layout = QFormLayout(dialog)
        slotBox = QSpinBox()
        slotBox.setRange(2, MAX_CONFIG_SLOT)  # slot 0 = Off (park target) and slot 1 = Standby (dropdown fallback), both protected
        used = {index for index in occupants if index is not None}
        slotBox.setValue(next((i for i in range(100, MAX_CONFIG_SLOT + 1) if i not in used), 100))  # suggest a free slot above the shipped ladder
        nameEdit = QLineEdit()
        nameEdit.setMaxLength(CONFIG_NAME_MAX)
        nameEdit.setPlaceholderText('e.g. Reserpine_150C_65V')
        occupantLabel = QLabel()

        def updateOccupant() -> None:
            occupantLabel.setText(f"overwrites: '{occupants[slotBox.value()]}'" if slotBox.value() in occupants else 'slot is free')
        slotBox.valueChanged.connect(updateOccupant)
        updateOccupant()
        layout.addRow('Slot (DLL index)', slotBox)
        layout.addRow('Name', nameEdit)
        layout.addRow('', occupantLabel)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec():
            self.controller.saveWorkingSet(slotBox.value(), nameEdit.text().strip())

    def getChannels(self) -> 'list[HVChannel]':
        return cast('list[HVChannel]', super().getChannels())

    def getDefaultSettings(self) -> dict[str, dict]:
        settings = super().getDefaultSettings()
        settings[f'{self.name}/Interval'][Parameter.VALUE] = 1000
        settings[f'{self.name}/{self.MAXDATAPOINTS}'][Parameter.VALUE] = 1E5
        # Device-level COM port: the single-instance DLL allows exactly one controller.
        settings[f'{self.name}/COM Port'] = parameterDict(value=getComPort('ESI', default=14), minimum=1, maximum=99,
                                                           toolTip='COM port number of the ESI controller.',
                                                           parameterType=PARAMETERTYPE.INT, attr='comPort')
        settings[f'{self.name}/Heater temperature'] = parameterDict(value=-1.0, minimum=-1.0, maximum=HEATER_MAX_C, parameterType=PARAMETERTYPE.FLOAT,
                                                                    toolTip='Target heater temperature in degC (HTCTRL-24-10, address 0). Negative = control off.\n'
                                                                            'Synced FROM the device after every config load (the config is the truth); edits apply\n'
                                                                            'live. A manual target below 50 degC applies the reduced 20 W power limit first.',
                                                                    attr='heaterTemp', event=lambda: self.heaterTempChanged())
        settings[f'{self.name}/Heater T measured'] = parameterDict(value=0.0, parameterType=PARAMETERTYPE.FLOAT, indicator=True, restore=False,
                                                                   toolTip='Measured heater temperature in degC (read-only).', attr='heaterTempMeasured')
        return settings

    def heaterTempChanged(self) -> None:
        """Apply an edited heater target to the device (settings can restore before initGUI creates the controller)."""
        controller = getattr(self, 'controller', None)
        if controller is not None:
            controller.applyHeaterTempFromThread()

    def onConfigSelected(self, channel: 'HVChannel') -> None:
        """Mirror the Config selection to every real row (one physical controller); load it right away if On.

        The config is the truth (user 2026-07-27): a live-select loads the config and the
        channel voltages + heater temperature then sync FROM the device. While Off the
        selection is only stored — the next On loads it.
        """
        if self.syncingConfig or not channel.real:
            return
        self.syncingConfig = True
        try:
            for sibling in self.getChannels():
                if sibling is not channel and sibling.real and sibling.configuration != channel.configuration:
                    sibling.getParameterByName(HVChannel.CONFIG).value = channel.configuration
        finally:
            self.syncingConfig = False
        if self.isOn():
            self.controller.applyConfigFromThread()

    def closeCommunication(self) -> None:
        self.setOn(False)
        self.controller.toggleOnFromThread(parallel=False)
        super().closeCommunication()


class HVChannel(Channel):
    """Channel for a single ESI HV supply module."""

    ADDRESS = 'Address'
    CURRENT = 'Current'
    CONFIG = 'Config'
    channelParent: ESI

    def getDefaultChannel(self) -> dict[str, dict]:

        self.address: int
        self.current: float
        self.configuration: str

        configItems, configDefault = mergedConfigItems()
        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'Voltage (V)'
        channel[self.MONITOR][Parameter.HEADER] = 'U (V)'  # voltage readback; device unit is nA (plotted currents)
        channel[self.CURRENT] = parameterDict(value=0, parameterType=PARAMETERTYPE.FLOAT, advanced=False, header='I (nA)', indicator=True, attr='current',
                                              toolTip='Measured HV output current (read-only). This is what the plot and the recorded data show.')
        channel[self.CONFIG] = parameterDict(value=configDefault, parameterType=PARAMETERTYPE.COMBO, items=', '.join(configItems), fixedItems=True,
                                             advanced=False, header='Config', attr='configuration', event=self.configChanged,
                                             toolTip='NVM config of the ESI controller (index: name), mirrored between all rows (one controller).\n'
                                                     'Loaded on On; selecting while On loads it immediately. The config is the truth: channel\n'
                                                     'voltages + heater temperature sync FROM the loaded config, then edits apply live.\n'
                                                     'List refreshes from the device NVM at every initialization.')
        channel[self.ADDRESS] = parameterDict(value=2, minimum=0, maximum=3, parameterType=PARAMETERTYPE.INT, advanced=True,
                                              header='Addr', toolTip='HV module address on the ESI controller (0-3; the lab uses 2 and 3).', attr='address')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.insertDisplayedParameter(self.CONFIG, before=self.DISPLAY)
        self.insertDisplayedParameter(self.CURRENT, before=self.DISPLAY)
        self.displayedParameters.append(self.ADDRESS)

    def configChanged(self) -> None:
        """Hand the user's Config selection to the device for mirroring and (if On) live-apply."""
        self.channelParent.onConfigSelected(self)

    def tempParameters(self) -> list[str]:
        return [*super().tempParameters(), self.CURRENT]

    def appendValue(self, lenT: int, nan: bool = False) -> None:
        """Append the measured module current (nA) as the channel data.

        The emitter/inlet currents (modules 3/2) are the signal of interest — plotted and
        recorded instead of the voltage readback (user, 2026-07-06). The voltage readback
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
                                and ((self.channelParent.isOn() and abs(self.monitor - self.value) > 5)
                                or (not self.channelParent.isOn() and abs(self.monitor - 0) > 5)))

    def realChanged(self) -> None:
        for name in (self.ADDRESS, self.CURRENT, self.CONFIG):
            self.getParameterByName(name).setVisible(self.real)
        super().realChanged()


class ESIController(DeviceController):
    """Controller for the CGC ESI controller. One instance, one COM port (single-instance DLL)."""

    controllerParent: ESI

    class SignalCommunicate(DeviceController.SignalCommunicate):
        """Bundle pyqtSignals."""

        configListsChangedSignal = pyqtSignal()
        """Signal that transfers the freshly enumerated NVM config list from the init thread to the Config dropdowns."""
        workingSetSyncSignal = pyqtSignal(object, float)
        """Signal that transfers the loaded working set ({address: HV target V}, heater target degC) from the worker thread to the inputs."""

    def __init__(self, controllerParent: ESI) -> None:
        super().__init__(controllerParent=controllerParent)
        self.esi = None  # esibd_bs ESI device instance
        self.currents = None  # measured module currents (nA), parallel to values
        self.telemetryThrottle = ChannelThrottle()
        self.hkPushTime = 0.0  # last housekeeping push (epoch s)
        self.configItems = None  # list of 'index: name' NVM config items (None = not enumerated yet / failed)
        self.syncing = False  # True while syncInputs writes the inputs — suppresses the apply events they fire
        self.heaterMeasured = np.nan  # last measured heater temperature (degC)
        self.signalComm.configListsChangedSignal.connect(self.updateConfigCombos)
        self.signalComm.workingSetSyncSignal.connect(self.syncInputs)

    def initializeValues(self, reset: bool = False) -> None:  # noqa: ARG002
        """Initialize values array: one entry per channel for monitor readback (+ current indicators)."""
        channels = self.controllerParent.getChannels()
        if channels:
            self.values = np.full(len(channels), fill_value=np.nan, dtype=np.float32)
            self.currents = np.full(len(channels), fill_value=np.nan, dtype=np.float32)

    def runInitialization(self) -> None:
        try:
            from devices.cgc import ESI as ESIDevice

            com = self.controllerParent.comPort
            sink = getLabSink()
            if sink is None:
                self.print('Telemetry sink unavailable (LAB_CONFIG not set?) — running without telemetry.db writes.', flag=PRINT.DEBUG)
            self.print(f'Connecting to ESI controller on COM{com}...')
            self.esi = ESIDevice(device_id='ESI', com=com, baudrate=230400, sink=sink)

            # connect() runs the bring-up (open -> set_comspeed -> set_enable) and
            # claims the process-wide single-instance slot (P6.5). The bring-up leaves
            # the device ENABLED, so it is parked in the Off config right away —
            # activation is config-driven since 1-00 (user 2026-07-28).
            if not self.esi.connect():
                self.print(f'Failed to connect to ESI controller on COM{com}.', flag=PRINT.ERROR)
                self.esi = None
                return

            self._parkAtInit()

            # Discover present modules so we can warn if a configured channel points at a missing module.
            present_status, _, max_module, presence_list = self.esi.get_module_presence()
            if present_status == self.esi.NO_ERR:
                present_modules = [i for i in range(min(max_module + 1, self.esi.MODULE_NUM)) if presence_list[i] == self.esi.MODULE_PRESENT]
                self.print(f'ESI controller on COM{com}: modules present {present_modules}')
                configured = {ch.address for ch in self.controllerParent.getChannels() if ch.real and ch.enabled}
                missing = configured - set(present_modules)
                if missing:
                    self.print(f'ESI controller on COM{com}: configured channels reference missing modules {sorted(missing)}', flag=PRINT.WARNING)
            else:
                self.print(f'ESI controller on COM{com}: get_module_presence failed (status {present_status})', flag=PRINT.WARNING)

            self.configItems = self._enumerateConfigs()
            # emitted before initCompleteSignal so the dropdown is refreshed before initComplete can trigger the On bring-up
            self.signalComm.configListsChangedSignal.emit()

            self.signalComm.initCompleteSignal.emit()
        except Exception as e:  # noqa: BLE001
            self.print(f'Error initializing ESI controller: {e}', flag=PRINT.ERROR)
            self.esi = None
        finally:
            self.initializing = False

    def fakeInitialization(self) -> None:
        """Create a simulated esibd_bs device before faking init, so Test Mode telemetry lands in the shared db with sim=1."""
        self.esi = None
        try:
            from devices.cgc import ESI as ESIDevice

            sink = getLabSink()
            if sink is not None:
                self.esi = ESIDevice(device_id='ESI', com=self.controllerParent.comPort, baudrate=230400, sink=sink, test_mode=True)
                self.esi.connect()
        except Exception as e:  # noqa: BLE001
            self.print(f'Test Mode runs without telemetry device: {e}', flag=PRINT.DEBUG)
        if self.esi is not None:
            self._parkAtInit()
            # the ESI sim models the NVM (SIM_CONFIG_NAMES) — the real enumeration path works in Test Mode
            self.configItems = self._enumerateConfigs()
            self.signalComm.configListsChangedSignal.emit()
        super().fakeInitialization()

    def _enumerateConfigs(self) -> 'list[str] | None':
        """Read the populated NVM config slots (index + name) of the controller.

        Returns None on failure so the dropdown keeps its last known list. Commas in
        device-stored names are replaced (the COMBO items string is comma-separated).
        """
        try:
            status, _active, valid = self.esi.list_configs()
            if status != self.esi.NO_ERR:
                self.print(f'list_configs failed (status {status}) — keeping the last known config list.', flag=PRINT.WARNING)
                return None
            items = []
            for index in valid:
                name_status, name = self.esi.get_config_name(index)
                items.append(f'{index}: {name.replace(",", ";")}' if name_status == self.esi.NO_ERR and name else f'{index}: <unnamed>')
        except Exception as e:  # noqa: BLE001
            self.print(f'NVM enumeration failed: {e}', flag=PRINT.WARNING)
            return None
        return items or None

    def updateConfigCombos(self) -> None:
        """Replace the Config dropdown items with the freshly enumerated NVM list (runs in the main thread).

        Selections are re-matched by config index (a renamed slot updates silently); a
        vanished index falls back to Standby with a warning. Real enumerations refresh
        the on-disk cache that seeds the dropdown at the next start.
        """
        items = self.configItems
        if not items:
            return
        if not getTestMode():
            cache = loadConfigCache()
            cache[str(self.controllerParent.comPort)] = items
            saveConfigCache(cache)
        device = self.controllerParent
        device.syncingConfig = True
        try:
            for channel in device.getChannels():
                if not channel.real:
                    continue
                parameter = channel.getParameterByName(HVChannel.CONFIG)
                previous = str(parameter.value)
                previousIndex = configIndex(previous)
                parameter.combo.blockSignals(True)
                parameter.combo.clear()
                for item in items:
                    parameter.combo.insertItem(parameter.combo.count(), item)
                match = next((k for k, item in enumerate(items) if configIndex(item) == previousIndex), -1)
                if match == -1:
                    match = next((k for k, item in enumerate(items) if configIndex(item) == STANDBY_CONFIG), 0)
                    self.print(f"{channel.name}: stored config '{previous}' not found in the controller NVM — "
                               f"falling back to '{items[match]}'.", flag=PRINT.WARNING)
                parameter.combo.setCurrentIndex(match)
                parameter.combo.blockSignals(False)
        finally:
            device.syncingConfig = False

    def _selectedConfig(self) -> int:
        """Return the config index selected in the mirrored Config column (first real row; fallback: Standby)."""
        for channel in self.controllerParent.getChannels():
            if channel.real:
                index = configIndex(channel.configuration)
                if index is not None:
                    return index
                break
        return STANDBY_CONFIG

    def applyConfigFromThread(self) -> None:
        """Load the selected config and sync the inputs from the resulting working set (thread safe)."""
        if self.initialized:
            threading.Thread(target=self.applyConfig, name=f'{self.controllerParent.name} applyConfigThread', daemon=True).start()

    def applyConfig(self) -> None:
        """Live config change — same sequence as the On bring-up."""
        with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock to load the ESI config.') as lock_acquired:
            if lock_acquired:
                self._loadConfigLocked(self._selectedConfig())

    def _loadConfigLocked(self, config: int) -> None:
        """Load one NVM config and sync the inputs FROM the device (caller must hold the controller lock).

        1-00 workflow: a working config takes effect immediately (heating starts, HV is
        applied — no enable step) and CLOBBERS manual targets, so the channel voltages
        and the heater-temperature setting are read back afterwards and synced into the
        GUI (config is the truth, user 2026-07-27). Modules of unchecked rows are
        deactivated on top of the config (the E checkbox keeps meaning 'this module may
        output'). A failed load is loud and leaves the device in its previous state.
        """
        if self.esi is None:
            return
        status = self.esi.load_current_config(config)
        if status != self.esi.NO_ERR:
            self.print(f'Failed to load config {config}: status {status}', flag=PRINT.ERROR)
            return
        self.print(f'Loaded config {config}.')
        targets = {}
        for channel in self.controllerParent.getChannels():
            if not (channel.real and channel.address > 0):
                continue
            if not channel.enabled:
                mod_status = self.esi.set_module_activation_state(channel.address, False)
                if mod_status != self.esi.NO_ERR:
                    self.print(f'Failed to deactivate module {channel.address}: status {mod_status}', flag=PRINT.WARNING)
                continue
            target_status, volts = self.esi.get_hv_supply_target_output_voltage(channel.address)
            if target_status == self.esi.NO_ERR:
                targets[channel.address] = float(volts)
        heater_status, heater_target = self.esi.get_heat_ctrl_heater_temperature()
        heater = float(heater_target) if heater_status == self.esi.NO_ERR else np.nan
        self.hkPushTime = 0.0  # next read cycle pushes the new activation state to telemetry right away
        self.signalComm.workingSetSyncSignal.emit(targets, heater)

    def syncInputs(self, targets: dict, heater: float) -> None:
        """Write the loaded working set into the channel voltages + heater setting (runs in the main thread).

        Config is the truth (user 2026-07-27): the inputs follow the loaded config, never
        the other way around. lastAppliedValue is set alongside (and read back after the
        set, so widget rounding cannot re-trigger an apply) and self.syncing suppresses
        the events.
        """
        self.syncing = True
        try:
            for channel in self.controllerParent.getChannels():
                if not (channel.real and channel.address in targets):
                    continue
                value = round(targets[channel.address], 2)
                channel.lastAppliedValue = value  # BEFORE the value event fires, so applyValue sees no change
                parameter = channel.getParameterByName(HVChannel.VALUE)
                parameter.value = value
                channel.lastAppliedValue = parameter.value  # read back what the widget stored (display rounding)
            if np.isfinite(heater):
                self.controllerParent.heaterTemp = round(max(-1.0, min(heater, HEATER_MAX_C)), 1)
        finally:
            self.syncing = False

    def applyHeaterTempFromThread(self) -> None:
        """Apply the heater-temperature setting to the device (thread safe; no-op while a sync writes it)."""
        if self.syncing or not self.initialized:
            return
        threading.Thread(target=self.applyHeaterTemp, name=f'{self.controllerParent.name} heaterTempThread', daemon=True).start()

    def applyHeaterTemp(self) -> None:
        """Set the heater target temperature; a sub-50 degC target gets the reduced power limit first.

        The overshoot rule (CGC 2026-07-21): below 50 degC limit the heater to 10-30 W —
        and the Standby config leaves the 180 W limit armed (notebook-032 trap). The
        power-limit setter is not simulated, so it is skipped in Test Mode. Negative
        target = temperature control off.
        """
        if self.esi is None:
            return
        temp = float(self.controllerParent.heaterTemp)
        with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock to set the heater temperature.') as lock_acquired:
            if not lock_acquired:
                return
            if 0 <= temp < HEATER_SAFE_TEMP_C and not self.esi.test_mode:
                try:
                    limit_status = self.esi.set_heat_ctrl_power_limit(HEATER_SAFE_POWER_W)
                    if limit_status != self.esi.NO_ERR:
                        self.print(f'Failed to set the {HEATER_SAFE_POWER_W:.0f} W heater power limit: status {limit_status} — '
                                   'NOT setting the sub-50 degC target (overshoot rule).', flag=PRINT.ERROR)
                        return
                except Exception as e:  # noqa: BLE001
                    self.print(f'Failed to set the heater power limit: {e} — NOT setting the sub-50 degC target.', flag=PRINT.ERROR)
                    return
            status, set_value = self.esi.set_heat_ctrl_heater_temperature(temp)
            if status != self.esi.NO_ERR:
                self.print(f'Failed to set the heater target to {temp:.1f} degC: status {status}', flag=PRINT.WARNING)
            else:
                self.print(f'Heater target set to {set_value:.1f} degC.', flag=PRINT.TRACE)

    def _pushTelemetry(self) -> None:
        """Write the freshly read monitor voltages to the telemetry sink, throttled per channel (>=5 s)."""
        if self.esi is None or self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if not (channel.enabled and channel.real and i < len(self.values)):
                continue
            value = float(self.values[i])
            if not np.isfinite(value) or not self.telemetryThrottle.ready(channel.name):
                continue
            # explicit 'V': self.values are voltage readbacks; the device unit is 'nA' (plotted currents)
            self.esi.log_sample(channel.name, value, 'V')
        self._pushHousekeeping()

    def _pushHousekeeping(self, force: bool = False) -> None:
        """Push electronics housekeeping (internal temps, heater temp + activation state) to the telemetry sink every HK_PUSH_INTERVAL_S.

        Rides the read loop (which already holds the controller lock) — never a second polling thread on the DLL.
        DB-only: these are not Explorer channels and never appear in the GUI; the dashboard's Electronics tab reads them.
        'Activated' derives from the main state (1-00 dropped the activation API; ST_ON = 1) — channel name kept for the dashboard.
        force=True skips the time gate (final push on closeCommunication; caller must hold the controller lock).
        """
        if self.esi is None or (not force and time.time() - self.hkPushTime < HK_PUSH_INTERVAL_S):
            return
        self.hkPushTime = time.time()
        try:
            status, _volt_24v, _volt_5v0, _volt_3v3, temp_cpu, temp_psu = self.esi.get_housekeeping()
            if status == self.esi.NO_ERR:
                self.esi.log_sample('Temp_CPU', temp_cpu, 'degC', '.1f')
                self.esi.log_sample('Temp_PSU', temp_psu, 'degC', '.1f')
            status, _state_hex, state_name = self.esi.get_main_state()
            if status == self.esi.NO_ERR:
                self.esi.log_sample('Activated', 1 if state_name == 'STATE_ON' else 0)
            if np.isfinite(self.heaterMeasured):
                self.esi.log_sample('Temp_Heater', float(self.heaterMeasured), 'degC', '.1f')
        except Exception as e:  # noqa: BLE001
            self.print(f'Housekeeping push failed: {e}', flag=PRINT.DEBUG)

    def applyValue(self, channel: HVChannel) -> None:
        if self.syncing:
            return  # input sync after a config load — the values CAME from the device, never push them back
        if self.esi is None:
            return
        voltage = channel.value if (channel.enabled and self.controllerParent.isOn()) else 0
        with self.lock.acquire_timeout(1, timeoutMessage=f'Cannot acquire lock to set {channel.name}.') as lock_acquired:
            if lock_acquired:
                status = self.esi.set_hv_supply_target_output_voltage(channel.address, voltage)
                if status != self.esi.NO_ERR:
                    self.print(f'Error setting {channel.name}: status {status}', flag=PRINT.WARNING)
                    self.errorCount += 1
                else:
                    self.print(f'Set {channel.name} to {voltage:.2f} V (module {channel.address})', flag=PRINT.TRACE)

    def readNumbers(self) -> None:
        """Read measured HV output voltage + current from every configured module, plus the heater temperature."""
        if self.esi is None or self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if not (channel.enabled and channel.real and i < len(self.values)):
                continue
            try:
                status, valid, volts = self.esi.get_hv_supply_output_voltage(channel.address)
                if status == self.esi.NO_ERR and valid:
                    self.values[i] = volts
                    self.errorCount = 0
                else:
                    self.errorCount += 1
                status, valid, amps = self.esi.get_hv_supply_output_current(channel.address)
                if status == self.esi.NO_ERR and valid:
                    self.currents[i] = amps * 1e9  # A -> nA (nb-024 currents sit around 0.1 nA)
            except Exception as e:  # noqa: BLE001
                self.print(f'Error reading module {channel.address}: {e}', flag=PRINT.ERROR)
                self.errorCount += 1
        try:
            status, valid, _volt_out, _volt_heat, _curr_out, temp_heat = self.esi.get_heat_ctrl_monitoring()
            self.heaterMeasured = float(temp_heat) if status == self.esi.NO_ERR and valid else np.nan
        except Exception as e:  # noqa: BLE001
            self.print(f'Error reading the heater temperature: {e}', flag=PRINT.DEBUG)
            self.heaterMeasured = np.nan
        self._pushTelemetry()

    def fakeNumbers(self) -> None:
        if self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real:
                if self.controllerParent.isOn():
                    self.values[i] = channel.value + self.rng.random() - 0.5
                    self.currents[i] = 0.1 + self.rng.random() * 0.05
                else:
                    self.values[i] = self.rng.random() - 0.5
                    self.currents[i] = 0.0
        if self.esi is not None:
            try:
                status, valid, _volt_out, _volt_heat, _curr_out, temp_heat = self.esi.get_heat_ctrl_monitoring()
                self.heaterMeasured = float(temp_heat) if status == self.esi.NO_ERR and valid else np.nan
            except Exception:  # noqa: BLE001
                self.heaterMeasured = np.nan
        else:
            self.heaterMeasured = 22.0 + self.rng.random()
        self._pushTelemetry()

    def updateValues(self) -> None:
        if self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real and i < len(self.values):
                channel.monitor = np.nan if channel.waitToStabilize else self.values[i]
                if self.currents is not None and np.isfinite(self.currents[i]):
                    channel.current = float(self.currents[i])
        if np.isfinite(self.heaterMeasured):
            self.controllerParent.heaterTempMeasured = round(float(self.heaterMeasured), 1)

    def toggleOn(self) -> None:
        super().toggleOn()
        if self.esi is None:
            return
        try:
            # Every DLL call must hold the controller lock — an unlocked call garbles the
            # in-flight exchange of the locked read/set threads (-13 storms, 2026-07-05).
            with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock to toggle the ESI configuration.') as lock_acquired:
                if not lock_acquired:
                    return
                if self.controllerParent.isOn():
                    self._loadConfigLocked(self._selectedConfig())
                else:
                    self._parkLocked()
        except Exception as e:  # noqa: BLE001
            self.print(f'Error toggling the ESI controller: {e}', flag=PRINT.ERROR)

    def _parkAtInit(self) -> None:
        """Park in the Off config right after connect — the bring-up (set_enable in _open_transport) leaves the device enabled."""
        with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock to park the ESI controller after init — verify the device state at the bench.') \
                as lock_acquired:
            if lock_acquired:
                self._parkLocked()

    def _parkLocked(self) -> None:
        """Park the device in the Off config (slot 0 — device + all modules off; user decision 2026-07-28, supersedes Standby slot 1).

        Runs after init, on Enable-off and on close. Caller must hold the controller
        lock. The Config dropdown is deliberately NOT snapped — it keeps the user's
        selection, which the next On loads. A failed park falls back to
        set_enable(False) (main state STBY) and is loud.
        """
        if self.esi is None:
            return
        status = self.esi.load_current_config(OFF_CONFIG)
        if status != self.esi.NO_ERR:
            self.print(f'PARK NOT CONFIRMED: Off config load returned {status} — falling back to set_enable(False).', flag=PRINT.ERROR)
            fallback_status = self.esi.set_enable(False)
            if fallback_status != self.esi.NO_ERR:
                self.print(f'set_enable(False) fallback returned {fallback_status} — verify the device state at the bench.', flag=PRINT.ERROR)
        self.hkPushTime = 0.0  # next read cycle pushes the new activation state to telemetry right away

    def saveWorkingSet(self, slot: int, name: str) -> None:
        """Store the CURRENT working set in NVM slot + name it, then refresh the dropdown (thread safe, user 2026-07-28).

        Deltas vs the SW precedent: no test-mode branch (the esibd_bs ESI sim models
        the NVM end-to-end — save/name/list/get all work on _sim_config_names) and no
        call_with_retry (DLL bridge, not a serial-family device). The settle sleeps
        are skipped in Test Mode to keep headless verification fast.
        """
        def save() -> None:
            with self.lock.acquire_timeout(10, timeoutMessage=f'Cannot acquire lock to save config {slot}.') as lock_acquired:
                if not lock_acquired:
                    return
                status = self.esi.save_current_config(slot)
                if status != self.esi.NO_ERR:
                    self.print(f'Failed to save the working set to slot {slot}: status {status}', flag=PRINT.ERROR)
                    return
                if not self.esi.test_mode:
                    time.sleep(NVM_SETTLE_S)  # NVM write — no traffic until settled
                if name:
                    name_status = self.esi.set_config_name(slot, name)
                    if name_status != self.esi.NO_ERR:
                        self.print(f'Config saved, but naming slot {slot} failed: status {name_status}', flag=PRINT.WARNING)
                    if not self.esi.test_mode:
                        time.sleep(NVM_SETTLE_S)
                items = self._enumerateConfigs()
                if items:
                    self.configItems = items
                self.print(f"Working set saved to NVM slot {slot} ('{name or '<unnamed>'}').")
            self.signalComm.configListsChangedSignal.emit()
        if self.esi is not None:
            threading.Thread(target=save, name=f'{self.controllerParent.name} saveConfigThread', daemon=True).start()
        else:
            self.print('ESI controller not connected.', flag=PRINT.WARNING)

    def listConfigs(self) -> None:
        """List the populated NVM config slots (index + device-stored name) in the Console and refresh the Config dropdown."""
        def enumerate_configs() -> None:
            with self.lock.acquire_timeout(5, timeoutMessage='Cannot acquire lock to read the ESI configs.') as lock_acquired:
                if not lock_acquired:
                    return
                items = self._enumerateConfigs()
            if items is None:
                return
            for item in items:
                self.print(f'Config {item}')
            self.print(f'{len(items)} populated NVM config slots.')
            self.configItems = items
            self.signalComm.configListsChangedSignal.emit()
        if self.esi is not None:
            threading.Thread(target=enumerate_configs, name=f'{self.controllerParent.name} configListThread', daemon=True).start()
        else:
            self.print('ESI controller not connected.', flag=PRINT.WARNING)

    def closeCommunication(self) -> None:
        super().closeCommunication()  # stops acquisition first
        if self.esi is not None:
            with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock to close the ESI controller.'):
                try:
                    self._parkLocked()
                except Exception:  # noqa: BLE001
                    pass
                # final hk push so telemetry records the parked state
                # (read loop stopped; dashboard staleness covers a crash)
                self._pushHousekeeping(force=True)
                try:
                    self.esi.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            self.esi = None
        self.initialized = False
