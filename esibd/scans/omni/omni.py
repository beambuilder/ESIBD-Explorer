import time
from typing import TYPE_CHECKING, cast

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSlider  # , QTextEdit  #, QSizePolicy  # QLabel, QMessageBox
from scipy.stats import binned_statistic

from esibd.core import PARAMETERTYPE, DynamicNp, MetaChannel, Parameter, ScanChannel, parameterDict, plotting
from esibd.plugins import Scan

if TYPE_CHECKING:
    from esibd.plugins import Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [Omni]


class Omni(Scan):
    """Most basic scan which simply records a number of arbitrary output channels as a function of a single arbitrary input channel.

    When switched to the
    interactive mode, a slider will appear that allows to set the value of
    the input channel manually and independent of the scan settings. This
    may be more intuitive and faster than automated scanning, e.g. when looking for a local maximum.
    """

    name = 'Omni'
    version = '1.0'
    useDisplayParameter = True
    useInvalidWhileWaiting = True
    iconFile = 'omni.png'

    display: 'Omni.Display'
    inputChannels: list['Omni.ScanChannel | Omni.MetaChannel']
    outputChannels: list['Omni.ScanChannel']

    class ScanChannel(ScanChannel):
        # will be DynamicNp in interactive mode and np.ndarray otherwise
        recordingData: 'DynamicNp | np.ndarray'

    class MetaChannel(MetaChannel):
        recordingData: 'DynamicNp'

    class Display(Scan.Display):
        """Display for Omni scan."""

        scan: 'Omni'

        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.xSlider: QSlider = None  # type: ignore  # noqa: PGH003
            self.lines = None  # type: ignore  # noqa: PGH003

        def initFig(self) -> None:
            super().initFig()
            if self.fig:
                self.lines = None  # type: ignore  # noqa: PGH003
                self.axes = []
                self.axes.append(self.fig.add_subplot(111))
                if self.xSlider:
                    self.xSlider.deleteLater()
                self.xSlider = QSlider(Qt.Orientation.Horizontal)
                self.vertLayout.addWidget(self.xSlider)
                self.xSlider.valueChanged.connect(self.updateX)
                self.updateInteractive()
                self.scan.labelAxis = self.axes[0]

        def updateX(self, value) -> None:
            """Update the value of the independent variable based on slider value.

            :param value: Slider value.
            :type value: float
            """
            if self.scan.inputChannels[0].sourceChannel:
                # map slider range onto range
                self.scan.inputChannels[0].sourceChannel.value = self.scan.start + value / self.xSlider.maximum() * (self.scan.stop - self.scan.start)

        def updateInteractive(self) -> None:
            """Adjust the scan based on the interactive Setting.

            If interactive, a slider is used to change the independent variable in real time.
            """
            if self.xSlider:
                self.xSlider.setVisible(self.scan.interactive)
                if self.scan.interactive and len(self.scan.inputChannels) > 0:
                    self.xSlider.setValue(int((self.scan.inputChannels[0].value - self.scan.inputChannels[0].min) *
                                              self.xSlider.maximum() / (self.scan.inputChannels[0].max - self.scan.inputChannels[0].min)))

    channelName: str
    bins: int
    interactive: bool

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings[self.WAIT][Parameter.VALUE] = 2000
        defaultSettings[self.CHANNEL] = parameterDict(value='RT_Grid', toolTip='Electrode that is swept through', items='RT_Grid, RT_Sample-Center, RT_Sample-End',
                                                                      parameterType=PARAMETERTYPE.COMBO, attr='channelName', event=self.dummyInitialization)
        defaultSettings[self.START] = parameterDict(value=-10, parameterType=PARAMETERTYPE.FLOAT, attr='start', event=self.estimateScanTime)
        defaultSettings[self.STOP] = parameterDict(value=-5, parameterType=PARAMETERTYPE.FLOAT, attr='stop', event=self.estimateScanTime)
        defaultSettings[self.STEP] = parameterDict(value=.2, parameterType=PARAMETERTYPE.FLOAT, attr='step', minimum=.1, maximum=10, event=self.estimateScanTime)
        self.BINS = 'Bins'
        defaultSettings[self.BINS] = parameterDict(value=20, parameterType=PARAMETERTYPE.INT, toolTip='Number of bins used in interactive mode.',
                                                    minimum=10, maximum=200, attr='bins')
        self.INTERACTIVE = 'Interactive'
        defaultSettings[self.INTERACTIVE] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL,
        toolTip='Use the slider to define channel value in interactive mode.\nUse short wait and average when possible to get fast feedback.\nStop scan when done.',
                                                attr='interactive', event=self.updateInteractive)
        return defaultSettings

    def updateInteractive(self) -> None:
        """Adjust the scan based on the interactive Setting."""
        if self.displayActive():
            self.display.updateInteractive()
        self.estimateScanTime()

    @Scan.finished.setter
    def finished(self, finished) -> None:
        Scan.finished.fset(self, finished)  # type: ignore  # noqa: PGH003
        # disable inputs while scanning
        self.settingsMgr.settings[self.INTERACTIVE].setEnabled(finished)

    def estimateScanTime(self) -> None:
        if self.interactive:
            self.scantime = 'n/a'
        else:
            super().estimateScanTime()

    def addInputChannels(self) -> None:
        super().addInputChannels()
        self.addInputChannel(self.channelName, self.start, self.stop, self.step)

    def initScan(self) -> bool:
        if super().initScan() and self.displayActive() and not self._dummy_initialization:
            self.display.lines = None  # type: ignore  # noqa: PGH003
            self.display.updateInteractive()
            if self.interactive:
                self.inputChannels[0].recordingData = DynamicNp()
                for outputChannel in self.outputChannels:
                    outputChannel.recordingData = DynamicNp()
            return True
        return False

    def loadDataInternal(self) -> bool:
        self.display.lines = None  # type: ignore  # noqa: PGH003
        return super().loadDataInternal()

    @plotting
    def plot(self, update=False, done=True, **kwargs) -> None:  # pylint:disable=unused-argument  # noqa: ARG002, C901, PLR0912
        if len(self.outputChannels) > 0 and self.display:
            inputRecordingData = self.inputChannels[0].getRecordingData()
            if not self.display.lines:
                self.display.axes[0].clear()
                self.display.lines = []  # dummy plots
                for outputChannel in self.outputChannels:
                    if outputChannel.sourceChannel:
                        self.display.lines.append(self.display.axes[0].plot([], [], label=f'{outputChannel.name} ({outputChannel.unit})', color=outputChannel.color)[0])
                    else:
                        self.display.lines.append(self.display.axes[0].plot([], [], label=f'{outputChannel.name} ({outputChannel.unit})')[0])
                legend = self.display.axes[0].legend(loc='best', prop={'size': 7}, frameon=False)
                legend.set_in_layout(False)
            if not update:
                self.display.axes[0].set_xlabel(f'{self.inputChannels[0].name} ({self.inputChannels[0].unit})')
                if self.recording:  # show all data if loaded from file
                    self.display.axes[0].set_xlim(self.start, self.stop)
            if self.interactive:
                for i, output in enumerate(self.outputChannels):
                    if output.display:
                        x = inputRecordingData
                        y = output.getRecordingData()
                        mean, bin_edges, _ = binned_statistic(x, y, bins=self.bins, range=(int(self.start), int(self.stop)))
                        self.display.lines[i].set_data((bin_edges[:-1] + bin_edges[1:]) / 2, mean)
                    else:
                        self.display.lines[i].set_data([], [])
            else:
                for i, output in enumerate(self.outputChannels):
                    outputRecordingData = output.getRecordingData()
                    if output.display and inputRecordingData is not None and outputRecordingData is not None:
                        self.display.lines[i].set_data(inputRecordingData, outputRecordingData)
                    else:
                        self.display.lines[i].set_data([], [])
            self.display.axes[0].relim()  # adjust to data
            self.setLabelMargin(self.display.axes[0], 0.15)
        self.updateToolBar(update=update)
        self.defaultLabelPlot()

    def pythonPlotCode(self) -> str:
        return f"""# add your custom plot code here
from scipy.stats import binned_statistic

_interactive = False  # set to True to use histogram
bins = 20  # choose number of bins
start   = min(inputChannels[0].recordingData)
to      = max(inputChannels[0].recordingData)

fig = plt.figure(num='{self.name} plot', constrained_layout=True)
ax0 = fig.add_subplot(111)
ax0.set_xlabel(f'{{inputChannels[0].name}} ({{inputChannels[0].unit}})')
for outputChannel in outputChannels:
    if _interactive:
        mean, bin_edges, _ = binned_statistic(inputChannels[0].recordingData, outputChannel.recordingData, bins=bins, range=(int(start), int(to)))
        ax0.plot((bin_edges[:-1] + bin_edges[1:]) / 2, mean, label=f'{{outputChannel.name}} ({{outputChannel.unit}})')
    else:
        ax0.plot(inputChannels[0].recordingData, outputChannel.recordingData, label=f'{{outputChannel.name}} ({{outputChannel.unit}})')
ax0.legend(loc='best', prop={{'size': 7}}, frameon=False)
fig.show()
        """  # similar to staticDisplay

    def runScan(self, recording) -> None:  # noqa: C901, PLR0912
        if self.interactive:
            while recording():
                # changing input is done in main thread using slider. Scan is only recording result.
                if self.invalidWhileWaiting:
                    for outputChannel in self.outputChannels:
                        outputChannel.signalComm.waitUntilStableSignal.emit(self.wait)
                time.sleep((self.wait + self.average) / 1000)  # if step is larger than threshold use longer wait time
                self.bufferLagging()
                self.waitForCondition(condition=lambda: self.stepProcessed, timeoutMessage='processing scan step.', timeout=10)
                inputChannelValues0 = self.inputChannels[0].getValues(subtractBackground=self.inputChannels[0].subtractBackgroundActive(), length=self.measurementsPerStep)
                if inputChannelValues0 is not None:
                    if self.inputChannels[0].recording:  # get average
                        cast('DynamicNp', self.inputChannels[0].recordingData).add(float(np.mean(inputChannelValues0)))
                    else:  # use last value
                        cast('DynamicNp', self.inputChannels[0].recordingData).add(self.inputChannels[0].value)
                    for j, outputChannel in enumerate(self.outputChannels):
                        outputChannelValues = outputChannel.getValues(subtractBackground=outputChannel.subtractBackgroundActive(), length=self.measurementsPerStep)
                        if outputChannelValues is not None:
                            cast('DynamicNp', self.outputChannels[j].recordingData).add(float(np.mean(outputChannelValues)))
                if not recording():  # last step
                    self.signalComm.scanUpdateSignal.emit(True)  # update graph and save data  # noqa: FBT003
                    self.signalComm.updateRecordingSignal.emit(False)  # noqa: FBT003
                else:
                    self.stepProcessed = False
                    self.signalComm.scanUpdateSignal.emit(False)  # update graph  # noqa: FBT003
        else:
            steps = self.inputChannels[0].getRecordingData()
            self.print(f'Starting scan M{self.pluginManager.Settings.measurementNumber:03}. Estimated time: {self.scantime}')
            if steps is not None:
                for i, step in enumerate(steps):  # scan over all steps
                    waitLong = False
                    if not waitLong and abs(self.inputChannels[0].value - step) > self.largestep:
                        waitLong = True
                    if self.inputChannels[0].updateValueSignal:
                        self.inputChannels[0].updateValueSignal.emit(step)
                    time.sleep(((self.waitLong if waitLong else self.wait) + self.average) / 1000)  # if step is larger than threshold use longer wait time
                    self.bufferLagging()
                    self.waitForCondition(condition=lambda: self.stepProcessed, timeoutMessage='processing scan step.', timeout=10)
                    for outputChannel in self.outputChannels:
                        outputRecordingData = outputChannel.getValues(subtractBackground=outputChannel.getDevice().subtractBackgroundActive(),
                                                                                          length=self.measurementsPerStep)
                        if outputRecordingData is not None:
                            cast('np.ndarray', outputChannel.recordingData)[i] = np.mean(outputRecordingData)
                    if i == len(steps) - 1 or not recording():  # last step
                        if self.inputChannels[0].updateValueSignal:
                            self.inputChannels[0].updateValueSignal.emit(self.inputChannels[0].initialValue)
                        time.sleep(.5)  # allow time to reset to initial value before saving
                        self.signalComm.scanUpdateSignal.emit(True)  # update graph and save data  # noqa: FBT003
                        self.signalComm.updateRecordingSignal.emit(False)  # noqa: FBT003
                        break  # in case this is last step
                    self.stepProcessed = False
                    self.signalComm.scanUpdateSignal.emit(False)  # update graph  # noqa: FBT003
