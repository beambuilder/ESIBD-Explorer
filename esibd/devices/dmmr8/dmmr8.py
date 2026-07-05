# pylint: disable=[missing-module-docstring]  # see class docstrings
import time

import numpy as np

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, DeviceController, Parameter, getTestMode, parameterDict
from esibd.devices.com_helper import getComPort
from esibd.devices.lab_telemetry import ChannelThrottle, getLabSink
from esibd.plugins import Device, Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [DMMR8]


NUM_MODULES = 5  # Number of DPA-1F modules in this setup

HK_PUSH_INTERVAL_S = 30  # electronics housekeeping (temps, enable state) cadence; current telemetry stays on ChannelThrottle


class DMMR8(Device):
    """CGC DMMR-8 picoammeter with DPA-1F current measurement modules.

    Reads ion currents from up to 5 DPA-1F modules connected to a single
    DMMR-8 controller via serial (COM) port. Uses automatic current mode
    for continuous polling of all modules.
    """

    name = 'DMMR8'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.OUTPUTDEVICE
    unit = 'pA'
    iconFile = 'DMMR8.png'
    useBackgrounds = True
    channels: 'list[CurrentChannel]'

    # type hints for settings
    comPort: int

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = CurrentChannel
        self.controller = DMMR8Controller(controllerParent=self)

    def initGUI(self) -> None:
        super().initGUI()
        self.addAction(event=self.resetCharge, toolTip=f'Reset accumulated charge for {self.name}.', icon='battery-empty.png')

    def getDefaultSettings(self) -> dict[str, dict]:
        settings = super().getDefaultSettings()
        settings[f'{self.name}/Interval'][Parameter.VALUE] = 200
        settings[f'{self.name}/COM Port'] = parameterDict(value=getComPort('pA', default=8), minimum=1, maximum=99,
                                                           toolTip='COM port number for the DMMR-8 controller.',
                                                           parameterType=PARAMETERTYPE.INT, attr='comPort')
        return settings

    def resetCharge(self) -> None:
        """Reset the charge of each channel."""
        for channel in self.channels:
            channel.resetCharge()

    def closeCommunication(self) -> None:
        super().closeCommunication()


class CurrentChannel(Channel):
    """UI for a single DPA-1F current measurement module."""

    CHARGE = 'Charge'
    ADDRESS = 'Address'
    channelParent: DMMR8

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.preciseCharge = 0

    def getDefaultChannel(self) -> dict[str, dict]:

        self.charge: float
        self.address: int

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'I (pA)'
        channel[self.CHARGE] = parameterDict(value=0, parameterType=PARAMETERTYPE.FLOAT, advanced=False, header='C (pAh)', indicator=True, attr='charge')
        channel[self.ADDRESS] = parameterDict(value=0, minimum=0, maximum=7, parameterType=PARAMETERTYPE.INT, advanced=True,
                                              header='Addr', toolTip='Module address on the DMMR-8 controller (0-7).', attr='address')
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.insertDisplayedParameter(self.CHARGE, before=self.DISPLAY)
        self.displayedParameters.append(self.ADDRESS)

    def tempParameters(self) -> list[str]:
        return [*super().tempParameters(), self.CHARGE]

    def appendValue(self, lenT, nan=False) -> None:
        super().appendValue(lenT, nan=nan)
        if not nan and not np.isnan(self.value) and not np.isinf(self.value):
            chargeIncrement = (self.value - self.background) * self.channelParent.interval / 1000 / 3600 if self.values.size > 1 else 0
            self.preciseCharge += chargeIncrement
            self.charge = self.preciseCharge  # pylint: disable=[attribute-defined-outside-init]

    def clearHistory(self) -> None:
        super().clearHistory()
        self.resetCharge()

    def resetCharge(self) -> None:
        """Reset the charge."""
        self.charge = 0  # pylint: disable=[attribute-defined-outside-init]
        self.preciseCharge = 0

    def realChanged(self) -> None:
        self.getParameterByName(self.ADDRESS).setVisible(self.real)
        super().realChanged()


