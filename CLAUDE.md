# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ESIBD Explorer is a PyQt6 desktop application for data acquisition and analysis in Electrospray Ion-Beam Deposition experiments. It provides hardware control, real-time data monitoring, and experimental analysis through a plugin-based architecture.

- **Python** >= 3.11, **PyQt6** 6.6, **matplotlib**, **pyqtgraph**
- Docs: https://esibd-explorer.readthedocs.io/
- Source: https://github.com/ioneater/ESIBD-Explorer

## Common Commands

```bash
# Run the application
python -m esibd.explorer

# Simulate fresh install (clears registry settings)
python -m esibd.reset

# Lint and format
ruff check esibd/
ruff format esibd/

# Build documentation (Sphinx)
sphinx-build docs docs/_build

# Build package for PyPI
python -m build
twine check dist/*

# Environment setup (conda)
cd setup && ./create_env.bat   # Windows
conda activate esibd
```

## Testing

There is no pytest/unittest suite. Testing is built into the plugin system:
- Each plugin has a `.test()` method run from the Console plugin
- `PluginManager.test()` runs all plugin tests (accessible from the in-app Console)
- Hardware integration testing is manual
- Test with all/no plugins enabled, and after `python -m esibd.reset`

## Version Bumping

Version must be updated manually in 4 files:
1. `pyproject.toml` — `version`
2. `esibd/config.py` — `PROGRAM_VERSION`
3. `docs/conf.py` — `release`
4. `EsibdExplorer.ifp` — Product Version (InstallForge GUI)

## Architecture

### Core Files

- **`esibd/core.py`** (~6000 LOC): Main framework — `EsibdExplorer` (QMainWindow), `PluginManager`, `Parameter`, `Channel`, `Setting`, custom widgets, `Logger`, threading primitives (`TimeoutLock`, `SignalCommunicate`)
- **`esibd/plugins.py`** (~8000 LOC): Plugin base classes — `Plugin`, `Device`, `Scan`, `StaticDisplay`, `LiveDisplay`, `ChannelManager`, and built-in plugins (`Console`, `Browser`, `Explorer`, `UCM`, `PID`, etc.)
- **`esibd/const.py`**: Enums (`PARAMETERTYPE`, `PLUGINTYPE`, `PRINT`, `INOUT`), utility functions (`smooth`, `synchronized`, `plotting`, `dynamicImport`)
- **`esibd/config.py`**: Program identity constants (`PROGRAM_NAME`, `PROGRAM_VERSION`, paths)
- **`esibd/provide_plugins.py`**: Defines core plugin load order
- **`esibd/extended.py`**: Customized `Settings` plugin (`ESIBDSettings`)

### Plugin System

Plugins are discovered from multiple directories in order: `provide_plugins.py` (core) -> `examples/` -> `devices/` -> `scans/` -> `displays/` -> user `pluginPath`. Each plugin directory contains subdirectories with a `.py` file exporting `providePlugins() -> list[type[Plugin]]`.

Plugin types (`PLUGINTYPE` enum): `CONSOLE`, `CONTROL`, `INPUTDEVICE`, `OUTPUTDEVICE`, `CHANNELMANAGER`, `DISPLAY`, `LIVEDISPLAY`, `SCAN`, `DEVICEMGR`, `INTERNAL`.

Plugin lifecycle: `__init__` -> `finalizeInit()` -> `provideDock()` -> runtime -> `test()`.

### Key Patterns

- **camelCase naming** is used throughout for PyQt compatibility (ruff N802/N803/N806 rules are disabled)
- **Star imports** are used (`F405` disabled) — be aware of namespace collisions
- **`@synchronized(timeout)`** decorator for thread-safe method execution
- **`@plotting`** decorator prevents concurrent matplotlib updates
- **`SignalCommunicate`**: Thread-safe Qt signal emission from worker threads
- **`makeSettingWrapper()` / `makeWrapper()`**: Property factories that decouple value storage from UI

### Threading Model

- Main thread: UI updates and Qt event loop
- Worker threads: Hardware communication and long-running scans
- Use `TimeoutLock` (reentrant with timeout) to avoid deadlocks
- Emit signals via `SignalCommunicate` to update UI from worker threads

### Data Storage

- **Settings**: `QSettings` (Windows Registry / macOS plist / Linux .conf)
- **Scan data**: HDF5 (`.h5`) files with hierarchical metadata
- **Plugin state**: INI files

## Custom Device Plugins (esibd_bs)

