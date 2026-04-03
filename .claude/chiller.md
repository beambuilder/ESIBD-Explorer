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
Communication: RS-232 serial, Lauda ASCII protocol, 9600 baud, `\r\n` terminators.
Write commands expect "OK" response; read commands return numeric strings.

## Plugin Architecture

Multi-COM pattern (same as AMPR-12): one Chiller instance per unique COM port,
mapped in `ChillerController.chillers` dict.

Three classes:
- `Chiller(Device)`: INPUTDEVICE, unit='°C', monitors + on/off logic
- `ChillerChannel(Channel)`: One per physical chiller, has COM port and Pump Level params
- `ChillerController(DeviceController)`: Manages all chiller instances

## Initialization Sequence (runInitialization)

1. `ChillerDev(device_id, port=f'COM{com}', baudrate=9600)` — create instance
2. `chiller.connect()` — opens serial port
3. If device is On: `chiller.start_device()` — starts pumping and cooling
4. Emit `initCompleteSignal`

## Temperature Control

- `applyValue` → `chiller.set_temperature(target)` — sets setpoint in °C
- `readNumbers` → `chiller.read_temp()` — reads current bath temperature per channel
- When off, `applyValue` defaults to 20°C (safe room-temp fallback)

## Pump Level

Per-channel pump level (1–6), set via `chiller.set_pump_level(level)`.
Triggered from the channel UI (non-instant update, user must confirm).

## On/Off Toggle

- On: `chiller.start_device()` then apply all channel setpoints
- Off: `chiller.stop_device()`

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
