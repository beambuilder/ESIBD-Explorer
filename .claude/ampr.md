# AMPR-12 DC Voltage Supply Plugin Notes

## Overview

The CGC AMPR-12 is a DC voltage amplifier/supply with up to 12 modules, each having 4 output channels.
Our setup has two AMPR units: AMPR1000 (max 1000V) and AMPR500 (max 500V), each on its own COM port.
The ESIBD Explorer plugin is at `esibd/devices/ampr12/ampr12.py`.

## Device Class (from esibd_bs)

Import path: `from devices.cgc import AMPR` (via `src/devices/cgc/__init__.py`).

Two-layer class hierarchy:
- `AMPRBase` (`ampr_base.py`): Low-level DLL wrapper (ctypes calls to `COM-AMPR-12.dll`)
- `AMPR` (`ampr.py`): Adds logging, housekeeping thread, connect/disconnect convenience methods

The DLL lives at `esibd_bs/src/devices/cgc/ampr/AMPR-12_1_01/x64/COM-AMPR-12.dll`.

## Plugin Architecture (MIPS Pattern)

One plugin manages **both** AMPRs through per-channel COM port addressing.
Follows the MIPS plugin pattern (`esibd/devices/mips/mips.py`).

Three classes:
- `AMPR12(Device)`: INPUTDEVICE container, `useMonitors=True`, `useOnOffLogic=True`
- `VoltageChannel(Channel)`: Per-output with COM (which AMPR), Module (0-11), Ch (0-3)
- `VoltageController(DeviceController)`: Creates one AMPR instance per unique COM port

## Initialization Sequence (runInitialization)

For each unique COM port found among channels:
1. `AMPR(device_id, com=COM_PORT, baudrate=230400)` — create instance
2. `ampr.connect()` — opens serial port, sets baud rate
3. `ampr.enable_psu(True)` — enables power supplies (only if device is toggled On)
4. Emit `initCompleteSignal` to start acquisition loop

## Voltage Control (applyValue)

- `ampr.set_module_voltage(module_addr, channel, voltage)` — set one output
- Channels are 0-indexed: modules 0-11, channels 0-3
- Voltage set to 0 if channel disabled or device toggled Off
- Routes to correct AMPR instance via channel's COM port

### Apply serialization (2026-07-13)

The framework spawns one thread per channel on every toggle/apply-all
(`Device.setOn` → `updateValues(apply=True)` and `initComplete`); with many
channels that herd starves the single serial lock — 1 s timeouts dropped sets
silently on real HW. The controller therefore overrides `applyValueFromThread`:
sets are queued (deduped per channel — the worker reads `channel.value` live)
and drained by ONE `applyWorker` daemon thread, one serial exchange at a time.
`applyValue` retries the lock (2 × 2 s) and prints a loud ERROR if the set is
dropped (the framework has already stamped `lastAppliedValue`, so a dropped set
is otherwise invisible). The queue is cleared in `closeCommunication`.

### Enable robustness (2026-07-13)

`toggleOn` goes through `_setPSUEnable` (3 × 2 s lock retries, checks status
AND the enable readback). If any unit fails to enable, `_abortEnable` disables
the units that did, prints an ERROR, and reverts the On toggle in the main
thread via `revertOnSignal` — the plugin never pretends On while a PSU is
actually disabled (that was the "values not applied, edits do nothing"
real-HW symptom of 2026-07-13).

## Monitor Readback (readNumbers)

- `ampr.get_measured_module_output_voltages(module_addr)` — returns all 4 measured voltages per module
- Channels grouped by (COM, module) for efficient bulk reads
- Monitor warning triggers if |measured - setpoint| > 1V (when On) or |measured - 0| > 1V (when Off)

## Shutdown Sequence (closeCommunication)

1. `ampr.enable_psu(False)` — disable power supplies
2. `ampr.disconnect()` — close serial port

## Equation System for Linked Voltages

Built into the framework (no custom code). For lens systems with dependent deflectors:
- Set independent channels: Active=True, Real=True (user controls directly)
- Set dependent channels: Active=False, Real=True, Equation references other channels
- Example: channel "Lens1_UpDown" with equation `Lens1_Entry * 0.8 + 5`
- Evaluated via `asteval` library, supports standard math functions
- All channels across all devices can be referenced by name

## AMPR Class Key Methods Reference

### Connection
- `ampr.connect() -> bool` — open port + set baud rate
- `ampr.disconnect() -> bool` — stop HK + close port
- `ampr.get_status() -> dict` — connection state summary

### PSU Control
- `ampr.enable_psu(bool) -> (status, enable_value)` — enable/disable power supplies
- `ampr.get_state() -> (status, state_hex, state_name)` — ST_ON, ST_STBY, ST_ERROR, etc.

### Voltage Control
- `ampr.set_module_voltage(addr, ch, voltage) -> status` — set one output
- `ampr.get_module_output_voltage(addr, ch) -> (status, voltage)` — get setpoint
- `ampr.get_measured_module_output_voltages(addr) -> (status, [v0, v1, v2, v3])` — measured values
- `ampr.get_module_voltages(addr) -> dict` — all setpoints + measured per channel

### Module Discovery
- `ampr.get_module_presence() -> (status, valid, max_module, presence_list)`
- `ampr.scan_modules() -> dict` — discover connected modules

### Error Codes
- `NO_ERR = 0`: success
- `ERR_NOT_CONNECTED = -100`: port not open
- `ERR_COMMAND_RECEIVE = -10`: device did not respond

### Device States
- `ST_ON (0x0000)`: PSUs on, normal operation
- `ST_STBY (0x0002)`: PSUs standby
- `ST_ERROR (0x8000)`: general error

## Channel Configuration Persistence

Channel configs (names, COM ports, modules, channels, equations, min/max) are saved
to INI files automatically by the framework. Configure once, persists across restarts.

## Testing Reference

Notebook `esibd_bs/debugging/notebooks/019_Testing_AMPR.ipynb` has the complete
test sequence: connect, state queries, module discovery, voltage set/read, PSU enable, housekeeping.
