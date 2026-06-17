import itertools
import time
from enum import Enum
from typing import TYPE_CHECKING, cast

import h5py
import numpy as np

from esibd.core import CursorAxes, MultiState, getDarkMode, plotting
from esibd.plugins import Device
from esibd.scans import Beam

if TYPE_CHECKING:
    from esibd.plugins import Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [Spectra]


class Spectra(Beam):
    """Extend Beam scan to plot the data in the form of multiple spectra instead of a single 2D plot.

    The spectra can be plotted stacked (Y axis represents value of Y input channel and data of display channel is normalized.)
    or overlaid (Y axis represents data of display channel and value of Y input channels are indicated in a legend).
    In addition, the average of all spectra can be displayed.
    If you want to remeasure the same spectrum several times,
    consider defining a dummy channel that can be used as an index.
    """

    # * by inheriting from Beam, this creates another independent instance which allows the user to use both at the same time.
    # This allows for a more flexible use compared to adding these features as options to Beam directly.
    # It also serves as an example for how to inherit from scans that can help users to make their own versions.
    # As this is backwards compatible with files saved by Beam scan, it is possible to disable Beam scan if you want to make sure Spectra scan is opening the file.

    name = 'Spectra'
    version = '1.0'
    iconFile = 'stacked.png'
    LEFTRIGHT = 'X'
    UPDOWN = 'Y'
    display: 'Display | None'

    class Display(Beam.Display):
        """Displays data for Spectra scan."""

        plotModeAction = None
        averageAction = None

        class PlotActionState(Enum):
            STACKED = 'STACKED'
            OVERLAY = 'OVERLAY'
            CONTOUR = 'CONTOUR'

        def __init__(self, scan, **kwargs) -> None:
            super(Beam.Display, self).__init__(scan, **kwargs)
            self.lines = None  # type: ignore  # noqa: PGH003

        def finalizeInit(self) -> None:
            self.mouseActive = False
            super().finalizeInit()
            self.averageAction = self.addStateAction(toolTipFalse='Show average.', toolTipTrue='Hide average.', iconFalse=self.scan.getIcon(),  # defined in updateTheme
                                                        before=self.copyAction, event=lambda: (self.initFig(), self.scan.plot(update=False, done=True)), attr='average')
            self.plotModeAction = self.addMultiStateAction(states=[MultiState(self.PlotActionState.STACKED, 'Overlay plots.', self.scan.makeIcon('overlay.png')),
                                                               MultiState(self.PlotActionState.OVERLAY, 'Contour plot.', self.scan.makeIcon('beam.png')),
                                                               MultiState(self.PlotActionState.CONTOUR, 'Stack plots.', self.scan.makeIcon('stacked.png'))], before=self.copyAction,
                                                        event=lambda: (self.initFig(), self.scan.plot(update=False, done=True)), attr='plotMode')
            self.updateTheme()  # set icons
            self.initFig()  # axes aspect or plotMode may have changed

        def initFig(self) -> None:
            if not self.plotModeAction:
                return
            self.lines = None  # type: ignore  # noqa: PGH003
            if self.plotModeAction.state == self.PlotActionState.CONTOUR:
                super().initFig()
                return
            super(Beam.Display, self).initFig()
            if self.fig:
                self.axes.append(cast('CursorAxes', self.fig.add_subplot(111)))
                if self.axesAspectAction and not self.axesAspectAction.state:
                    self.axes[0].set_aspect('equal', adjustable='box')
                self.scan.labelAxis = self.axes[0]

        def updateTheme(self) -> None:
            if self.averageAction:
                self.averageAction.iconFalse = self.scan.makeIcon('average_dark.png' if getDarkMode() else 'average_light.png')
                self.averageAction.iconTrue = self.averageAction.iconFalse
                self.averageAction.updateIcon(self.averageAction.state)
            return super().updateTheme()

    def __init__(self, **kwargs) -> None:
        super(Beam, self).__init__(**kwargs)
        self.useDisplayChannel = True
        self.previewFileTypes.append('beam.h5')

    def initData(self) -> None:
        # self.toggleDisplay(visible=True) todo delete?
        super().initData()
        if self.displayActive() and self.display:
            self.display.lines = None  # type: ignore  # noqa: PGH003

    def loadDataInternal(self) -> bool:
        if self.display:
            self.display.lines = None  # type: ignore  # noqa: PGH003
        if self.file.name.endswith('beam.h5'):
            with h5py.File(self.file, 'r') as h5file:
                group = cast('h5py.Group', h5file['Beam'])  # only modification needed to open beam files. data structure is identical
                input_group = cast('h5py.Group', group[self.INPUTCHANNELS])
                for name, data in input_group.items():
                    self.addInputChannel(name=name, unit=data.attrs[self.UNIT], recordingData=data[:])
                output_group = cast('h5py.Group', group[self.OUTPUTCHANNELS])
                for name, data in output_group.items():
                    self.addOutputChannel(name=name, unit=data.attrs[self.UNIT], recordingData=data[:])
                self.finalizeChannelTree()
            return True
        return super(Beam, self).loadDataInternal()

    @plotting
    def plot(self, update=False, done=True, **kwargs) -> None:  # pylint:disable=unused-argument  # noqa: C901, PLR0912, PLR0915
        # timing test with 50 data points: update True: 33 ms, update False: 120 ms
        if not self.display or not self.display.plotModeAction or not self.display.averageAction:
            return
        if self.display.plotModeAction.state == self.display.PlotActionState.CONTOUR:
            self.plotting = False  # decorator will be called again
            super().plot(update=update, done=done, **kwargs)
            return
        if self.loading or len(self.outputChannels) == 0:
            return
        recordingData0 = self.inputChannels[0].getRecordingData()
        recordingData1 = self.inputChannels[1].getRecordingData()
        if recordingData0 is None or recordingData1 is None:
            return

        x = np.linspace(recordingData0[0], recordingData0[-1], len(recordingData0))
        y = np.linspace(recordingData1[0], recordingData1[-1], len(recordingData1))
        if self.display and not self.display.lines:
            self.display.axes[0].clear()
            self.display.lines = []  # dummy plots
            outputRecordingData = self.outputChannels[self.getOutputIndex()].getRecordingData()
            if outputRecordingData is not None:
                for i in range(len(outputRecordingData)):
                    if self.display.plotModeAction.state == self.display.PlotActionState.STACKED:
                        self.display.lines.append(self.display.axes[0].plot([], [])[0])
                    else:  # self.display.plotModeAction.labels.overlay
                        self.display.lines.append(self.display.axes[0].plot([], [], label=y[i])[0])
                if self.display.averageAction.state:
                    if self.display.plotModeAction.state == self.display.PlotActionState.STACKED:
                        self.display.lines.append(self.display.axes[0].plot([], [], linewidth=4)[0])
                    else:  # self.display.plotModeAction.labels.overlay
                        self.display.lines.append(self.display.axes[0].plot([], [], label='avg', linewidth=4)[0])
                if self.display.plotModeAction.state == self.display.PlotActionState.OVERLAY:
                    legend = self.display.axes[0].legend(loc='best', prop={'size': 10}, frameon=False)
                    legend.set_in_layout(False)

        if not update:
            self.display.axes[0].set_xlabel(f'{self.inputChannels[0].name} ({self.inputChannels[0].unit})')
            self.display.axes[0].set_ylabel(f'{self.inputChannels[1].name} ({self.inputChannels[1].unit})')
        recordingDataOutputIndex = self.outputChannels[self.getOutputIndex()].getRecordingData()
        if self.display.lines and recordingDataOutputIndex is not None:
            for i, z in enumerate(recordingDataOutputIndex):
                if self.display.plotModeAction.state == self.display.PlotActionState.STACKED:
                    z_offset = None
                    if np.abs(z.max() - z.min()) != 0:
                        z_normalized = z / (np.abs(z.max() - z.min())) * np.abs(y[1] - y[0])
                        z_offset = z_normalized + y[i] - z_normalized[0]
                    self.display.lines[i].set_data(x, z_offset if z_offset is not None else z)
                else:  # self.display.plotModeAction.labels.overlay
                    self.display.lines[i].set_data(x, z)
            if self.display.averageAction.state:
                z = np.mean(recordingDataOutputIndex, 0)
                if self.display.plotModeAction.state == self.display.PlotActionState.STACKED:
                    if np.abs(z.max() - z.min()) != 0:
                        z = z / (np.abs(z.max() - z.min())) * np.abs(y[1] - y[0])
                    self.display.lines[-1].set_data(x, z + y[-1] + y[1] - y[0] - z[0])
                else:  # self.display.plotModeAction.labels.overlay
                    self.display.lines[-1].set_data(x, z)

        self.display.axes[0].relim()  # adjust to data
        self.setLabelMargin(self.display.axes[0], 0.15)
        self.updateToolBar(update=update)
        self.defaultLabelPlot()

    def runScan(self, recording) -> None:  # noqa: C901, PLR0912
        # definition of steps updated to scan along x instead of y axis.
        inputRecordingData0 = self.inputChannels[0].getRecordingData()
        inputRecordingData1 = self.inputChannels[1].getRecordingData()
        if inputRecordingData0 is None or inputRecordingData1 is None:
            return
        steps = [steps_1d[::-1] for steps_1d in list(itertools.product(inputRecordingData1, inputRecordingData0))]
        self.print(f'Starting scan M{self.pluginManager.Settings.measurementNumber:03}. Estimated time: {self.scantime}')
        for i, step in enumerate(steps):  # scan over all steps
            waitLong = False
            for j, inputChannel in enumerate(self.inputChannels):
                if not waitLong and abs(inputChannel.value - step[j]) > self.largestep:
                    waitLong = True
                if inputChannel.updateValueSignal:
                    inputChannel.updateValueSignal.emit(step[j])
            if self.invalidWhileWaiting:
                for outputChannel in self.outputChannels:
                    outputChannel.signalComm.waitUntilStableSignal.emit(self.waitLong if waitLong else self.wait)
            time.sleep(((self.waitLong if waitLong else self.wait) + self.average) / 1000)  # if step is larger than threshold use longer wait time
            self.bufferLagging()
            self.waitForCondition(condition=lambda: self.stepProcessed, timeoutMessage='processing scan step.', timeout=10)
            for outputChannel in self.outputChannels:
                # 2D scan
                # definition updated to scan along x instead of y axis.
                outputDevice = outputChannel.getDevice()
                if isinstance(outputDevice, Device):
                    outputValues = outputChannel.getValues(subtractBackground=outputDevice.subtractBackgroundActive(), length=self.measurementsPerStep)
                    if outputChannel.recordingData is not None and inputRecordingData0 is not None and outputValues is not None:
                        outputChannel.recordingData[i // len(inputRecordingData0), i % len(inputRecordingData0)] = np.mean(outputValues)
            if i == len(steps) - 1 or not recording():  # last step
                for inputChannel in self.inputChannels:
                    if inputChannel.updateValueSignal:
                        inputChannel.updateValueSignal.emit(inputChannel.initialValue)
                time.sleep(.5)  # allow time to reset to initial value before saving
                self.stepProcessed = False
                self.signalComm.scanUpdateSignal.emit(True)  # update graph and save data  # noqa: FBT003
                self.signalComm.updateRecordingSignal.emit(False)  # noqa: FBT003
                break  # in case this is last step
            self.signalComm.scanUpdateSignal.emit(False)  # update graph  # noqa: FBT003

    def pythonPlotCode(self) -> str:
        return f"""# add your custom plot code here

_interpolate = False  # set to True to interpolate data
varAxesAspect = False  # set to True to use variable axes aspect ratio
average = False  # set to True to display an average spectrum
plotMode = 'stacked'  # 'stacked', 'overlay', or 'contour'  # select the representation of your data

from mpl_toolkits.axes_grid1 import make_axes_locatable
from scipy import interpolate

fig = plt.figure(num='{self.name} plot', constrained_layout=True)
ax = fig.add_subplot(111)
if not varAxesAspect:
    ax.set_aspect('equal', adjustable='box')

def getMeshgrid(scaling=1):
    return np.meshgrid(*[np.linspace(i.recordingData[0], i.recordingData[-1], len(i.recordingData) if scaling == 1 else
    min(len(i.recordingData)*scaling, 50)) for i in inputChannels])

ax.set_xlabel(f'{{inputChannels[0].name}} ({{inputChannels[0].unit}})')
ax.set_ylabel(f'{{inputChannels[1].name}} ({{inputChannels[1].unit}})')

if plotMode == 'contour':
    divider = make_axes_locatable(ax)
    cont = None
    cax = divider.append_axes("right", size="5%", pad=0.15)
    x, y = getMeshgrid()
    z = outputChannels[output_index].recordingData.ravel()

    if _interpolate:
        rbf = interpolate.Rbf(x.ravel(), y.ravel(), outputChannels[output_index].recordingData.ravel())
        xi, yi = getMeshgrid(2)  # interpolation coordinates, scaling of 1 much faster than 2 and seems to be sufficient
        zi = rbf(xi, yi)
        cont = ax.contourf(xi, yi, zi, levels=100, cmap='afmhot')  # contour with interpolation
    else:
        cont = ax.pcolormesh(x, y, outputChannels[output_index].recordingData, cmap='afmhot')  # contour without interpolation
    cbar = fig.colorbar(cont, cax=cax)  # match axis and color bar size  # , format='%d'
    cbar.ax.set_title(outputChannels[0].unit)
else:
    x = np.linspace(inputChannels[0].recordingData[0], inputChannels[0].recordingData[-1], len(inputChannels[0].recordingData))
    y = np.linspace(inputChannels[1].recordingData[0], inputChannels[1].recordingData[-1], len(inputChannels[1].recordingData))
    for i, z in enumerate(outputChannels[output_index].recordingData):
        if plotMode == 'stacked':
            if np.abs(z.max()-z.min()) != 0:
                z = z/(np.abs(z.max()-z.min()))*np.abs(y[1]-y[0])
            ax.plot(x, z + y[i] - z[0])
        else:  # 'overlay'
            ax.plot(x, z, label=y[i])
    if average:
        z = np.mean(outputChannels[output_index].recordingData, 0)
        if plotMode == 'stacked':
            if np.abs(z.max()-z.min()) != 0:
                z = z/(np.abs(z.max()-z.min()))*np.abs(y[1]-y[0])
            ax.plot(x, z + y[-1] + y[1]-y[0] - z[0], linewidth=4)
        else:  # 'overlay'
            ax.plot(x, z, label='avg', linewidth=4)
    if plotMode == 'overlay':
        legend = ax.legend(loc='best', prop={{'size': 10}}, frameon=False)
        legend.set_in_layout(False)
fig.show()
        """  # noqa: S608
