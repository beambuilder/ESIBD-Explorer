import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, cast

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import ScalarFormatter
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QTextEdit

from esibd.core import INOUT, PARAMETERTYPE, PRINT, Channel, DynamicNp, MetaChannel, Parameter, ScanChannel, parameterDict, plotting
from esibd.plugins import Device, Scan

if TYPE_CHECKING:
    from esibd.plugins import Plugin

winsound = None
if sys.platform == 'win32':
    import winsound
else:
    print('Module winsound only available on Windows.')  # noqa: T201


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [Depo]


class Depo(Scan):
    """Scan that records the sample current and accumulated charge during deposition.

    A target charge and current threshold can be defined and a message will be
    played back when the target has been reached or the current drops below
    the threshold. The time until completion is estimated based on recent data.
    As data analysis is decoupled from data
    acquisition, you can continue to use all other scan modes and
    optimization while the deposition recording is running.
    """

    name = 'Depo'
    version = '1.0'
    CHARGE = 'Charge'
    useDisplayParameter = True
    iconFile = 'depo.png'

    display: 'Depo.Display'
    outputChannels: list['Depo.ScanChannel']
    inputChannels: list['Depo.MetaChannel']

    class MetaChannel(MetaChannel):
        recordingData: 'DynamicNp'

    class ChargeChannel(Channel):
        charge: float
        resetCharge: Callable

    class ScanChannel(ScanChannel):

        recordingData: 'DynamicNp'

        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.isChargeChannel = False

        def relayValueEvent(self) -> None:
            if self.sourceChannel:
                device = self.sourceChannel.getDevice()
                try:
                    if self.isChargeChannel:
                        self.value = cast('Depo.ChargeChannel', self.sourceChannel).charge
                    elif self.sourceChannel.useMonitors:
                        self.value = self.sourceChannel.monitor
                    elif isinstance(device, Device):
                        self.value = (self.sourceChannel.value - self.sourceChannel.background
                                      if device.subtractBackgroundActive() else self.sourceChannel.value)
                except RuntimeError:
                    self.removeEvents()

        def initGUI(self, item) -> None:
            super().initGUI(item)
            if self.name.endswith(f'_{Depo.CHARGE}') and self.unit == 'pAh':
                self.isChargeChannel = True

        def connectSource(self, giveFeedback: bool = False) -> None:
            if self.isChargeChannel:
                # change name before connectSource() to connect to corresponding channel
                self.name = self.name.removesuffix(f'_{Depo.CHARGE}')
            super().connectSource(giveFeedback=giveFeedback)
            if self.isChargeChannel:
                self.unit = 'pAh'
                self.name += f'_{Depo.CHARGE}'
            if self.sourceChannel and not hasattr(self.sourceChannel, 'resetCharge'):
                # found channel with same name but likely from different device
                super().connectSource()  # running again after changing name -> disconnect
            if self.unit == 'pAh':
                self.display = False

    class Display(Scan.Display):
        """Display for Depo scan."""

        scan: 'Depo'

        class CustomScientificFormatter(ScalarFormatter):
            """Custom formatter that prevents waste of vertical space by offset or scale factor.

            There is still an issue with very large or very small values if log=False.
            In that case the corresponding device should use self.logY = True.
            """

            def __init__(self, *args, log=False, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                self.log = log
                self.set_scientific(True)  # Enable scientific notation
                self.set_useOffset(False)  # Disable the offset/scaling factor above the axis

            def _set_format(self) -> None:
                """Override the default format behavior to suppress scaling."""
                self._useMathText = True
                if self.log:
                    self.format = '%.1e'  # Format ticks in scientific notation (e.g., 1.0e-10)
                else:
                    self.format = '%.1f'  # Format ticks in normal notation

        def initFig(self) -> None:  # noqa: C901
            super().initFig()
            if self.fig:
                self.fig.set_constrained_layout_pads(h_pad=-4.0)  # type: ignore # reduce space between axes  # noqa: PGH003
                rows = len(self.scan.getExtraUnits()) + 2
                self.axes.append(self.fig.add_subplot(rows, 1, 1))  # current axis
                self.axes.append(self.fig.add_subplot(rows, 1, 2, sharex=self.axes[0]))  # charge axis
                for i, unit in enumerate(self.scan.getExtraUnits()):
                    self.axes.append(self.fig.add_subplot(rows, 1, 3 + i, sharex=self.axes[0]))
                    self.axes[2 + i].set_ylabel(unit)
                for outputChannel in self.scan.outputChannels:
                    if outputChannel.unit == 'pA':
                        outputChannel.line = self.axes[0].plot([[datetime.now()]], [0], color=outputChannel.color)[0]  # type: ignore  # noqa: PGH003
                    if outputChannel.unit == 'pAh':
                        outputChannel.line = self.axes[1].plot([[datetime.now()]], [0], color=outputChannel.color)[0]  # type: ignore  # noqa: PGH003
                    elif outputChannel.unit in self.scan.getExtraUnits():
                        outputChannel.line = self.axes[2 + self.scan.getExtraUnits().index(outputChannel.unit)].plot(
                            [[datetime.now()]], [0], color=outputChannel.color, label=outputChannel.name)[0]  # type: ignore  # noqa: PGH003
                        if outputChannel.logY:
                            self.axes[2 + self.scan.getExtraUnits().index(outputChannel.unit)].set_yscale('log')
                        self.axes[2 + self.scan.getExtraUnits().index(outputChannel.unit)].get_yaxis().set_major_formatter(self.CustomScientificFormatter(log=outputChannel.logY))
                for i, _ in enumerate(self.scan.getExtraUnits()):
                    legend = self.axes[2 + i].legend(loc='best', prop={'size': 6}, frameon=False)
                    legend.set_in_layout(False)

                self.currentWarnLine = self.axes[0].axhline(y=float(self.scan.warnLevel), color=self.scan.MYRED)
                self.depoChargeTarget = self.axes[1].axhline(y=float(self.scan.target), color=self.scan.MYGREEN)
                if len(self.scan.outputChannels) > 0:
                    selected_output = self.scan.outputChannels[self.scan.getOutputIndex()]
                    self.chargePredictionLine = self.axes[1].plot([[datetime.now()]], [0], '--', color=selected_output.color)[0]  # type: ignore  # noqa: PGH003
                for i in range(len(self.axes) - 1):
                    self.axes[i].tick_params(axis='x', which='both', bottom=False, labelbottom=False)
                for i in range(len(self.axes)):
                    self.addRightAxis(self.axes[i])
                self.axes[0].set_ylabel('pA')
                self.axes[1].set_ylabel('pAh')
                self.axes[-1].set_xlabel(self.TIME)
                self.axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))  # auto formatting will resort to only year if space is limited -> fix format
                self.tilt_xlabels(self.axes[-1])
                self.progressAnnotation = self.axes[1].annotate(text='', xy=(0.02, 0.98), xycoords='axes fraction', fontsize=6, ha='left', va='top',
                                                                bbox={'boxstyle': 'square, pad=.2', 'fc': plt.rcParams['axes.facecolor'], 'ec': 'none'})
                self.updateDepoTarget()
                self.scan.labelAxis = self.axes[0]

        def updateDepoTarget(self) -> None:
            """Update the deposition target line in the plot."""
            if self.depoChargeTarget is not None and self.canvas:
                self.depoChargeTarget.set_ydata([self.scan.target])
                if np.sign(self.scan.target) == 1:
                    self.axes[0].set_ylim(0, 1)
                    self.axes[1].set_ylim(0, 1)
                else:
                    self.axes[0].set_ylim(1, 0)
                    self.axes[1].set_ylim(1, 0)
                self.axes[0].autoscale(self.scan.autoscale, axis='y')
                self.axes[0].autoscale(enable=True, axis='x')
                self.axes[0].relim()
                self.axes[1].autoscale(enable=True)
                self.axes[1].relim()
                self.canvas.draw_idle()

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.useDisplayChannel = True

    def initGUI(self) -> None:
        super().initGUI()
        self.recordingAction.setToolTip('Toggle deposition.')
        self.depoCheckList = QTextEdit()
        self.depoCheckList.setReadOnly(True)
        self.depoCheckList.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.depoCheckList.setText('Deposition checklist:\n- New session created?\n- Plasma cleaned?\n- Grid in place?\n'
                                   '- Shield closed?\n- Shuttle inserted?\n- Landing energy set?\n'
                                   '- Right polarity?\n- Temperature set?\n- Mass selection on?\n- LN2 ready for transfer?')
        verticalScrollBar = self.depoCheckList.verticalScrollBar()
        if verticalScrollBar:
            self.depoCheckList.setFixedWidth(QFontMetrics(self.depoCheckList.font()).horizontalAdvance('- LN2 ready for transfer?') +
                                          verticalScrollBar.sizeHint().width() + 10)
        self.settingsLayout.addWidget(self.depoCheckList, alignment=Qt.AlignmentFlag.AlignTop)

    def getExtraUnits(self) -> list[str]:
        """Get all units that are not representing a current or a charge."""
        return list({channel.unit for channel in self.outputChannels if channel.unit not in {'pA', 'pAh'} and channel.display})

    interval: int
    target: float
    warnLevel: int
    warn: bool
    autoscale: bool

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings.pop(self.WAIT)
        defaultSettings.pop(self.WAITLONG)
        defaultSettings.pop(self.LARGESTEP)
        defaultSettings.pop(self.SCANTIME)
        defaultSettings[self.DISPLAY][Parameter.VALUE] = 'RT_Sample-Center'
        defaultSettings[self.DISPLAY][Parameter.TOOLTIP] = 'Any channel that should be recorded during deposition, including at least one current channel.'
        defaultSettings[self.DISPLAY][Parameter.ITEMS] = 'RT_Sample-Center, RT_Sample-End, C_Shuttle'
        defaultSettings[self.AVERAGE][Parameter.VALUE] = 4000
        defaultSettings[self.INTERVAL] = parameterDict(value=10000, toolTip='Deposition interval.', parameterType=PARAMETERTYPE.INT,
                                                                minimum=1000, maximum=60000, attr='interval')
        defaultSettings['Target'] = parameterDict(value='15', toolTip='Target coverage in pAh.', items='-20,-15,-10, 10, 15, 20',
                                                                parameterType=PARAMETERTYPE.INTCOMBO, attr='target', event=self.updateDepoTarget)
        defaultSettings['Warnlevel'] = parameterDict(value='10', toolTip='Warning sound will be played when value drops below this level.', event=self.updateWarnLevel,
                                                            items='20, 15, 10, 0, -10, -15, -20', parameterType=PARAMETERTYPE.INTCOMBO, attr='warnLevel')
        defaultSettings['Warn'] = parameterDict(value=False, toolTip='Warning sound will be played when value drops below warnLevel. Disable to Mute.',
                                                            parameterType=PARAMETERTYPE.BOOL, attr='warn')
        defaultSettings['Autoscale'] = parameterDict(value=True, toolTip='Disable y axis autoscale if your data includes outliers, e.g. from pickup spikes.',
                                                            parameterType=PARAMETERTYPE.BOOL, attr='autoscale')
        return defaultSettings

    def updateDepoTarget(self) -> None:
        """Update the deposition target line in the plot."""
        if self.displayActive():
            self.display.updateDepoTarget()

    def addInputChannels(self) -> None:
        super().addInputChannels()
        self.addTimeInputChannel()

    def initScan(self) -> bool:
        # overwrite parent
        """Initialize all data and metadata.

        Returns True if initialization successful and scan is ready to start.
        """
        if super().initScan() and self.displayActive() and not self._dummy_initialization:
            if len([outputChannel for outputChannel in self.outputChannels if outputChannel.unit == 'pA']) > 0:  # at least one current channel
                self.measurementsPerStep = max(int(self.average / self.interval) - 1, 1)
                self.display.progressAnnotation.set_text('')
                self.display.updateDepoTarget()  # flip axes if needed
                return True
            if not self._dummy_initialization:
                self.print('No initialized current output channel found.', flag=PRINT.WARNING)
            return False
        return False

    def addOutputChannels(self) -> None:
        if self.channelTree:
            for name in self.settingsMgr.settings[self.DISPLAY].items:
                channel = self.addOutputChannel(name=name, recordingData=DynamicNp())
                if channel and channel.sourceChannel and hasattr(channel.sourceChannel, 'resetCharge'):
                    cast('Depo.ChargeChannel', channel.sourceChannel).resetCharge()
                    self.addOutputChannel(name=f'{name}_{self.CHARGE}', unit='pAh', recordingData=DynamicNp())
            self.channelTree.setHeaderLabels([parameterDict.get(Parameter.HEADER, '') or name.title()
                                                for name, parameterDict in self.headerChannel.getSortedDefaultChannel().items()])
            self.toggleAdvanced(advanced=False)

    def populateDisplayChannel(self) -> None:
        # overwrite parent to hide charge channels
        if self.displayActive():
            self.loading = True
            self.display.displayComboBox.clear()
            for outputChannel in self.outputChannels:
                if outputChannel.unit == 'pA':
                    self.display.displayComboBox.insertItem(self.display.displayComboBox.count(), outputChannel.name)
            self.loading = False
            if not self._dummy_initialization:
                self.updateDisplayDefault()

    def updateDisplayChannel(self) -> None:
        if self.displayActive():
            self.display.initFig()  # need to reinitialize as displayed channels are changing
        super().updateDisplayChannel()

    def updateWarnLevel(self) -> None:
        """Update the warning level line in the plot."""
        if self.displayActive() and self.display.currentWarnLine and self.display.canvas:
            self.display.currentWarnLine.set_ydata([self.warnLevel])
            self.display.canvas.draw_idle()

    MIN_FIT_DATA_POINTS = 10

    @plotting
    def plot(self, update=False, done=True, **kwargs) -> None:  # pylint:disable=unused-argument  # noqa: ARG002, C901, PLR0912
        # timing test with 360 data points (one hour at 0.1 Hz) update True: 75 ms, update False: 135 ms
        if self.loading:
            return
        if len(self.outputChannels) > 0 and len(self.inputChannels) > 0:  # noqa: PLR1702
            time_axis = self.getData(0, INOUT.IN)  # mdates.date2num(self.getData(0, INOUT.IN))
            if time_axis is not None:
                time_stamp_axis = [datetime.fromtimestamp(float(t)) for t in time_axis]  # convert timestamp to datetime
                charge = []
                for i, outputChannel in enumerate(self.outputChannels):
                    outputRecordingData = outputChannel.getRecordingData()
                    if outputRecordingData is not None:
                        if outputChannel.unit == 'pA':
                            if outputChannel.display or i == self.getOutputIndex():
                                outputChannel.line.set_data(time_stamp_axis, outputRecordingData)  # type: ignore  # noqa: PGH003
                            else:
                                outputChannel.line.set_data([], [])
                        elif outputChannel.unit == 'pAh':
                            if outputChannel.display or i == self.getOutputIndex() + 1:
                                outputChannel.line.set_data(time_stamp_axis, outputRecordingData)  # type: ignore  # noqa: PGH003
                            else:
                                outputChannel.line.set_data([], [])
                            if i == self.getOutputIndex() + 1:
                                charge = outputRecordingData
                        elif outputChannel.unit != 'pAh' and outputChannel.display:  # only show current and charge for selected channel
                            if hasattr(outputChannel, 'line'):
                                outputChannel.line.set_data(time_stamp_axis, outputRecordingData)  # type: ignore  # noqa: PGH003
                            else:
                                self.print(f'Line not initialized for channel {outputChannel.name}', flag=PRINT.WARNING)
                time_done_str = 'unknown'
                end_str = 'end'
                if len(time_stamp_axis) > self.MIN_FIT_DATA_POINTS or (len(time_stamp_axis) > 0 and done):  # predict scan based on last 10 data points
                    if len(time_stamp_axis) > self.MIN_FIT_DATA_POINTS and update and np.abs(charge[-1]) < np.abs(float(self.target)) and np.abs(charge[-1]) > np.abs(charge[-10]):
                        # only predict if below target and charge is increasing
                        # Pseudo code: t_t=t_i + dt/dQ * Q_remaining
                        time_done = datetime.fromtimestamp(float(time_axis[-1] + (time_axis[-1] - time_axis[-10]) / (charge[-1] - charge[-10]) * (float(self.target) - charge[-1])))
                        self.display.chargePredictionLine.set_data([time_stamp_axis[-1], time_done], [charge[-1], self.target])  # type: ignore  # noqa: PGH003
                        time_done_str = self.roundDateTime(time_done).strftime('%H:%M')
                        end_str = 'estimated end'
                    else:
                        # hide at beginning and end of scan or if loaded from file
                        self.display.chargePredictionLine.set_data([[time_stamp_axis[0]]], [0])  # type: ignore  # noqa: PGH003
                    if done:
                        time_done_str = self.roundDateTime(time_stamp_axis[-1]).strftime('%H:%M')
                if len(time_stamp_axis) > 0:  # predict scan based on last 10 data points
                    self.display.progressAnnotation.set_text(f"start: {self.roundDateTime(time_stamp_axis[0]).strftime('%H:%M')}, {end_str}: {time_done_str}\n"
                                            f"{charge[-1] - charge[0]:2.1f} pAh deposited")
        else:  # no data
            self.removeAnnotations(self.display.axes[1])
        self.display.axes[0].autoscale(enable=True, axis='x')
        self.display.axes[0].relim()
        for i in range(len(self.display.axes)):
            if i > 0:
                self.display.axes[i].autoscale(enable=True)
                self.display.axes[i].relim()
        if self.autoscale:
            self.setLabelMargin(self.display.axes[0], 0.3)
        self.updateToolBar(update=update)
        self.defaultLabelPlot()

    def pythonPlotCode(self) -> str:
        return f"""# add your custom plot code here

from datetime import datetime
import matplotlib.dates as mdates

MYBLUE='#1f77b4'

def addRightAxis(ax):
    axr = ax.twinx()
    axr.tick_params(direction="out", right=True)
    axr.sharey(ax)
    if ax.get_yscale() == 'log':
        axr.set_yscale('log')

def tilt_xlabels(ax, rotation=30):
    for label in ax.get_xticklabels(which='major'):
        label.set_ha('right')
        label.set_rotation(rotation)

def getExtraUnits():
    return list(set([channel.unit for channel in outputChannels if channel.unit not in ['pA', 'pAh']]))

fig = plt.figure(num='{self.name} plot', constrained_layout=True)
fig.set_constrained_layout_pads(h_pad=-4.0)  # reduce space between axes
rows = len(getExtraUnits()) + 2
axes = []
axes.append(fig.add_subplot(rows, 1, 1))  # current axis
axes[0].set_ylabel('pA')
axes.append(fig.add_subplot(rows, 1, 2, sharex = axes[0]))  # charge axis
axes[1].set_ylabel('pAh')

for i, unit in enumerate(getExtraUnits()):
    axes.append(fig.add_subplot(rows, 1, 3+i, sharex = axes[0]))
    axes[2+i].set_ylabel(unit)

time_axis = inputChannels[0].recordingData
time_stamp_axis = [datetime.fromtimestamp(float(t)) for t in time_axis]  # convert timestamp to datetime
axes[0].plot(time_stamp_axis, outputChannels[output_index].recordingData, color=MYBLUE)[0]
axes[1].plot(time_stamp_axis, outputChannels[output_index+1].recordingData, color=MYBLUE)[0]

for outputChannel in outputChannels:
    if outputChannel.unit not in ['pA', 'pAh'] and outputChannel.unit in getExtraUnits():
        axes[2+getExtraUnits().index(outputChannel.unit)].plot(time_stamp_axis, outputChannel.recordingData, label=outputChannel.name)[0]
        if outputChannel.logY:
            axes[2+getExtraUnits().index(outputChannel.unit)].set_yscale('log')

for i, unit in enumerate(getExtraUnits()):
    legend = axes[2+i].legend(loc='best', prop={{'size': 6}}, frameon=False)
    legend.set_in_layout(False)

for i in range(len(axes)-1):
    axes[i].tick_params(axis='x', which='both', bottom=False, labelbottom=False)
for i in range(len(axes)):
    addRightAxis(axes[i])
axes[-1].set_xlabel('Time')
axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
tilt_xlabels(axes[-1])

fig.show()

        """

    def roundDateTime(self, interval) -> datetime:
        """Round to nearest minute.

        :param interval: The original time interval.
        :type interval: datetime.datetime
        :return: The rounded time interval.
        :rtype: datetime.datetime
        """
        discard = timedelta(minutes=interval.minute % 1,
                             seconds=interval.second,
                             microseconds=interval.microsecond)
        interval -= discard
        if discard >= timedelta(seconds=30):
            interval += timedelta(minutes=1)
        return interval

    def runScan(self, recording) -> None:
        while recording():
            time.sleep(self.interval / 1000)
            self.bufferLagging()
            self.waitForCondition(condition=lambda: self.stepProcessed, timeoutMessage='processing scan step.', timeout=10)
            self.inputChannels[0].recordingData.add(time.time())
            for outputChannel in self.outputChannels:
                if outputChannel.isChargeChannel:
                    outputChannel.recordingData.add(cast('Depo.ChargeChannel', outputChannel.sourceChannel).charge)
                else:
                    outputValues = outputChannel.getValues(subtractBackground=outputChannel.getDevice().subtractBackgroundActive(), length=self.measurementsPerStep)
                    if outputValues is not None:
                        outputChannel.recordingData.add(float(np.mean(outputValues)))
            if self.warn and winsound:  # Sound only supported for windows
                outputData = self.getData(self.getOutputIndex(), INOUT.OUT)
                outputDataPlus1 = self.getData(self.getOutputIndex() + 1, INOUT.OUT)
                if outputData is not None and outputDataPlus1 is not None:
                    if ((np.sign(self.target) == 1 and outputDataPlus1[-1] > float(self.target)) or
                                  (np.sign(self.target) == -1 and outputDataPlus1[-1] < float(self.target))):
                        winsound.PlaySound(str(self.dependencyPath / 'done.wav'), winsound.SND_ASYNC | winsound.SND_ALIAS)
                    elif ((np.sign(self.warnLevel) == 1 and outputData[-1] < float(self.warnLevel)) or
                                  (np.sign(self.warnLevel) == -1 and outputData[-1] > float(self.warnLevel))):
                        winsound.PlaySound(str(self.dependencyPath / 'alarm.wav'), winsound.SND_ASYNC | winsound.SND_ALIAS)
            if recording():  # all but last step
                self.stepProcessed = False
                self.signalComm.scanUpdateSignal.emit(False)  # update graph  # noqa: FBT003
        self.signalComm.scanUpdateSignal.emit(True)  # update graph and save data  # placed after while loop to ensure it will be executed  # noqa: FBT003
