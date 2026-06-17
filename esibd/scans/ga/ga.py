import time
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np

from esibd.core import INOUT, PARAMETERTYPE, PRINT, DynamicNp, MetaChannel, Parameter, ScanChannel, dynamicImport, parameterDict, plotting, pyqtSignal
from esibd.plugins import Scan

if TYPE_CHECKING:
    from esibd.plugins import Plugin

    from .ga_standalone import GA


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [GAScan]


class GAScan(Scan):
    r"""Allows to integrate an independently developed genetic algorithm (GA) for automated optimization of signals\ :cite:`esser_cryogenic_2019`.

    Multiple input channels can be selected to be included in the optimization. Make sure to choose safe
    limits for optimized channels and choose appropriate wait and average
    values to get valid feedback. The performance and reliability of the
    optimization depends on the stability and reproducibility of the
    selected output channel. The output channel can be virtual and contain an
    equation that references many other channels. At the end of the optimization the changed
    parameters will be shown in the plugin. The initial parameters can
    always be restored in case the optimization fails.
    """

    documentation = """This plugin allows to integrate an independently developed genetic
    algorithm (GA) for automated optimization of signals.
    Multiple input channels can be selected to be included in the optimization. Make sure to choose safe
    limits for optimized channels and choose appropriate wait and average
    values to get valid feedback. The performance and reliability of the
    optimization depends on the stability and reproducibility of the
    selected output channel. The output channel can be virtual and contain an
    equation that references many other channels. At the end of the optimization the changed
    parameters will be shown in the plugin. The initial parameters can
    always be restored in case the optimization fails.
    """

    name = 'GA'
    version = '1.0'
    iconFile = 'GA_light.png'
    iconFileDark = 'GA_dark.png'
    useInvalidWhileWaiting = True

    signalComm: 'SignalCommunicate'
    display: 'GAScan.Display'
    inputChannels: list['GAScan.MetaChannel']
    outputChannels: list['GAScan.ScanChannel | GAScan.MetaChannel']
    ga: 'GA'

    class SignalCommunicate(Scan.SignalCommunicate):
        """Bundle pyqtSignals."""

        updateValuesSignal = pyqtSignal(int, bool)

    class MetaChannel(MetaChannel):
        recordingData: 'DynamicNp'

    class ScanChannel(ScanChannel):

        recordingData: 'DynamicNp'

    class Display(Scan.Display):
        """Display for GA scan."""

        scan: 'GAScan'

        def initFig(self) -> None:
            super().initFig()
            if self.fig:
                self.axes.append(self.fig.add_subplot(111))
                self.bestLine = self.axes[0].plot([[datetime.now()]], [0], label='best fitness')[0]  # type: ignore  # noqa: PGH003  # need to be initialized with datetime on x axis
                self.avgLine = self.axes[0].plot([[datetime.now()]], [0], label='avg fitness')[0]  # type: ignore  # noqa: PGH003
                legend = self.axes[0].legend(loc='lower right', prop={'size': 10}, frameon=False)
                legend.set_in_layout(False)
                self.axes[0].set_xlabel(self.TIME)
                self.axes[0].set_ylabel('Fitness Value')
                self.tilt_xlabels(self.axes[0])
                self.scan.labelAxis = self.axes[0]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        Module = dynamicImport('ga_standalone', self.dependencyPath / 'ga_standalone.py')
        if Module:
            self.ga = Module.GA()
        self.signalComm.updateValuesSignal.connect(self.updateValues)
        self.changeLog = []

    def initGUI(self) -> None:
        super().initGUI()
        self.recordingAction.setToolTip('Toggle optimization.')
        self.initialAction = self.addStateAction(event=self.toggleInitial, toolTipFalse='Switch to initial settings.', iconFalse=self.makeIcon('switch-medium_on.png'),
                                                 toolTipTrue='Switch to optimized settings.', iconTrue=self.makeIcon('switch-medium_off.png'),
                                                   attr='applyInitialParameters', restore=False)

    def runTestParallel(self) -> None:
        self.testControl(self.initialAction, self.initialAction.state)
        super().runTestParallel()

    GACHANNEL = 'GA Channel'
    log: bool

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings.pop(self.WAITLONG)
        defaultSettings.pop(self.LARGESTEP)
        defaultSettings.pop(self.SCANTIME)
        defaultSettings[self.GACHANNEL] = defaultSettings.pop(self.DISPLAY)  # keep display for using displayChannel functionality but modify properties as needed
        defaultSettings[self.GACHANNEL][Parameter.TOOLTIP] = 'Genetic algorithm optimizes on this channel'
        defaultSettings[self.GACHANNEL][Parameter.ITEMS] = 'C_Shuttle, RT_Detector, RT_Sample-Center, RT_Sample-End, LALB-Aperture'
        defaultSettings[self.GACHANNEL][Parameter.EVENT] = self.dummyInitialization
        defaultSettings['Logging'] = parameterDict(value=False, toolTip='Show detailed GA updates in console.', parameterType=PARAMETERTYPE.BOOL, attr='log')
        return defaultSettings

    def toggleInitial(self) -> None:
        """Toggles between initial and optimized values."""
        if len(self.outputChannels) > 0:
            self.signalComm.updateValuesSignal.emit(0, self.initialAction.state)
        else:
            self.initialAction.state = False
            self.print('GA not initialized.')

    def addInputChannels(self) -> None:
        super().addInputChannels()
        self.addTimeInputChannel()

    def initScan(self) -> bool:
        """Start optimization."""
        # overwrite parent
        if super().initScan() and self.displayActive() and not self._dummy_initialization:
            self.display.axes[0].set_ylabel(self.outputChannels[0].name)
            self.ga.init()  # don't mix up with init method from Scan
            self.ga.maximize(True)  # noqa: FBT003
            for channel in self.pluginManager.DeviceManager.channels(inout=INOUT.IN):
                if channel.optimize:
                    self.ga.optimize(channel.value, channel.min, channel.max, .2, abs(channel.max - channel.min) / 10, channel.name)
                else:
                    # add entry but set rate to 0 to prevent value change. Can be activated later.
                    self.ga.optimize(channel.value, channel.min, channel.max, 0, abs(channel.max - channel.min) / 10, channel.name)
            self.ga.genesis()
            self.ga.file_path(self.file.parent.as_posix())
            self.ga.file_name(self.file.name)
            self.initialAction.state = False
            return True
        return False

    def addOutputChannels(self) -> None:
        if self.channelTree:
            self.print('addOutputChannels', flag=PRINT.DEBUG)
            self.addOutputChannel(name=f'{self.displayDefault}')  # only add ga channel
            self.channelTree.setHeaderLabels([parameterDict.get(Parameter.HEADER, '') or name.title()
                                        for name, parameterDict in self.headerChannel.getSortedDefaultChannel().items()])
            if len(self.outputChannels) > 0:
                self.outputChannels[0].recordingData = DynamicNp()
                self.outputChannels.append(self.MetaChannel(parentPlugin=self, name=f'{self.displayDefault}_Avg', recordingData=DynamicNp()))
                self.toggleAdvanced(advanced=False)

    @plotting
    def plot(self, update=False, **kwargs) -> None:  # pylint:disable=unused-argument, missing-param-doc  # noqa: ARG002
        """Plot fitness data.

        :param update: Indicates if this is just an incremental update or the final plot (e.g. when loading data from file), defaults to False
        :type update: bool, optional
        """
        # timing test with 160 generations: update True: 25 ms, update False: 37 ms
        if self.loading:
            return
        bestData = self.getData(0, INOUT.IN)
        avgData = self.getData(1, INOUT.OUT)
        if len(self.outputChannels) > 0:
            if bestData is not None and avgData is not None:
                time_axis = [datetime.fromtimestamp(float(time_axis)) for time_axis in bestData]  # convert timestamp to datetime
                self.display.bestLine.set_data(time_axis, bestData)  # type: ignore  # noqa: PGH003
                self.display.avgLine.set_data(time_axis, avgData)  # type: ignore  # noqa: PGH003
        else:  # no data
            self.display.bestLine.set_data([], [])
            self.display.avgLine.set_data([], [])
        self.display.axes[0].autoscale(enable=True, axis='x')
        self.display.axes[0].relim()
        self.display.axes[0].autoscale_view(tight=True, scalex=True, scaley=False)
        if bestData is not None and len(bestData) > 1:
            self.setLabelMargin(self.display.axes[0], 0.15)
        self.updateToolBar(update=update)
        self.defaultLabelPlot()

    def pythonPlotCode(self) -> str:
        return f"""# add your custom plot code here
from datetime import datetime

fig = plt.figure(num='{self.name} plot', constrained_layout=True)
ax0 = fig.add_subplot(111)
ax0.set_xlabel('Time')
ax0.set_ylabel('Fitness Value')
for label in ax0.get_xticklabels(which='major'):
    label.set_ha('right')
    label.set_rotation(30)
time_axis = [datetime.fromtimestamp(float(time_axis)) for time_axis in inputChannels[0].recordingData]
ax0.plot(time_axis, outputChannels[0].recordingData, label='best fitness')[0]
ax0.plot(time_axis, outputChannels[1].recordingData, label='avg fitness')[0]
ax0.legend(loc='lower right', prop={{'size': 10}}, frameon=False)
fig.show()
        """

    def runScan(self, recording) -> None:
        # first datapoint before optimization
        self.inputChannels[0].recordingData.add(time.time())
        outputChannelValues = self.outputChannels[0].getValues(subtractBackground=self.outputChannels[0].subtractBackgroundActive(), length=self.measurementsPerStep)
        if outputChannelValues is not None:
            fitnessStart = float(np.mean(outputChannelValues))
            self.outputChannels[0].recordingData.add(fitnessStart)
            self.outputChannels[1].recordingData.add(fitnessStart)
            while recording():
                outputChannelValues = self.outputChannels[0].getValues(subtractBackground=self.outputChannels[0].subtractBackgroundActive(), length=self.measurementsPerStep)
                if outputChannelValues is None:
                    self.print('outputChannelValues not defined', flag=PRINT.ERROR)
                    return
                self.signalComm.updateValuesSignal.emit(-1, False)  # noqa: FBT003
                if self.invalidWhileWaiting:
                    for outputChannel in self.outputChannels:
                        if isinstance(outputChannel, ScanChannel):
                            outputChannel.signalComm.waitUntilStableSignal.emit(self.wait)
                time.sleep((self.wait + self.average) / 1000)
                self.bufferLagging()
                self.waitForCondition(condition=lambda: self.stepProcessed, timeoutMessage='processing scan step.', timeout=10)
                self.ga.fitness(np.mean(outputChannelValues))
                if self.log:
                    self.print(self.ga.step_string().replace('GA: ', ''))
                _, session_saved = self.ga.check_restart()
                if session_saved:
                    self.print(f'Session Saved -- Average Fitness: {self.ga.average_fitness():6.2f} Best Fitness: {self.ga.best_fitness():6.2f}')
                    self.print(f'Starting Generation {self.ga.current_generation}:')
                    self.inputChannels[0].recordingData.add(time.time())
                    self.outputChannels[0].recordingData.add(self.ga.best_fitness())
                    self.outputChannels[1].recordingData.add(float(self.ga.average_fitness()))
                    self.stepProcessed = False
                    self.signalComm.scanUpdateSignal.emit(False)  # noqa: FBT003
        self.ga.check_restart(_terminate=True)  # sort population
        self.signalComm.updateValuesSignal.emit(0, False)  # noqa: FBT003
        self.signalComm.scanUpdateSignal.emit(True)  # noqa: FBT003

    def updateValues(self, index, initial=False) -> None:
        """Update all optimized values or restores initial values.

        :param index: Index of being in GA population. -1 is current being. 0 is best after sorting. Defaults to None
        :type index: int, optional
        :param initial: Indicates if initial values should be returned, defaults to False
        :type initial: bool, optional
        """
        # only call in main thread as updates GUI
        self.pluginManager.loading = True  # only update after setting all voltages
        try:
            for channel in [channel for channel in self.pluginManager.DeviceManager.channels(inout=INOUT.IN) if channel.optimize]:
                channel.value = self.ga.GAget(channel.name, channel.value, index=index, initial=initial)
        except ValueError as e:
            self.print(f'Could not assign value: {e}', flag=PRINT.ERROR)
        finally:
            self.pluginManager.loading = False
        self.pluginManager.DeviceManager.globalUpdate(inout=INOUT.IN)

    def saveScanParallel(self, file) -> None:
        self.changeLog = [f'Change log for optimizing channels by {self.name}:']
        for channel in [channel for channel in self.pluginManager.DeviceManager.channels(inout=INOUT.IN) if channel.optimize]:
            parameter = channel.getParameterByName(Parameter.VALUE)
            if not parameter.equals(self.ga.GAget(channel.name, channel.value, initial=True)):
                self.changeLog.append(
    f'Changed value of {channel.name} from {parameter.formatValue(self.ga.GAget(channel.name, channel.value, initial=True))} to '
    f'{parameter.formatValue(self.ga.GAget(channel.name, channel.value, index=0))}.')
        if len(self.changeLog) == 1:
            self.changeLog.append('No changes.')
        self.pluginManager.Text.setTextParallel('\n'.join(self.changeLog))
        self.print('Change log available in Text plugin.')
        super().saveScanParallel(file)
