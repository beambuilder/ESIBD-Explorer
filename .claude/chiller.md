# Lauda Chiller Plugin Notes

## Overview

Three Lauda chillers in the lab setup, each on a separate COM port:
- Chiller_A: COM23
- Chiller_B: COM19
- Chiller_C: COM20

The ESIBD Explorer plugin is at `esibd/devices/chiller/chiller.py`.

## Device Class (from esibd_bs)

The Chiller class is pip-installed from `esibd_bs` (`pip install -e .`).
Import path: `from devices.chiller import Chiller` (via `src/devices/chiller/__init__.py`).

Single class — no base/derived split like the CGC devices.
Communication: RS-232 serial, Lauda ASCII protocol, 115200 baud, `\r\n` terminators.
Write commands expect "OK" response; read commands return numeric strings.

## Plugin Architecture

Multi-COM pattern (same as AMPR-12): one Chiller instance per unique COM port,
mapped in `ChillerController.chillers` dict.

Three classes:
- `Chiller(Device)`: INPUTDEVICE, unit='°C', monitors + on/off logic
- `ChillerChannel(Channel)`: One per physical chiller, has COM, Pump Level, and Running params
- `ChillerController(DeviceController)`: Manages all chiller instances

## Initialization Sequence (runInitialization)

1. `ChillerDev(device_id, port=f'COM{com}', baudrate=115200)` — create instance
2. `chiller.connect()` — opens serial port
3. If device is On: `chiller.start_device()` — starts pumping and cooling
4. Emit `initCompleteSignal`

## Channel Parameters

Each channel has:
- **T (°C)** — target temperature setpoint
- **Run** — boolean tickbox: tick to `start_device()`, untick to `stop_device()`
- **Pump** — pump level (1–6), non-instant update (user must confirm)
- **Monitor** — live readback from `read_temp()`
- **COM** — COM port number (advanced)

## Direct Command Pipeline

All user-triggered commands (temperature, pump level, start/stop) use the same
direct pipeline that bypasses the framework's `applyValueFromThread` chain:

1. UI event triggers channel method (`valueChanged`, `setRunning`, `setPumpLevel`)
2. Channel method creates a `Thread` targeting the controller method
3. Controller method calls the esibd_bs `Chiller` serial method directly

This pattern was adopted because the framework's pipeline (`applyValueFromThread`)
has multiple conditions that silently fail for input devices. The direct approach
matches how pump level always worked reliably.

## Temperature Control

- `valueChanged` → `setTemperature` → Thread → `controller.applyValue` → `chiller.set_temperature(channel.value)`
- `readNumbers` → `chiller.read_temp()` — reads current bath temperature per channel
- Always sends `channel.value` directly (no conditional fallback)

## Pump Level

Per-channel pump level (1–6), set via `chiller.set_pump_level(level)`.
Triggered from the channel UI (non-instant update, user must confirm).

## Start/Stop (Running Tickbox)

Per-channel boolean "Run" checkbox:
- Tick: `chiller.start_device()` — starts pumping and cooling
- Untick: `chiller.stop_device()` — stops pumping and cooling
- Cooling mode is set manually on the chiller (AUTO) and persists — no need to set via plugin

## Shutdown Sequence (closeCommunication)

1. `chiller.stop_device()`
2. `chiller.disconnect()`

## Monitor Warning

Warning state triggers when device is on and |monitor - setpoint| > 5°C.

## Chiller Class Key Methods Reference

### Connection
- `chiller.connect() -> bool`
- `chiller.disconnect() -> bool`
- `chiller.get_status() -> dict`

### Temperature
- `chiller.read_temp() -> float` — current bath temperature (°C)
- `chiller.read_set_temp() -> float` — current setpoint (°C)
- `chiller.set_temperature(target: float)` — set target temperature

### Pump
- `chiller.read_pump_level() -> int` — current pump level (1–6)
- `chiller.set_pump_level(level: int)` — set pump level

### Device Control
- `chiller.start_device()` — start pumping and cooling
- `chiller.stop_device()` — stop pumping and cooling
- `chiller.set_keylock(locked: bool)` — lock/unlock front panel

### Status
- `chiller.read_cooling() -> str` — "OFF", "ON", "AUTO"
- `chiller.read_running() -> str` — "DEVICE RUNNING", "DEVICE STANDBY"
- `chiller.read_status() -> str` — "OK", "ERROR"

## Lauda Command Reference

Read commands: `IN_PV_00` (temp), `IN_SP_00` (setpoint), `IN_SP_01` (pump), `IN_SP_02` (cooling mode).
Write commands: `OUT_SP_00 value` (temp), `OUT_SP_01 value` (pump), `START`, `STOP`.