class DMMR8Controller(DeviceController):
    """Controller for the DMMR-8 picoammeter. Manages communication with all modules through one COM port."""

    controllerParent: DMMR8

    def __init__(self, controllerParent: DMMR8) -> None:
        super().__init__(controllerParent=controllerParent)
        self.pa = None  # PA device instance
        self.telemetryThrottle = ChannelThrottle()
        self.hkPushTime = 0.0  # last housekeeping push (epoch s)

    def runInitialization(self) -> None:
        try:
            from devices.cgc import PA

            com = self.controllerParent.comPort
            sink = getLabSink()
            if sink is None:
                self.print('Telemetry sink unavailable (LAB_CONFIG not set?) — running without telemetry.db writes.', flag=PRINT.DEBUG)
            self.print(f'Connecting to DMMR-8 on COM{com}...')
            self.pa = PA(device_id='DMMR8', com=com, baudrate=230400, sink=sink)

            # connect() runs the full strict bring-up (open -> baud -> set_enable -> set_automatic_current) since P6.2.
            if not self.pa.connect():
                self.print(f'Failed to connect to DMMR-8 on COM{com}.', flag=PRINT.ERROR)
                return

            self.print(f'DMMR-8 initialized on COM{com} with {NUM_MODULES} modules.')
            self.signalComm.initCompleteSignal.emit()
        except Exception as e:  # noqa: BLE001
            self.print(f'Error initializing DMMR-8: {e}', flag=PRINT.ERROR)
        finally:
            self.initializing = False

    def fakeInitialization(self) -> None:
        """Create a simulated esibd_bs device before faking init, so Test Mode telemetry lands in the shared db with sim=1."""
        self.pa = None
        try:
            from devices.cgc import PA

            sink = getLabSink()
            if sink is not None:
                self.pa = PA(device_id='DMMR8', com=self.controllerParent.comPort, baudrate=230400, sink=sink, test_mode=True)
                self.pa.connect()
        except Exception as e:  # noqa: BLE001
            self.print(f'Test Mode runs without telemetry device: {e}', flag=PRINT.DEBUG)
        super().fakeInitialization()

    def _pushTelemetry(self) -> None:
        """Write the freshly read channel values to the telemetry sink, throttled per channel (>=5 s)."""
        if self.pa is None or self.values is None:
            return
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if not (channel.enabled and channel.real and i < len(self.values)):
                continue
            value = float(self.values[i])
            if not np.isfinite(value) or not self.telemetryThrottle.ready(channel.name):
                continue
            self.pa.log_sample(channel.name, value, self.controllerParent.unit)
        self._pushHousekeeping()

    def _pushHousekeeping(self) -> None:
        """Push electronics housekeeping (internal temps + enable state) to the telemetry sink every HK_PUSH_INTERVAL_S.

        Rides the read loop (which already holds the controller lock) — never a second polling thread on the DLL.
        DB-only: these are not Explorer channels and never appear in the GUI; the dashboard's Electronics tab reads them.
        """
        if self.pa is None or time.time() - self.hkPushTime < HK_PUSH_INTERVAL_S:
            return
        self.hkPushTime = time.time()
        try:
            status, _volt_12v, _volt_5v0, _volt_3v3, temp_cpu = self.pa.get_housekeeping()
            if status == self.pa.NO_ERR:
                self.pa.log_sample('Temp_CPU', temp_cpu, 'degC', '.1f')
            status, base_temp = self.pa.get_base_temp()
            if status == self.pa.NO_ERR:
                self.pa.log_sample('Base_Temp', base_temp, 'degC', '.1f')
            status, enabled = self.pa.get_enable()
            if status == self.pa.NO_ERR:
                self.pa.log_sample('Enabled', 1 if enabled else 0)
        except Exception as e:  # noqa: BLE001
            self.print(f'Housekeeping push failed: {e}', flag=PRINT.DEBUG)

    def readNumbers(self) -> None:
        if self.pa is None or self.values is None:
            return
        # Build address-to-index mapping from channels
        channels = self.controllerParent.getChannels()
        addr_to_idx = {}
        for i, ch in enumerate(channels):
            if ch.enabled and ch.real:
                addr_to_idx[ch.address] = i

        # Poll get_current() to collect readings from all modules.
        # Each call returns one module's data. Poll until NO_DATA to drain the buffer.
        readings_received = 0
        max_polls = NUM_MODULES * 3  # generous limit to avoid infinite loop
        for _ in range(max_polls):
            try:
                status, address, current_a, meas_range, timestamp = self.pa.get_current()
            except Exception as e:  # noqa: BLE001
                self.print(f'Error reading current: {e}', flag=PRINT.ERROR)
                self.errorCount += 1
                break
            if status == self.pa.NO_DATA:
                break  # no more data available right now
            if status == self.pa.NO_ERR and address in addr_to_idx:
                self.values[addr_to_idx[address]] = current_a * 1e12  # convert A to pA
                readings_received += 1
                self.errorCount = 0
            elif status != self.pa.NO_ERR and status != self.pa.NO_DATA:
                self.errorCount += 1

        if readings_received == 0:
            # No data yet, short sleep to avoid busy-waiting
            time.sleep(0.01)
        self._pushTelemetry()

    def fakeNumbers(self) -> None:
        if self.values is None:
            return
        t = time.time()
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled:
                # Simulate slowly varying currents in pA range
                self.values[i] = (np.sin(t / 10 + i * 1.5) * 5 + self.rng.random() * 0.5 + 10 + i * 3)
        self._pushTelemetry()

    def closeCommunication(self) -> None:
        super().closeCommunication()
        if self.pa is not None:
            try:
                self.pa.set_automatic_current(False)
            except Exception:  # noqa: BLE001
                pass
            try:
                self.pa.set_enable(False)
            except Exception:  # noqa: BLE001
                pass
            try:
                self.pa.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self.pa = None
        self.initialized = False
