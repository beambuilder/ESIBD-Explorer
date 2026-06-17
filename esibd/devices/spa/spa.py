# pylint: disable=[missing-module-docstring]  # see class docstrings
import time
from typing import TYPE_CHECKING, cast

import numpy as np
import serial.serialutil

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, CompactComboBox, DeviceController, Parameter, ToolButton, dynamicImport, parameterDict
from esibd.plugins import Device, Plugin

if TYPE_CHECKING:
    from .SPA_python_example import SPA  # only used for IntelliSense


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [SPA1x0]


class SPA1x0(Device):
    """Contains a list of current channels, each corresponding to a single channel on an electron plus SPA100 or SPA120 picoammeter.

    The channels show the accumulated charge over time,
    which is proportional to the number of deposited ions. It can also
    reveal on which ion optics ions are lost.

    Note: for now this plugin is using a standard calibration file as downloading the device specific calibration file takes 100 s per device.
    As soon as this issue is resolved on the hardware side the plugin will be adjusted to use the device specific files.
    Until then, the current readings should be used more qualitatively than quantitatively.
    """

    name = 'SPA'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.OUTPUTDEVICE
    unit = 'pA'
    iconFile = 'spa.png'
    useBackgrounds = True  # record backgrounds for data correction
    useOnOffLogic = True
    channels: 'list[SPACurrentChannel]'
    controller: 'SPACurrentController'
    defaultChannel: 'SPACurrentChannel'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = SPACurrentChannel

    def getChannels(self) -> 'list[SPACurrentChannel]':
        return cast('list[SPACurrentChannel]', super().getChannels())

    def initGUI(self) -> None:
        super().initGUI()
        self.addAction(event=self.resetCharge, toolTip=f'Reset accumulated charge for {self.name}.', icon='battery-empty.png')
        self.controller = SPACurrentController(controllerParent=self)  # after all channels loaded

    def finalizeInit(self) -> None:
        super().finalizeInit()
        self.toggleUseInternalBias()

    useInternalBias: bool

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings[f'{self.name}/Interval'][Parameter.VALUE] = 100  # overwrite default value
        defaultSettings[f'{self.name}/Use internal bias'] = parameterDict(value=True, toolTip='Provides controls to set and toggle internal bias voltage.',
                                          parameterType=PARAMETERTYPE.BOOL, attr='useInternalBias', event=self.toggleUseInternalBias)
        return defaultSettings

    def resetCharge(self) -> None:
        """Reset the charge of each channel."""
        for channel in self.channels:
            channel.resetCharge()

    def toggleUseInternalBias(self) -> None:
        """Toggle display of controls for using internla bias voltage."""
        self.onAction.setVisible(self.useInternalBias)
        for channel in self.channels:
            channel.realChanged()
        self.tree.setColumnHidden(list(self.defaultChannel.getSortedDefaultChannel().keys()).index(self.defaultChannel.BIAS), False)  # noqa: FBT003
        self.tree.setColumnHidden(list(self.defaultChannel.getSortedDefaultChannel().keys()).index(self.defaultChannel.BIASON), False)  # noqa: FBT003
        self.toggleAdvanced()

    def getCOMs(self) -> list[str]:  # get list of unique used COMs
        """List of COM ports."""
        return list({channel.com for channel in self.channels if channel.real and channel.enabled})

    def updateAverage(self, channel: 'SPACurrentChannel') -> None:
        """Set flag to trigger update of average. For SPA120 the same value is used for both channels.

        :param channel: Channel for which to update Average.
        :type channel: SPACurrentChannel
        """
        for chan in self.getChannels():
            if chan is not channel and chan.com == channel.com:
                event = chan.getParameterByName(channel.AVERAGE).event
                chan.getParameterByName(channel.AVERAGE).event = None
                chan.average = channel.average
                chan.getParameterByName(channel.AVERAGE).event = event
        if self.controller and self.controller.acquiring:
            self.controller.setAverage(channel=channel)

    def updateRange(self, channel: 'SPACurrentChannel') -> None:
        """Set flag to trigger update of Range.

        :param channel: Channel for which to update Range.
        :type channel: SPACurrentChannel
        """
        if self.controller and self.controller.acquiring:
            self.controller.setRange(channel=channel)

    def updateRate(self, channel: 'SPACurrentChannel') -> None:
        """Set flag to trigger update of Sample Rate. For SPA120 the same value is used for both channels.

        :param channel: Channel for which to update Sample Rate.
        :type channel: SPACurrentChannel
        """
        for chan in self.getChannels():
            if chan is not channel and chan.com == channel.com:
                event = chan.getParameterByName(channel.RATE).event
                chan.getParameterByName(channel.RATE).event = None
                chan.rate = channel.rate
                chan.getParameterByName(channel.RATE).event = event
        if self.controller and self.controller.acquiring:
            self.controller.setRate(channel=channel)

    def updateBias(self, channel: 'SPACurrentChannel') -> None:
        """Set flag to trigger update of Bias.

        :param channel: Channel for which to update Bias.
        :type channel: SPACurrentChannel
        """
        if self.controller and self.controller.acquiring:
            self.controller.setBias(channel=channel)


