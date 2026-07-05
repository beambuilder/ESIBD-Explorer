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
