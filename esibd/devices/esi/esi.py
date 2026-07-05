# pylint: disable=[missing-module-docstring]  # see class docstrings
import time
from typing import cast

import numpy as np

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, parameterDict
from esibd.devices.com_helper import getComPort
from esibd.devices.lab_telemetry import ChannelThrottle, getLabSink
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [ESI]


HK_PUSH_INTERVAL_S = 30  # electronics housekeeping (temps, activation state) cadence; voltage telemetry stays on ChannelThrottle


class ESI(Device):
    """Contains a list of HV channels of the CGC ESI controller (electrospray high voltage).

    The controller carries up to 4 HV supply modules; the lab uses addresses 2 and 3
    (notebook 024). Supports monitor readback, a read-only output-current indicator per
    module, and On/Off logic mapped to the controller + module activation states.

    The ESI-CTRL DLL is SINGLE-INSTANCE per process (no port argument in its exports):
    one controller, one COM port, and never this plugin and an ESI notebook at the
    same time — the esibd_bs class enforces this with a connect guard.
    """

    name = 'ESI'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.INPUTDEVICE
    unit = 'V'
    iconFile = 'ESI.png'
    useMonitors = True
    useOnOffLogic = True
    channels: 'list[HVChannel]'

    # type hints for settings
    comPort: int

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = HVChannel

    def initGUI(self) -> None:
        super().initGUI()
        self.controller = ESIController(controllerParent=self)

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
        return settings

    def closeCommunication(self) -> None:
        self.setOn(False)
        self.controller.toggleOnFromThread(parallel=False)
        super().closeCommunication()


class HVChannel(Channel):
    """Channel for a single ESI HV supply module."""

    ADDRESS = 'Address'
    CURRENT = 'Current'
    channelParent: ESI

    def getDefaultChannel(self) -> dict[str, dict]:

        self.address: int
        self.current: float

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'Voltage (V)'
        channel[self.CURRENT] = parameterDict(value=0, parameterType=PARAMETERTYPE.FLOAT, advanced=False, header='I (nA)', indicator=True, attr='current',
                                              toolTip='Measured HV output current (read-only).')
        channel[self.ADDRESS] = parameterDict(value=2, minimum=0, maximum=3, parameterType=PARAMETERTYPE.INT, advanced=True,
                                              header='Addr', toolTip='HV module address on the ESI controller (0-3; the lab uses 2 and 3).', attr='address')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.insertDisplayedParameter(self.CURRENT, before=self.DISPLAY)
        self.displayedParameters.append(self.ADDRESS)

    def tempParameters(self) -> list[str]:
        return [*super().tempParameters(), self.CURRENT]

    def monitorChanged(self) -> None:
        self.updateWarningState(self.enabled and self.channelParent.controller.acquiring
                                and ((self.channelParent.isOn() and abs(self.monitor - self.value) > 5)
                                or (not self.channelParent.isOn() and abs(self.monitor - 0) > 5)))

    def realChanged(self) -> None:
        self.getParameterByName(self.ADDRESS).setVisible(self.real)
        self.getParameterByName(self.CURRENT).setVisible(self.real)
        super().realChanged()


class ESIController(DeviceController):
    """Controller for the CGC ESI controller. One instance, one COM port (single-instance DLL)."""

    controllerParent: ESI

    def __init__(self, controllerParent: ESI) -> None:
        super().__init__(controllerParent=controllerParent)
        self.esi = None  # esibd_bs ESI device instance
        self.currents = None  # measured module currents (nA), parallel to values
        self.telemetryThrottle = ChannelThrottle()
        self.hkPushTime = 0.0  # last housekeeping push (epoch s)

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

            # connect() runs the nb-024 bring-up (open -> set_comspeed -> set_enable) and
            # claims the process-wide single-instance slot (P6.5).
            if not self.esi.connect():
                self.print(f'Failed to connect to ESI controller on COM{com}.', flag=PRINT.ERROR)
                self.esi = None
                return

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
        super().fakeInitialization()

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
            self.esi.log_sample(channel.name, value, self.controllerParent.unit)
        self._pushHousekeeping()

    def _pushHousekeeping(self, force: bool = False) -> None:
        """Push electronics housekeeping (internal temps + HV activation state) to the telemetry sink every HK_PUSH_INTERVAL_S.

        Rides the read loop (which already holds the controller lock) — never a second polling thread on the DLL.
        DB-only: these are not Explorer channels and never appear in the GUI; the dashboard's Electronics tab reads them.
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
            status, activated = self.esi.get_activation_state()
            if status == self.esi.NO_ERR:
                self.esi.log_sample('Activated', 1 if activated else 0)
        except Exception as e:  # noqa: BLE001
            self.print(f'Housekeeping push failed: {e}', flag=PRINT.DEBUG)

    def applyValue(self, channel: HVChannel) -> None:
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
        """Read measured HV output voltage + current from every configured module."""
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
        self._pushTelemetry()

    def updateValues(self) -> None:
        if self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real and i < len(self.values):
                channel.monitor = np.nan if channel.waitToStabilize else self.values[i]
                if self.currents is not None and np.isfinite(self.currents[i]):
                    channel.current = float(self.currents[i])

    def toggleOn(self) -> None:
        super().toggleOn()
        if self.esi is None:
            return
        on = self.controllerParent.isOn()
        try:
            # Every DLL call must hold the controller lock — an unlocked call garbles the
            # in-flight exchange of the locked read/set threads (-13 storms, 2026-07-05).
            with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock to toggle ESI activation.') as lock_acquired:
                if not lock_acquired:
                    return
                status = self.esi.set_activation_state(on)
                if status != self.esi.NO_ERR:
                    self.print(f'Failed to {"activate" if on else "deactivate"} ESI controller: status {status}', flag=PRINT.WARNING)
                for channel in self.controllerParent.getChannels():
                    if channel.real:
                        mod_status = self.esi.set_module_activation_state(channel.address, on and channel.enabled)
                        if mod_status != self.esi.NO_ERR:
                            self.print(f'Failed to set module {channel.address} activation: status {mod_status}', flag=PRINT.WARNING)
            self.hkPushTime = 0.0  # next read cycle pushes the new activation state to telemetry right away
        except Exception as e:  # noqa: BLE001
            self.print(f'Error toggling ESI activation: {e}', flag=PRINT.ERROR)
        if on:
            # Give the device a moment to settle before pushing channel targets.
            time.sleep(0.2)
            for channel in self.controllerParent.getChannels():
                if channel.real:
                    self.applyValueFromThread(channel)

    def closeCommunication(self) -> None:
        super().closeCommunication()  # stops acquisition first
        if self.esi is not None:
            with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock to close the ESI controller.'):
                try:
                    self.esi.set_activation_state(False)
                except Exception:  # noqa: BLE001
                    pass
                # final hk push so telemetry records the deactivated state
                # (read loop stopped; dashboard staleness covers a crash)
                self._pushHousekeeping(force=True)
                try:
                    self.esi.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            self.esi = None
        self.initialized = False
