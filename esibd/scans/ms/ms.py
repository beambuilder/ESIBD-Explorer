from typing import TYPE_CHECKING, cast

import h5py

from esibd.core import PARAMETERTYPE, MZCalculator, Parameter, parameterDict, plotting
from esibd.plugins import Scan

if TYPE_CHECKING:
    from esibd.plugins import Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [MassSpec]


class MassSpec(Scan):
    """Record mass spectra by recording an output channel as a function of a (calibrated) input channel.

    Left clicking on peaks in a charge state series while holding down the Ctrl key provides a
    quick estimate of charge state and mass, based on minimizing the standard
    deviation of the mass as a function of possible charge states.
    Use Ctrl + right mouse click to reset.
    This can be used as a template or a parent class for a simple one dimensional scan of other properties.
    """

    name = 'msScan'
    version = '1.1'
    supportedVersion = '1.0'
    iconFile = 'msScan.png'
    useInvalidWhileWaiting = True

    display: 'MassSpec.Display'

    class Display(Scan.Display):
        """Display for MassSpec scan."""

        scan: 'MassSpec'

        def initGUI(self) -> None:
            self.mzCalc = MZCalculator(parentPlugin=self.scan)
            super().initGUI()
            self.addAction(event=lambda: self.copyLineDataClipboard(line=self.ms), toolTip='Data to Clipboard.', icon=self.dataClipboardIcon, before=self.copyAction)

        def initFig(self) -> None:
            super().initFig()
            if self.fig and self.canvas:
                self.axes.append(self.fig.add_subplot(111))
                self.ms = self.axes[0].plot([], [])[0]  # dummy plot
                self.mzCalc.setAxis(self.axes[0])
                self.scan.labelAxis = self.axes[0]
                self.canvas.mpl_connect('button_press_event', self.mzCalc.msOnClick)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.useDisplayChannel = True
        self.previewFileTypes.append('ms scan.h5')

    channelName: str

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings[self.DISPLAY][Parameter.VALUE] = 'Detector'
        defaultSettings[self.DISPLAY][Parameter.TOOLTIP] = 'Channel for transmitted signal.'
        defaultSettings[self.DISPLAY][Parameter.ITEMS] = 'Detector, Detector2'
        defaultSettings[self.CHANNEL] = parameterDict(value='AMP_Q1', toolTip='Amplitude that is swept through', items='AMP_Q1, AMP_Q2',
                                                                      parameterType=PARAMETERTYPE.COMBO, attr='channelName', event=self.dummyInitialization)
        defaultSettings[self.START] = parameterDict(value=50, parameterType=PARAMETERTYPE.FLOAT, attr='start', event=self.estimateScanTime)
        defaultSettings[self.STOP] = parameterDict(value=200, parameterType=PARAMETERTYPE.FLOAT, attr='stop', event=self.estimateScanTime)
        defaultSettings[self.STEP] = parameterDict(value=1, parameterType=PARAMETERTYPE.FLOAT, attr='step', minimum=.1, maximum=10, event=self.estimateScanTime)
        return defaultSettings

    def addInputChannels(self) -> None:
        super().addInputChannels()
        self.addInputChannel(self.channelName, self.start, self.stop, self.step)

    @plotting
    def plot(self, update=False, done=False, **kwargs) -> None:  # pylint:disable=unused-argument  # noqa: ARG002
        if len(self.outputChannels) > 0:
            inputRecordingData0 = self.inputChannels[0].getRecordingData()
            outputRecordingData = self.outputChannels[self.getOutputIndex()].getRecordingData()
            if inputRecordingData0 is not None and outputRecordingData is not None:
                self.display.ms.set_data(inputRecordingData0, outputRecordingData)
            if not update:
                self.display.axes[0].set_ylabel(f'{self.outputChannels[self.getOutputIndex()].name} ({self.outputChannels[self.getOutputIndex()].unit})')
                self.display.axes[0].set_xlabel(f'{self.inputChannels[0].name} ({self.inputChannels[0].unit})')
        else:  # no data
            self.display.ms.set_data([], [])
        self.display.axes[0].relim()  # adjust to data
        self.setLabelMargin(self.display.axes[0], 0.15)
        self.updateToolBar(update=update)
        self.display.mzCalc.update_mass_to_charge()
        self.defaultLabelPlot()

    def pythonPlotCode(self) -> str:
        return f"""# add your custom plot code here

fig = plt.figure(num='{self.name} plot', constrained_layout=True)
ax0 = fig.add_subplot(111)

ax0.plot(inputChannels[0].recordingData, outputChannels[output_index].recordingData)
ax0.set_ylabel(f'{{outputChannels[output_index].name}} ({{outputChannels[output_index].unit}})')
ax0.set_xlabel(f'{{inputChannels[0].name}} ({{inputChannels[0].unit}})')

fig.show()
"""

    def loadData(self, file, showPlugin=True) -> None:
        super().loadData(file, showPlugin)
        self.display.mzCalc.clear()

    def loadDataInternal(self) -> bool:
        """Load data in internal standard format for plotting."""
        if self.file.name.endswith('ms scan.h5'):  # legacy file before removing space in plugin name
            with h5py.File(self.file, 'r') as h5file:
                group = cast('h5py.Group', h5file['MS Scan'])
                input_group = cast('h5py.Group', group[self.INPUTCHANNELS])
                for name, data in input_group.items():
                    self.addInputChannel(name=name, unit=data.attrs[self.UNIT], recordingData=data[:])
                output_group = cast('h5py.Group', group[self.OUTPUTCHANNELS])
                for name, data in output_group.items():
                    self.addOutputChannel(name=name, unit=data.attrs[self.UNIT], recordingData=data[:])
                self.finalizeChannelTree()
            return True
        return super().loadDataInternal()
