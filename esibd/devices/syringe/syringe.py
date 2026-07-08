# pylint: disable=[missing-module-docstring]  # see class docstrings
import re
import threading
import time
from typing import cast

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, getTestMode, parameterDict
from esibd.devices.com_helper import getComPort
from esibd.devices.lab_telemetry import ChannelThrottle, getLabSink
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [SyringePump]


HK_PUSH_INTERVAL_S = 30  # pump run-state cadence; displaced-volume telemetry stays on ChannelThrottle
STATUS_POLL_INTERVAL_S = 15  # slow serial poll (status + displaced): every command blocks ~1 s on the wire — per-cycle
# polling starved the controller lock on real hardware ('Could not acquire lock', 2026-07-08). Run state is
# primarily click-driven; the poll only backs the auto-stop / front-panel-stop detection.
BAUDRATE = 38400  # notebook 006 (real pump on COM29); the esibd_bs class default 9600 gets no replies
UNIT_ITEMS = ('mL/min', 'mL/hr', 'μL/min', 'μL/hr')  # EXACT keys of SyringePump.set_units
UNIT_TO_ML_PER_S = {'mL/min': 1 / 60, 'mL/hr': 1 / 3600, 'μL/min': 1 / 60000, 'μL/hr': 1 / 3600000}
EMPTY_FRACTION = 0.95  # displaced >= this fraction of the syringe volume counts as an empty syringe
STOPPED_POLLS_CONFIRM = 2  # consecutive 'stopped' status polls before the GUI reacts (one glitchy reply is ignored)


def payloadLines(response: list, command: str = '') -> list[str]:
    """Strip the command echo and the '>' prompt from a pump reply.

    The pump echoes every command and terminates with a prompt, e.g.
    ['pump status', '0', '>'] — the old plugin parsed the echo (notebook 006).
    """
    lines = []
    for line in response:
        text = str(line).strip()
        if not text or text == '>':
            continue
        if command and text.lower().startswith(command.lower()):
            continue
        lines.append(text)
    return lines


def parseRunning(lines: list[str]) -> 'bool | None':
    """Interpret a 'pump status' payload: numeric code (real pump, 0 = stopped) or a status word (Test Mode)."""
    for text in lines:
        lowered = text.lower()
        if lowered.lstrip('+-').isdigit():
            return int(lowered) != 0
        if 'pause' in lowered or 'stop' in lowered:
            return False
        if 'pump' in lowered or 'run' in lowered or 'infus' in lowered or 'withdraw' in lowered:
            return True
    return None


def parseDisplacedML(lines: list[str]) -> 'float | None':
    """First number in a 'dispensed volume' payload, converted to mL (µL payloads divided by 1000)."""
    for text in lines:
        match = re.search(r'[-+]?\d+(?:\.\d+)?', text)
        if match:
            value = abs(float(match.group()))
            lowered = text.lower()
            if 'ul' in lowered or 'µl' in lowered or 'μl' in lowered:
                value /= 1000.0
            return value
    return None