class SPACurrentChannel(Channel):
    """UI for picoammeter with integrated functionality."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.preciseCharge = 0  # store independent of spin box precision to avoid rounding errors

    CHARGE = 'Charge'
    COM = 'COM'
    ID = 'ID'
    RATE = 'Rate'
    RANGE = 'Range'
    AVERAGE = 'Average'
    BIASON = 'BiasOn'
    BIAS = 'Bias'
    OVERLOAD = 'Overload'
    channelParent: SPA1x0

    def getDefaultChannel(self) -> dict[str, dict]:

        # definitions for type hinting
        self.charge: float
        self.com: str
        self.id: int
        self.devicename: str
        self.rate: int
        self.range: str
        self.average: str
        self.biasOn: bool
        self.bias: float
        self.overload: bool

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'I (pA)'
        channel[self.VALUE][Parameter.UNIT] = 'pA'
        channel[self.BACKGROUND][Parameter.HEADER] = 'BG (pA)'
        channel[self.BACKGROUND][Parameter.UNIT] = 'pA'
        channel[self.CHARGE] = parameterDict(value=0, parameterType=PARAMETERTYPE.FLOAT, advanced=False, header='C (pAh)', unit='pAh', indicator=True, attr='charge')
        channel[self.COM] = parameterDict(value='COM1', parameterType=PARAMETERTYPE.COMBO, advanced=True, toolTip='COM port',
                                        items=','.join([f'COM{x}' for x in range(1, 25)]), header='COM', attr='com')
        channel[self.ID] = parameterDict(value=1, parameterType=PARAMETERTYPE.INTCOMBO, advanced=True, toolTip='Channel ID',
                                        items='1,2', header='ID', attr='id')
        channel[self.RATE] = parameterDict(value=2, parameterType=PARAMETERTYPE.INTCOMBO, advanced=True, header='Rate (Hz)',
                                        items='2, 10, 100', attr='rate',
                                        event=lambda: self.channelParent.updateRate(channel=self), toolTip='Sample rate.')
        channel[self.RANGE] = parameterDict(value='2 mA', parameterType=PARAMETERTYPE.COMBO, advanced=True,
                                        items='200 pA, 2 nA, 20 nA, 200 nA, 2 µA, 20 µA, 200 µA, 2 mA', attr='range',  # noqa: RUF001
                                        event=lambda: self.channelParent.updateRange(channel=self), toolTip='Sample range. Defines resolution.')
        channel[self.AVERAGE] = parameterDict(value=1, parameterType=PARAMETERTYPE.INTCOMBO, advanced=True,
                                        items='1, 2, 4, 8, 16, 32, 64', attr='average',
                                        event=lambda: self.channelParent.updateAverage(channel=self), toolTip='Running average on hardware side.')
        channel[self.BIASON] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, advanced=not self.channelParent.useInternalBias, header='ON',
                                        toolTip='Apply internal bias.', attr='biasOn', event=lambda: self.channelParent.updateBias(channel=self))
        channel[self.BIAS] = parameterDict(value=0, parameterType=PARAMETERTYPE.FLOAT, advanced=not self.channelParent.useInternalBias, minimum=-40, maximum=40,
                                        toolTip='Internal bias.', attr='bias', event=lambda: self.channelParent.updateBias(channel=self), header='Bias (V)',
                                        recorded=self.channelParent.useInternalBias, unit='V')
        channel[self.OVERLOAD] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, advanced=False, indicator=True,
                                        header='OoR', toolTip='Indicates if signal is out of range.', attr='overload', restore=False)
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.insertDisplayedParameter(self.CHARGE, before=self.DISPLAY)
        self.insertDisplayedParameter(self.BIASON, before=self.DISPLAY)
        self.insertDisplayedParameter(self.BIAS, before=self.DISPLAY)
        self.insertDisplayedParameter(self.OVERLOAD, before=self.DISPLAY)
        self.displayedParameters.append(self.COM)
        self.displayedParameters.append(self.ID)
        self.displayedParameters.append(self.RATE)
        self.displayedParameters.append(self.RANGE)
        self.displayedParameters.append(self.AVERAGE)

    def initGUI(self, item: dict) -> None:
        super().initGUI(item)
        biasOn = self.getParameterByName(self.BIASON)
        value = biasOn.value
        biasOn.widget = ToolButton()
        biasOn.applyWidget()
        if biasOn.check:
            biasOn.check.setMaximumHeight(biasOn.rowHeight)
            biasOn.check.setText('ON')
            biasOn.check.setMinimumWidth(5)
            biasOn.check.setCheckable(True)
        biasOn.value = value
        self.updateColor()

    def tempParameters(self) -> list[str]:
        return [*super().tempParameters(), self.CHARGE, self.OVERLOAD]

    def enabledChanged(self) -> None:
        super().enabledChanged()
        if self.channelParent.liveDisplayActive() and self.channelParent.recording:
            self.channelParent.controller.initializeCommunication()

    def appendValue(self, lenT, nan=False) -> None:
        # calculate deposited charge in last time step for all channels
        # this does not only measure the deposition current but also on what lenses current is lost
        # make sure that the data interval is the same as used in data acquisition
        super().appendValue(lenT, nan=nan)
        if not nan and not np.isnan(self.value) and not np.isinf(self.value):
            chargeIncrement = (self.value - self.background) * self.channelParent.interval / 1000 / 3600 if self.values.size > 1 else 0
            self.preciseCharge += chargeIncrement  # display accumulated charge  # don't use np.sum(self.charges) to allow
            self.charge = self.preciseCharge  # pylint: disable=[attribute-defined-outside-init]  # attribute defined dynamically

    def clearHistory(self) -> None:
        super().clearHistory()
        self.resetCharge()

    def resetCharge(self) -> None:
        """Reset the charge."""
        self.charge = 0  # pylint: disable=[attribute-defined-outside-init]  # attribute defined dynamically
        self.preciseCharge = 0

    def realChanged(self) -> None:
        self.getParameterByName(self.COM).setVisible(self.real)
        self.getParameterByName(self.ID).setVisible(self.real)
        self.getParameterByName(self.RATE).setVisible(self.real)
        self.getParameterByName(self.RANGE).setVisible(self.real)
        self.getParameterByName(self.AVERAGE).setVisible(self.real)
        self.getParameterByName(self.BIASON).setVisible(self.real if self.channelParent.useInternalBias else False)
        self.getParameterByName(self.BIAS).setVisible(self.real if self.channelParent.useInternalBias else False)
        self.getParameterByName(self.OVERLOAD).setVisible(self.real)
        super().realChanged()


class SPACurrentController(DeviceController):

    controllerParent: SPA1x0
    ch1_avg = 'ch1_avg'
    ch2_avg = 'ch2_avg'
    ch1_overload = 'ch1_overload'
    ch2_overload = 'ch2_overload'
    SPA: 'type[SPA]'
    spas: 'list[SPA | None] | None' = None

    def __init__(self, controllerParent: SPA1x0) -> None:
        super().__init__(controllerParent=controllerParent)
        self.port = None
        Module = dynamicImport('SPA', self.controllerParent.dependencyPath / 'SPA_python_example.py')
        if Module:
            self.SPA = cast('type[SPA]', Module.SPA)
        self.initCOMs()

    def initCOMs(self) -> None:
        """Initialize COMs."""
        self.COMs = self.controllerParent.getCOMs() or ['COM1']

    def runInitialization(self) -> None:
        self.initCOMs()
        if self.COMs is None:
            return
        self.spas = [self.SPA(port=COM, cal_file=(self.controllerParent.dependencyPath / 'SPA120_cal.JSON').as_posix()) for COM in self.COMs]
        for i, spa in enumerate(self.spas):
            if spa:
                try:
                    spa.connect()
                except serial.serialutil.SerialException as e:
                    self.print(f'Could not establish Serial connection to a SPA at {spa.port}. Exception: {e}', flag=PRINT.WARNING)
                    self.spas[i] = None
        if any(self.spas):
            self.updateParameters(already_acquired=True)
            self.signalComm.initCompleteSignal.emit()
        self.initializing = False

    def initializeValues(self, reset: bool = False) -> None:
        super().initializeValues(reset)
        channel_count = len(self.controllerParent.getChannels())
        self.overload = [False] * channel_count
        self.samples = [{}] * len(self.controllerParent.getCOMs())
        self.phase = [self.rng.random() * 10 for _ in range(channel_count)]  # used in test mode
        self.omega = [self.rng.random() for _ in range(channel_count)]  # used in test mode
        self.offset = [self.rng.random() * 10 for _ in range(channel_count)]  # used in test mode

    def readNumbers(self) -> None:
        if self.spas:
            for i, spa in enumerate(self.spas):
                if spa:
                    try:
                        self.samples[i] = spa.read_sample()
                    except Exception as e:  # noqa: BLE001
                        self.print(f'Error while reading sample from SPA at {spa.port} {e}', flag=PRINT.WARNING)
                        self.errorCount += 1
                        self.samples[i] = {}
            for i, channel in enumerate(self.controllerParent.getChannels()):
                if channel.enabled and channel.real:
                    sample = self.samples[self.COMs.index(channel.com)]
                    if self.ch1_avg in sample:
                        self.overload[i] = sample[self.ch1_overload if channel.id == 1 else self.ch2_overload]
                        self.values[i] = np.nan if self.overload[i] else sample[self.ch1_avg if channel.id == 1 else self.ch2_avg] * 1e12  # in pA
                    else:
                        self.overload[i] = False
                        self.values[i] = np.nan
            self.signalComm.updateValuesSignal.emit()

    def fakeNumbers(self) -> None:
        for i, channel in enumerate(self.controllerParent.getChannels()):
            if channel.enabled and channel.real:
                self.overload[i] = False
                self.values[i] = np.sin(self.omega[i] * time.time() / 5 + self.phase[i]) * 10 + self.rng.random() + self.offset[i]
        self.signalComm.updateValuesSignal.emit()

    def updateValues(self) -> None:
        if self.values is not None:
            for i, channel in enumerate(self.controllerParent.getChannels()):
                if channel.enabled and channel.real:
                    channel.value = np.nan if channel.waitToStabilize else self.values[i]
                    channel.overload = self.overload[i]

    def setRange(self, channel: SPACurrentChannel, already_acquired: bool = False) -> None:
        """Set the range. Typically autorange is sufficient.

        :param channel: The Channel.
        :type channel: SPACurrentChannel
        :param already_acquired: Indicates if the lock has already been acquired, defaults to False
        :type already_acquired: bool, optional
        """
        with self.lock.acquire_timeout(2, timeoutMessage=f'Cannot acquire lock for setting Range of {channel.name}', already_acquired=already_acquired) as lock_acquired:
            if lock_acquired and self.spas:
                rangeWidget = cast('CompactComboBox', channel.getParameterByName(channel.RANGE).getWidget())
                spa = self.spas[self.COMs.index(channel.com)]
                if rangeWidget and spa:
                    spa.set_range(channel.id, rangeWidget.currentIndex())

    def setAverage(self, channel: SPACurrentChannel, already_acquired: bool = False) -> None:
        """Set averaging. If this is a SPA120 avearging will be changed for both channels.

        :param channel: The Channel.
        :type channel: SPACurrentChannel
        :param already_acquired: Indicates if the lock has already been acquired, defaults to False
        :type already_acquired: bool, optional
        """
        with self.lock.acquire_timeout(2, timeoutMessage=f'Cannot acquire lock for setting Average of {channel.name}', already_acquired=already_acquired) as lock_acquired:
            if lock_acquired and self.spas:
                spa = self.spas[self.COMs.index(channel.com)]
                if spa:
                    spa.set_averaging(int(channel.average))

    def setBias(self, channel: SPACurrentChannel, already_acquired: bool = False) -> None:
        """Set the bias voltage value, polarity and on or off.

        :param channel: The Channel.
        :type channel: SPACurrentChannel
        :param already_acquired: Indicates if the lock has already been acquired, defaults to False
        :type already_acquired: bool, optional
        """
        with self.lock.acquire_timeout(2, timeoutMessage=f'Cannot acquire lock for setting Bias of {channel.name}', already_acquired=already_acquired) as lock_acquired:
            if lock_acquired and self.spas:
                spa = self.spas[self.COMs.index(channel.com)]
                if spa:
                    spa.set_bias(channel=channel.id, voltage=abs(channel.bias) if self.controllerParent.isOn() else 0,
                                                          polarity='Positive' if np.sign(channel.bias) == 1 else 'Negative', enable=channel.biasOn)

    def setRate(self, channel: SPACurrentChannel, already_acquired: bool = False) -> None:
        """Set the Sample Rate. If this is a SPA120 the rate will be changed for both channels.

        :param channel: The Channel.
        :type channel: SPACurrentChannel
        :param already_acquired: Indicates if the lock has already been acquired, defaults to False
        :type already_acquired: bool, optional
        """
        with self.lock.acquire_timeout(2, timeoutMessage=f'Cannot acquire lock for setting Rate of {channel.name}', already_acquired=already_acquired) as lock_acquired:
            if lock_acquired and self.spas:
                spa = self.spas[self.COMs.index(channel.com)]
                if spa:
                    spa.set_sample_rate(channel.rate)

    def updateParameters(self, already_acquired: bool = False) -> None:
        """Update Range, Average, and Bias.

        :param already_acquired: Indicates if the lock has already been acquired, defaults to False
        :type already_acquired: bool, optional
        """
        # call from runAcquisition to make sure there are no race conditions
        with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock for updating all channel parameters', already_acquired=already_acquired) as lock_acquired:
            if lock_acquired:
                for channel in self.controllerParent.getChannels():
                    if channel.enabled and channel.real:
                        self.setRate(channel=channel, already_acquired=already_acquired)
                        self.setRange(channel=channel, already_acquired=already_acquired)
                        self.setAverage(channel=channel, already_acquired=already_acquired)
                        self.setBias(channel=channel, already_acquired=already_acquired)

    def toggleOn(self) -> None:
        super().toggleOn()
        with self.lock.acquire_timeout(2, timeoutMessage='Cannot acquire lock for updating all channel bias', already_acquired=False) as lock_acquired:
            if lock_acquired:
                for channel in self.controllerParent.getChannels():
                    if channel.enabled and channel.real:
                        self.setBias(channel=channel, already_acquired=True)

    def closeCommunication(self) -> None:
        super().closeCommunication()
        if self.spas:
            with self.lock.acquire_timeout(1, timeoutMessage='Could not acquire lock before closing ports.') as lock_acquired:
                if self.initialized and lock_acquired:  # pylint: disable=[access-member-before-definition]  # defined in DeviceController class
                    for spa in self.spas:
                        if spa:
                            spa.disconnect()
                    self.spas = None
        self.initialized = False
        self.closing = False