Device classes live in a separate pip-installable repo (`esibd_bs`, installed via `pip install -e .`).
ESIBD Explorer plugins under `esibd/devices/` are thin wrappers that import from this package (e.g. `from devices.cgc import PA`).
The DMMR-8 picoammeter plugin is at `esibd/devices/dmmr8/dmmr8.py` — see `.claude/pA.md` for implementation details.
The AMPR-12 DC voltage plugin is at `esibd/devices/ampr12/ampr12.py` — see `.claude/ampr.md` for implementation details.
It manages two AMPR units (AMPR1000/AMPR500) via the MIPS multi-COM pattern. Supports monitor readback, On/Off PSU toggle, and equation-based linked voltages.
The ESI HV plugin is at `esibd/devices/esi/esi.py` (P6.6): INPUTDEVICE, ONE controller with a device-level COM Port setting (the ESI-CTRL DLL is single-instance per process — the esibd_bs class guards connect; never run the plugin and an ESI notebook together). Channel = HV module address 0–3 (lab uses 2 = inlet, 3 = emitter). Channel VALUE sets the target voltage (V), but the device unit is **nA** and the plotted/recorded channel data is the measured module output current (`HVChannel.appendValue` override, user 2026-07-06) — the voltage readback stays as the monitor (`U (V)` column + deviation warning) and is what `_pushTelemetry` logs to telemetry.db (unit pinned to 'V' there). On/Off logic maps to controller + module activation states. Its hk piggyback pushes `Temp_CPU`/`Temp_PSU`/`Activated` (not `Enabled` — bring-up always enables, activation is the meaningful state).
The syringe-pump plugin is at `esibd/devices/syringe/syringe.py` (P6.7): INPUTDEVICE, unit mL/hr, one channel per pump (value = flow rate; On/Off = start/stop, a start resets the pump's displaced-volume counter and re-sends syringe volume/diameter/units/rate); read-only `Displaced` indicator polls the pump. COM key `Syringe_Pump` (port not connected yet). Telemetry: `Displaced_Vol` throttled + `Pump_Running` every 30 s, same piggyback rules as the CGC plugins.
The RF PSU plugin is at `esibd/devices/psu/psu.py` (P6.10 session 1; Config column 2026-07-07): INPUTDEVICE, ONE controller managing the four HV-PSU-CTRL-2D units via the ampr12 multi-COM pattern (per-channel `COM`; dll device index = PSU\<n\> key number − 1, notebook-025 mapping). Channel = one output (`Out` = POS/NEG), VALUE sets the target voltage (V, clamped ≤ 350) but the device unit is **mA** and the plotted/recorded data is the measured output current (ESI precedent); monitor = `Measured U (V)` readback. `I_lim (mA)` is settable per channel and rides along with every voltage set (hardware limit + soft-watchdog threshold: 1st breach = −10 % setpoint, 2nd consecutive = both outputs of that supply disabled; P limit 100 W). **Per-unit `Config` COMBO column** (mirrored across the POS/NEG rows of one COM; replaced the old device-level "Baseline config" setting): items `index: name` enumerated from the device NVM at every plugin initialization (and by the List-configs toolbar action) and cached in `psu_nvm_configs.json` next to plugins.ini — the cache seeds the dropdowns at load time so saved selections survive restarts (COMBO values missing from the items reset to item 0, hence the cache); selections re-match by index. **On = `load_current_config(<selected Config>)` per unit (fresh channels default 63) + E-checkbox-driven output enables + re-apply values; selecting a Config while On runs the same sequence immediately (design choice Option A, [[cgc-psu]] — the channel table stays the voltage truth); Off/close = 0 V + standby config 0** — campaign lesson: bring-up must go through NVM configs, bare enables arm nothing. Every setter/reader uses `CGCDevice.call_with_retry` (purge-retry, EMI -13 recovery). Toolbar: Purge + List-NVM-configs actions (Test Mode prints canned notes and uses a canned config list). hk piggyback pushes `Temp_CPU`/`Temp_Sensor0-2`/`Device_Enabled`/`PSU0_Enabled`/`PSU1_Enabled` per unit — the dashboard's Electronics tab renders three enable dots per PSU card. Recommended channel set (8 real + 4 virtual chain-amplitude knobs via equations) is in the class docstring.
The RF switch plugin is at `esibd/devices/sw/sw.py` (P6.10 session 2): INPUTDEVICE, unit **kHz**, ONE controller driving the esibd_bs `SW` class (`skip_sensors=(0,)` — swB sensor 0 broken); the COM port is a per-channel column (user 2026-07-07; the controller uses the FIRST real row's COM — one switch, one port, key `swB`). ONE real channel v1 (the ED has ONE oscillator; both RF chains share f — per-chain duty needs pulser rerouting, v2); value = frequency (kHz, clamped 1–1000 = campaign-proven envelope), monitor = `Measured f (kHz)` readback from `get_oscillator_period` (deviation warning only while On). **Standby zeroes the period register** — `config_swB.cfg [Configuration1] Oscillator=0` — so a period of 0 with status OK is a parked switch (monitor nan), NEVER a read error: counting it closed communication after 25 silent errors on the real unit (2026-07-07). Extra columns: `Duty (%)` (re-applies immediately while On) and advanced `Pulser` (default 0). **Every frequency/duty move goes through `SW.set_rf`** (esibd_bs): monoflop + stuck-HIGH validation, then width→1 → frequency → duty re-fit, each step locked with purge-retry — never call `set_frequency_khz` bare (the NVM configs fix the width REGISTER; duty drags and width ≥ period = DC on the load). Per-channel `Config` COMBO column mirrors the PSU plugin's mechanics (cache `sw_nvm_configs.json`, enumeration at every init + List-configs action, re-match by index) but **the sync direction is INVERTED vs the PSU plugin (user decision 2026-07-07): config is the truth.** On = `load_current_config(<selected>)` (fresh channels default 89 = SwitchSym 1 MHz baseline), then the frequency/duty INPUTS sync FROM the loaded working set (`workingSetSyncSignal` → main-thread `syncInputs`; sets `lastAppliedValue` alongside and holds `controller.syncing` so the framework's change detection never echoes the synced values back to the device); selecting a Config while On does the same; Off/close = standby config 0. Channel values are NEVER pushed onto a freshly loaded config — only user edits of f/duty after the sync write to the device (via `set_rf`). **Save toolbar action** (`saveconfig.png`): dialog (slot 1–125 + name, slot 0/standby protected, overwrite shown) → `save_current_config` + `set_config_name` with `NVM_SETTLE_S` CTS waits → re-enumeration refreshes the dropdown — the per-ion recipe workflow (notebook 027 proved python-made NVM configs end-to-end on real HW). Toolbar: Purge + List-NVM-configs + Save-config. hk piggyback pushes `Temp_CPU`/`Temp_Sensor1`/`Temp_Sensor2`/`Device_Enabled` (never the broken S0) — the dashboard Electronics tab renders the swB card. Cross-plugin safety order (CGC recipe): ramp PSU voltage at 1 kHz FIRST, then frequency — the SW plugin cannot enforce this; the PSU watchdog catches over-current.
The Lauda chiller plugin was deleted 2026-07-05 (P6.0) — chillers are handled by `lab_services`; recover from git history if ever needed.

COM port assignments are centralized in `esibd/devices/com_ports.json` (all lab devices, COM3–COM27). Device plugins read from this file via `getComPort()` from `esibd/devices/com_helper.py`. The JSON key for the DMMR-8 is `pA`. Update the JSON when COM ports change — no need to edit individual plugins.

**Telemetry sink (P6.3):** device plugins write their read-loop values into the shared lab `telemetry.db` via `esibd/devices/lab_telemetry.py` — `getLabSink()` (process-wide SQLiteSink, resolved from the `LAB_CONFIG` env var → lab_config.toml `[telemetry] db_path`; the lab_services master sets LAB_CONFIG for its children) and `ChannelThrottle` (≥5 s per channel). Controllers call `device.log_sample(channelName, value, unit)` from `readNumbers()`/`fakeNumbers()`; Test Mode constructs the esibd_bs device with `test_mode=True` so its rows carry sim=1. Without LAB_CONFIG there is no sink and plugins behave as before. Never add a second polling thread for telemetry — pushes ride the existing read loop.

**Housekeeping piggyback (P6.4):** the ampr12/dmmr8 controllers also push electronics housekeeping — internal temperatures + enable state, canonical hk channel names (`Temp_*`, `PSU_Enabled`/`Enabled`) — every 30 s (`HK_PUSH_INTERVAL_S`) via `_pushHousekeeping()` from the same read loop. DB-only: these are not Explorer channels and never appear in the GUI (user requirement); the lab dashboard's Electronics/Currents tabs read them. ampr12's `toggleOn()` resets the unit's push gate so the next read cycle records the new enable state immediately; both plugins do a final forced push inside the locked `closeCommunication()` teardown (after enable-off, before disconnect) so a clean Explorer close lands the disabled state in telemetry — a crashed Explorer is covered by the dashboard's staleness display instead. Never start the esibd_bs hk worker from a plugin — its thread would call the DLL outside the controller lock.

**Locking rule:** every esibd_bs vendor-DLL call in a plugin must hold the controller `self.lock` (`acquire_timeout`). The framework locks `readNumbers()`/`applyValue()` only; an unlocked call from `toggleOn()`/`closeCommunication()` garbles the in-flight serial exchange for both threads (real-HW -13 storms, 2026-07-05).

Key gotcha: **Settings > General > Test Mode** must be unchecked for real hardware communication.
When Test Mode is on, `fakeInitialization()` and `fakeNumbers()` run instead of real hardware code (`core.py:5483,5605`).

## Code Style

Configured in `ruff.toml`:
- Line length: 180 characters
- Single quotes for inline strings (`ruff check`), double quotes for formatted output (`ruff format`)
- `select = ["ALL"]` with targeted ignores — see `ruff.toml` for rationale
- Annotations required only in core files (`config.py`, `const.py`, `core.py`, `plugins.py`)
- TODOs in device/example plugins don't need author/issue links (they are instructions for plugin developers)