class SyringePump(Device):
    """Syringe pump feeding the ESI emitter.

    One pump, one channel (the first real row wins): the channel VALUE is the flow
    rate in the per-channel Unit (dropdown, the four units of the pump firmware),
    the On/Off logic starts and PAUSES the pump, and read-only indicators show the
    displaced volume and the run state.

    SAFETY MODEL (notebook 006): the pump has no mechanical end-stop detection — it
    computes the run length from the CONFIGURED syringe volume + diameter and
    auto-stops when that volume is displaced. 'pause' keeps the displaced-distance
    memory (resume is safe); 'stop' ZEROES it (a start after a mid-syringe stop, or
    after a completed run, overruns into the hard stop and can damage the pump).
    Therefore:

    - On  = a BARE start (resume-safe). The pump keeps its parameters in its own
      memory; rate/unit edits are sent at edit time, never by the On button.
    - Off = pause. The plugin NEVER sends stop on its own — not even on close.
    - 'New syringe' toolbar action = the ONLY stop path: zeroes the counter and sends
      syringe volume/diameter/units/rate. Use it exactly when a freshly filled
      syringe is in the holder. Blocked while On.
    - On is refused while the plugin believes the syringe is empty (displaced >= 95 percent
      of the configured volume, or a completed run was detected) — refill, then reset.
    - An externally stopped/finished pump is detected by status polling and the GUI
      toggle syncs Off.

    Syringe volume + diameter are advanced channel parameters, sent ONLY by the
    New-syringe reset (they define the pump's hard-stop math). Current syringe set:
    1 mL / 4.64 mm. Pump replies take ~1 s each (notebook 006) — keep the interval
    at 3 s or slower.
    """

    name = 'SyringePump'
    version = '2.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = 'mL/hr'
    iconFile = 'SyringePump.png'
    useOnOffLogic = True
    channels: 'list[PumpChannel]'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = PumpChannel

    def initGUI(self) -> None:
        super().initGUI()
        self.addAction(event=lambda: self.newSyringeDialog(), icon='newsyringe.png',
                       toolTip='New syringe: zero the displaced-volume counter and send syringe volume/diameter/units/rate. '
                               'ONLY after refilling/replacing the syringe — the pump computes its safe travel from this.')
        self.controller = SyringePumpController(controllerParent=self)

    def getChannels(self) -> 'list[PumpChannel]':
        return cast('list[PumpChannel]', super().getChannels())

    def getDefaultSettings(self) -> dict[str, dict]:
        settings = super().getDefaultSettings()
        settings[f'{self.name}/Interval'][Parameter.VALUE] = 3000  # two serial reads per cycle at ~1 s each (nb 006: replies are slow)
        return settings

    def setOn(self, on: 'bool | None' = None) -> None:
        """Refuse to start into an empty syringe — the pump would overrun its mechanical hard stop (notebook 006)."""
        requested = on if on is not None else self.isOn()
        if requested and self.controller:
            reason = self.controller.startBlockedReason()
            if reason:
                if self.isOn():
                    self.onAction.state = False  # revert the click; setChecked fires no event
                self.print(f'Pump start blocked: {reason} Refill/replace the syringe, then run the New-syringe reset (toolbar) — '
                           'a bare start would drive the pusher into the mechanical hard stop.', flag=PRINT.ERROR)
                return
        super().setOn(on)

    def onPumpStopped(self, empty: bool) -> None:
        """Sync the GUI toggle after the pump stopped on its own (auto-stop at the configured volume, or a front-panel stop)."""
        if empty:
            self.print('Pump run complete — the configured syringe volume is displaced. '
                       'Refill/replace the syringe, then run the New-syringe reset before the next start.', flag=PRINT.WARNING)
        else:
            self.print('Pump reports stopped (front panel?) — toggle synced Off. On resumes safely (displaced counter is kept).',
                       flag=PRINT.WARNING)
        if self.isOn():
            self.setOn(False)

    def newSyringeDialog(self) -> None:
        """Confirm, then run the only stop path: zero the counter and configure the fresh syringe."""
        if not self.controller.initialized:
            self.print('Initialize communication first.', flag=PRINT.WARNING)
            return
        if self.isOn():
            self.print('Toggle the pump Off (pause) before the New-syringe reset.', flag=PRINT.WARNING)
            return
        channel = next((channel for channel in self.getChannels() if channel.real and channel.enabled), None)
        if channel is None:
            self.print('No enabled real channel.', flag=PRINT.WARNING)
            return
        box = QMessageBox(QMessageBox.Icon.Warning, 'New syringe?',
                          'Only confirm with a FRESHLY FILLED syringe in the holder.\n\n'
                          'This sends stop (zeroes the pump\'s displaced-volume counter) and configures:\n'
                          f'    syringe {channel.volume:g} mL, diameter {channel.diameter:g} mm,\n'
                          f'    rate {channel.value:g} {channel.rateUnit}.\n\n'
                          'Confirming with a partially used syringe makes the next run overrun the hard stop.',
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.setWindowFlags(box.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        if box.exec() == QMessageBox.StandardButton.Yes:
            self.controller.newSyringeFromThread()

    def closeCommunication(self) -> None:
        self.setOn(False)  # GUI state; the controller teardown pauses the pump itself, synchronously and under the lock
        super().closeCommunication()


class PumpChannel(Channel):
    """Channel for the syringe pump: flow rate + unit in, displaced volume and run state out."""

    UNIT = 'Unit'
    STATUS = 'Status'
    COM = 'COM'
    DISPLACED = 'Displaced'
    VOLUME = 'Volume'
    DIAMETER = 'Diameter'
    channelParent: SyringePump

    def getDefaultChannel(self) -> dict[str, dict]:

        self.rateUnit: str
        self.status: str
        self.com: int
        self.displaced: float
        self.volume: float
        self.diameter: float

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'Rate'
        channel[self.VALUE][Parameter.MIN] = 0
        channel[self.VALUE][Parameter.MAX] = 100000
        channel[self.UNIT] = parameterDict(value='mL/hr', parameterType=PARAMETERTYPE.COMBO, items=', '.join(UNIT_ITEMS), fixedItems=True,
                                           advanced=False, header='Unit', attr='rateUnit', event=self.unitChanged,
                                           toolTip='Flow-rate unit of the pump firmware. A change sends units + rate to the pump immediately\n'
                                                   '(the pump stores them in its own memory; the On button is a bare start).')
        channel[self.DISPLACED] = parameterDict(value=0.0, parameterType=PARAMETERTYPE.FLOAT, advanced=False, header='Done (mL)', indicator=True,
                                                attr='displaced', toolTip='Displaced volume reported by the pump, in mL (read-only).\n'
                                                                          'The pump keeps it across pause/resume; only the New-syringe reset zeroes it.')
        channel[self.STATUS] = parameterDict(value='?', parameterType=PARAMETERTYPE.TEXT, advanced=False, header='Status', indicator=True, attr='status',
                                             toolTip='Run state polled from the pump (read-only). EMPTY = configured volume displaced; '
                                                     'refill + New-syringe reset required.')
        channel[self.VOLUME] = parameterDict(value=1.0, parameterType=PARAMETERTYPE.FLOAT, advanced=True, header='Syringe (mL)', attr='volume',
                                             toolTip='Syringe volume in mL. Sent ONLY by the New-syringe reset — the pump computes its safe travel '
                                                     '(auto-stop) from volume + diameter.')
        channel[self.DIAMETER] = parameterDict(value=4.64, parameterType=PARAMETERTYPE.FLOAT, advanced=True, header='Dia (mm)', attr='diameter',
                                               toolTip='Syringe inner diameter in mm (4.64 = the 1 mL set). Sent ONLY by the New-syringe reset.')
        channel[self.COM] = parameterDict(value=getComPort('Syringe_Pump', default=29), minimum=1, maximum=99, parameterType=PARAMETERTYPE.INT,
                                          advanced=True, header='COM', attr='com',
                                          toolTip='COM port number of the syringe pump (notebook 006: COM29 at 38400 baud). '
                                                  'One pump, one port — the controller uses the first real row.')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.insertDisplayedParameter(self.UNIT, before=self.DISPLAY)
        self.insertDisplayedParameter(self.DISPLACED, before=self.DISPLAY)
        self.insertDisplayedParameter(self.STATUS, before=self.DISPLAY)
        self.displayedParameters.append(self.VOLUME)
        self.displayedParameters.append(self.DIAMETER)
        self.displayedParameters.append(self.COM)

    def tempParameters(self) -> list[str]:
        return [*super().tempParameters(), self.DISPLACED, self.STATUS]

    def unitChanged(self) -> None:
        """Send units + rate to the pump right away — running or paused (the On button stays a bare start)."""
        if self.real:
            self.channelParent.controller.applyUnitFromThread(self)

    def realChanged(self) -> None:
        for name in (self.UNIT, self.STATUS, self.COM, self.DISPLACED, self.VOLUME, self.DIAMETER):
            self.getParameterByName(name).setVisible(self.real)
        super().realChanged()


class SyringePumpController(DeviceController):
    """Controller for the syringe pump (esibd_bs SyringePump on SerialDeviceBase). One pump, one COM port."""

    controllerParent: SyringePump

    class SignalCommunicate(DeviceController.SignalCommunicate):
        """Bundle pyqtSignals."""

        pumpStoppedSignal = pyqtSignal(bool)
        """Pump stopped on its own (bool = syringe empty); handled in the main thread to sync the On/Off toggle."""

    def __init__(self, controllerParent: SyringePump) -> None:
        super().__init__(controllerParent=controllerParent)
        self.pump = None  # esibd_bs SyringePump instance
        self.telemetryThrottle = ChannelThrottle()
        self.hkPushTime = 0.0  # last run-state push (epoch s)
        self.runComplete = False  # a finished run was detected — On stays blocked until the New-syringe reset
        self.lastRunning: 'bool | None' = None  # last known run state (click-driven; the slow poll corrects it)
        self.stoppedPolls = 0  # consecutive 'stopped' polls while the GUI is On (debounce)
        self.lastStatusPoll = 0.0  # last slow serial poll (epoch s)
        self.signalComm.pumpStoppedSignal.connect(self.controllerParent.onPumpStopped)

    def initializeValues(self, reset: bool = False) -> None:  # noqa: ARG002
        """Values array holds the displaced volume (mL) per channel."""
        channels = self.controllerParent.getChannels()
        if channels:
            self.values = np.full(len(channels), fill_value=np.nan, dtype=np.float32)

    def _comPort(self) -> int:
        """COM port from the first real channel (one pump, one port; fallback: the com_ports.json key)."""
        for channel in self.controllerParent.getChannels():
            if channel.real:
                return channel.com
        return getComPort('Syringe_Pump', default=29)

    def _activeChannel(self) -> 'PumpChannel | None':
        """The first enabled real channel — the one row that drives the single pump."""
        return next((channel for channel in self.controllerParent.getChannels() if channel.real and channel.enabled), None)

    def runInitialization(self) -> None:
        try:
            from devices.syringe_pump import SyringePump as SyringePumpDevice

            com = self._comPort()
            sink = getLabSink()
            if sink is None:
                self.print('Telemetry sink unavailable (LAB_CONFIG not set?) — running without telemetry.db writes.', flag=PRINT.DEBUG)
            self.print(f'Connecting to the syringe pump on COM{com} at {BAUDRATE} baud...')
            self.pump = SyringePumpDevice(device_id='Syringe_Pump', port=f'COM{com}', baudrate=BAUDRATE, sink=sink)
            if not self.pump.connect():
                self.print(f'Failed to connect to the syringe pump on COM{com}.', flag=PRINT.ERROR)
                self.pump = None
                return
            self.print(f'Syringe pump connected on COM{com}.')
            realChannels = [channel for channel in self.controllerParent.getChannels() if channel.real]
            if len(realChannels) > 1:
                self.print('More than one real channel configured — there is ONE pump; the first real row wins.', flag=PRINT.WARNING)
            if any(channel.com != com for channel in realChannels):
                self.print(f'Real channels disagree on the COM port — using COM{com} from the first real row.', flag=PRINT.WARNING)
            # NO motion/config commands here: the pump may sit paused mid-syringe and its counter memory must survive an Explorer restart.
            self.signalComm.initCompleteSignal.emit()
        except Exception as e:  # noqa: BLE001
            self.print(f'Error initializing the syringe pump: {e}', flag=PRINT.ERROR)
            self.pump = None
        finally:
            self.initializing = False

    def fakeInitialization(self) -> None:
        """Create a simulated esibd_bs device before faking init, so Test Mode telemetry lands in the shared db with sim=1."""
        self.pump = None
        try:
            from devices.syringe_pump import SyringePump as SyringePumpDevice

            sink = getLabSink()
            if sink is not None:
                self.pump = SyringePumpDevice(device_id='Syringe_Pump', port=f'COM{self._comPort()}', baudrate=BAUDRATE, sink=sink, test_mode=True)
                self.pump.connect()
        except Exception as e:  # noqa: BLE001
            self.print(f'Test Mode runs without telemetry device: {e}', flag=PRINT.DEBUG)
        super().fakeInitialization()

    # =========================================================================
    #     Safety
    # =========================================================================

    def startBlockedReason(self) -> str:
        """Why On must be refused right now ('' = clear to start). The pump would overrun its hard stop on a start into an empty syringe."""
        if self.runComplete:
            return 'the last run displaced the full configured syringe volume.'
        channel = self._activeChannel()
        if channel is not None and channel.volume > 0 and self.values is not None:
            index = self.controllerParent.getChannels().index(channel)
            displaced = float(self.values[index]) if index < len(self.values) else np.nan
            if np.isfinite(displaced) and displaced >= EMPTY_FRACTION * channel.volume:
                return f'the pump reports {displaced:.3f} of {channel.volume:g} mL displaced — the syringe counts as empty.'
        return ''

    def _checkAutoStop(self, running: 'bool | None') -> None:
        """Detect a pump that stopped on its own while the GUI is On (auto-stop at the configured volume, or a front-panel stop).

        Debounced over STOPPED_POLLS_CONFIRM cycles. Runs in the acquisition thread; the GUI sync happens in the main thread.
        """
        self.lastRunning = running
        if running is False and self.controllerParent.isOn():
            self.stoppedPolls += 1
            if self.stoppedPolls >= STOPPED_POLLS_CONFIRM:
                self.stoppedPolls = 0
                empty = bool(self.startBlockedReason())
                if empty:
                    self.runComplete = True
                self.signalComm.pumpStoppedSignal.emit(empty)
        else:
            self.stoppedPolls = 0

    # =========================================================================
    #     Read loop
    # =========================================================================

    def runAcquisition(self) -> None:
        """Framework loop, but lock-frugal: readNumbers is a serial no-op on most cycles (15 s poll
        gate), so the lock is only taken when the poll is actually due. Without this, every idle
        cycle competed with a running toggle — which holds the lock 2-5 s at ~1 s per pump command —
        and printed 'Could not acquire lock to acquire data' (real HW, 2026-07-08). A due poll that
        finds the lock busy skips SILENTLY (no timeoutMessage) and retries next cycle; the toggle
        pushes lastStatusPoll out when it finishes anyway.
        """
        while self.acquiring:
            testMode = getTestMode()
            if testMode or time.time() - self.lastStatusPoll >= STATUS_POLL_INTERVAL_S:
                with self.lock.acquire_timeout(1) as lock_acquired:
                    if lock_acquired:
                        self.fakeNumbers() if testMode else self.readNumbers()
            self.signalComm.updateValuesSignal.emit()
            # release lock before waiting!
            time.sleep(self.getDevice().interval / 1000)

    def readNumbers(self) -> None:
        """Slow poll only (every STATUS_POLL_INTERVAL_S): run state + displaced volume.

        Every pump command blocks ~1 s on the wire — polling each acquisition cycle starved the
        controller lock on real hardware (2026-07-08). Most cycles are a serial no-op; the run
        state is primarily set by the On/Off clicks, this poll just catches auto-stop and
        front-panel stops.
        """
        if self.pump is None or self.values is None or time.time() - self.lastStatusPoll < STATUS_POLL_INTERVAL_S:
            return
        channel = self._activeChannel()
        if channel is None:
            return
        self.lastStatusPoll = time.time()
        index = self.controllerParent.getChannels().index(channel)
        try:
            displaced = parseDisplacedML(payloadLines(self.pump.get_displaced_volume(), 'dispensed volume'))
            if displaced is not None:
                self.values[index] = displaced
                self.errorCount = 0
            running = parseRunning(payloadLines(self.pump.get_pump_status(), 'pump status'))
            self._checkAutoStop(running)
            if displaced is None and running is None:
                self.errorCount += 1
        except Exception as e:  # noqa: BLE001
            self.print(f'Error reading the pump: {e}', flag=PRINT.ERROR)
            self.errorCount += 1
        self._pushTelemetry()

    def fakeNumbers(self) -> None:
        """Simulate the real pump's accounting: displaced integrates the rate and the pump auto-stops at the configured volume."""
        if self.values is None:
            return
        interval_s = getattr(self.controllerParent, 'interval', 3000) / 1000
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if not (channel.enabled and channel.real and i < len(self.values)):
                continue
            displaced = float(self.values[i]) if np.isfinite(self.values[i]) else 0.0
            running = self.controllerParent.isOn() and channel.volume > 0 and displaced < channel.volume
            if running:
                displaced = min(channel.volume, displaced + channel.value * UNIT_TO_ML_PER_S.get(channel.rateUnit, 1 / 3600) * interval_s)
            self.values[i] = displaced
            self._checkAutoStop(running)
            break  # one pump — only the first enabled real row simulates
        self._pushTelemetry()

    def updateValues(self) -> None:
        if self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real and i < len(self.values):
                if np.isfinite(self.values[i]):
                    channel.displaced = float(self.values[i])
                if self.runComplete:
                    channel.status = 'EMPTY — reset'
                elif self.lastRunning is None:
                    channel.status = '?'
                else:
                    channel.status = 'pumping' if self.lastRunning else 'idle'

    # =========================================================================
    #     Telemetry
    # =========================================================================

    def _pushTelemetry(self) -> None:
        """Write the displaced volume to the telemetry sink, throttled per channel (>=5 s)."""
        if self.pump is None or self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if not (channel.enabled and channel.real and i < len(self.values)):
                continue
            value = float(self.values[i])
            if not np.isfinite(value) or not self.telemetryThrottle.ready(channel.name):
                continue
            self.pump.log_sample('Displaced_Vol', value, 'mL', '.3f')
        self._pushHousekeeping()

    def _pushHousekeeping(self, force: bool = False) -> None:
        """Push the pump run state to the telemetry sink every HK_PUSH_INTERVAL_S.

        Rides the read loop — never the esibd_bs hk worker. force=True skips the time gate
        (final push on closeCommunication; caller must hold the controller lock).
        """
        if self.pump is None or self.lastRunning is None or (not force and time.time() - self.hkPushTime < HK_PUSH_INTERVAL_S):
            return
        self.hkPushTime = time.time()
        try:
            self.pump.log_sample('Pump_Running', 1 if self.lastRunning else 0)
        except Exception as e:  # noqa: BLE001
            self.print(f'Run-state push failed: {e}', flag=PRINT.DEBUG)

    # =========================================================================
    #     Control
    # =========================================================================

    def applyValue(self, channel: PumpChannel) -> None:
        """Send a changed flow rate to the pump right away — running or paused (rate changes never
        touch the displaced counter). The On button itself stays a bare start: the pump keeps its
        parameters in its own memory (user 2026-07-08)."""
        if self.pump is None or not (channel.enabled and channel.real):
            return
        with self.lock.acquire_timeout(5, timeoutMessage=f'Cannot acquire lock to set {channel.name}.') as lock_acquired:
            if lock_acquired:
                self.pump.pump_rate = channel.value
                self.pump.set_rate(channel.value)
                self.print(f'Set {channel.name} rate to {channel.value:g} {channel.rateUnit}.', flag=PRINT.TRACE)

    def applyUnitFromThread(self, channel: PumpChannel) -> None:
        """Live unit change: re-send units + rate without blocking the main thread."""
        if self.pump is not None and self.initialized:
            threading.Thread(target=self.applyUnit, args=(channel,), name=f'{self.controllerParent.name} applyUnitThread', daemon=True).start()

    def applyUnit(self, channel: PumpChannel) -> None:
        with self.lock.acquire_timeout(5, timeoutMessage='Cannot acquire lock to set the rate unit.') as lock_acquired:
            if lock_acquired:
                self.pump.units = channel.rateUnit
                self.pump.pump_rate = channel.value
                self.pump.set_units(channel.rateUnit)
                self.pump.set_rate(channel.value)  # the numeric rate means something new in the new unit — always re-send the pair
                self.print(f'Rate unit set to {channel.rateUnit} (rate re-sent: {channel.value:g}).')

    def toggleOnFromThread(self, parallel: bool = True) -> None:  # noqa: ARG002
        """ALWAYS toggle in a background thread: the framework passes parallel=False from setOn,
        but pump commands block ~1 s each on the wire — the start sequence froze the GUI for
        2-5 s on real hardware (2026-07-08). The lock serializes overlapping toggles."""
        super().toggleOnFromThread(parallel=True)

    def toggleOn(self) -> None:
        super().toggleOn()
        if self.pump is None:
            return
        on = self.controllerParent.isOn()
        try:
            # ~1 s per command on the wire (nb 006) — the start sequence is displaced-check + start
            with self.lock.acquire_timeout(10, timeoutMessage='Cannot acquire lock to toggle the syringe pump.') as lock_acquired:
                if not lock_acquired:
                    return
                channel = self._activeChannel()
                if channel is None:
                    self.print('No enabled real channel.', flag=PRINT.WARNING)
                    return
                index = self.controllerParent.getChannels().index(channel)
                if on:
                    # Authoritative empty check on a FRESH displaced reading (the setOn guard only sees
                    # the last slow poll): a start into an empty syringe overruns the hard stop (nb 006).
                    displaced = parseDisplacedML(payloadLines(self.pump.get_displaced_volume(), 'dispensed volume'))
                    if displaced is not None:
                        self.values[index] = displaced
                    if self.startBlockedReason():
                        self.runComplete = True
                        self.signalComm.pumpStoppedSignal.emit(True)  # main thread prints + reverts the toggle
                        return
                    # BARE start (user 2026-07-08): parameters live in the pump's own memory — rate/unit
                    # edits are sent at edit time (applyValue/applyUnit), volume/diameter by the New-syringe
                    # reset. NEVER stop before start — stop zeroes the counter and the next run overruns
                    # the hard stop (nb 006).
                    self.pump.start_pump()
                    self.stoppedPolls = 0
                    self.lastRunning = True  # run state is click-driven; the slow poll corrects it
                    self.print('Syringe pump started (resume-safe: displaced counter kept).')
                else:
                    self.pump.pause_pump()  # pause keeps the displaced-distance memory; stop would zero it
                    self.lastRunning = False
                    # one displaced read at the pause point keeps the Done (mL) indicator honest without live polling
                    displaced = parseDisplacedML(payloadLines(self.pump.get_displaced_volume(), 'dispensed volume'))
                    if displaced is not None:
                        self.values[index] = displaced
                    self.print('Syringe pump paused (displaced counter kept — On resumes safely).')
                self.lastStatusPoll = time.time()  # the toggle just talked to the pump — push the next slow poll out
                self._pushHousekeeping(force=True)  # telemetry records the new run state right away
        except Exception as e:  # noqa: BLE001
            self.print(f'Error toggling the syringe pump: {e}', flag=PRINT.ERROR)

    def newSyringeFromThread(self) -> None:
        """Run the New-syringe reset without blocking the main thread (confirm dialog already passed)."""
        if self.pump is not None:
            threading.Thread(target=self.newSyringe, name=f'{self.controllerParent.name} newSyringeThread', daemon=True).start()
        else:
            self.print('Pump not connected.', flag=PRINT.WARNING)

    def newSyringe(self) -> None:
        """The ONLY stop path: zero the displaced counter and configure the fresh syringe (volume, diameter, units, rate).

        Safe exactly because a freshly filled syringe is in the holder (confirm dialog) — the zeroed
        counter then matches the physical plunger travel again. The pump is left stopped; the user
        toggles On to start.
        """
        channel = self._activeChannel()
        if channel is None:
            self.print('No enabled real channel.', flag=PRINT.WARNING)
            return
        try:
            # 5 commands at ~1 s each on the wire
            with self.lock.acquire_timeout(15, timeoutMessage='Cannot acquire lock for the New-syringe reset.') as lock_acquired:
                if not lock_acquired:
                    return
                self.pump.volume = channel.volume
                self.pump.diameter = channel.diameter
                self.pump.units = channel.rateUnit
                self.pump.pump_rate = channel.value
                self.pump.stop_pump()  # zeroes the displaced counter — matches the fresh syringe
                self.pump.apply_parameters(rate=channel.value)  # volume, diameter, units, rate
                self.runComplete = False
                self.stoppedPolls = 0
                self.lastRunning = False
                if self.values is not None:
                    self.values[:] = 0.0
                self.hkPushTime = 0.0
            self.print(f'New syringe configured: {channel.volume:g} mL, dia {channel.diameter:g} mm, '
                       f'rate {channel.value:g} {channel.rateUnit}. Counter zeroed; pump stopped — toggle On to start.')
        except Exception as e:  # noqa: BLE001
            self.print(f'New-syringe reset failed: {e}', flag=PRINT.ERROR)

    def closeCommunication(self) -> None:
        super().closeCommunication()  # stops acquisition first
        if self.pump is not None:
            with self.lock.acquire_timeout(5, timeoutMessage='Cannot acquire lock to close the syringe pump.'):
                try:
                    self.pump.pause_pump()  # NEVER stop on close — the counter memory must survive into the next session
                    self.lastRunning = False
                except Exception:  # noqa: BLE001
                    pass
                # final push so telemetry records the paused state
                self._pushHousekeeping(force=True)
                try:
                    self.pump.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            self.pump = None
        self.initialized = False
