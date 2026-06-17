from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from esibd.core import PLUGINTYPE, MZCalculator, StateAction, colors, getDarkMode
from esibd.plugins import Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [MS]


class MS(Plugin):
    """The MS plugin allows to display simple mass spectra.

    Left clicking on peaks in a charge state series while holding down the Ctrl key provides a
    quick estimate of charge state and mass, based on minimizing the standard
    deviation of the mass as a function of possible charge states. The
    detailed results are shown in the graph, and help to evaluate the
    quality of the estimate. Use Ctrl + right mouse click to reset. In most cases you will need to create your own version of this plugin
    that is inheriting from the built-in version and redefines how data is
    loaded for your specific data format. See :ref:`sec:plugin_system` for more information.
    """

    documentation = """The MS plugin allows to display simple mass spectra. Left clicking on peaks
    in a charge state series while holding down the Ctrl key provides a
    quick estimate of charge state and mass, based on minimizing the standard
    deviation of the mass as a function of possible charge states. The
    detailed results are shown in the graph, and help to evaluate the
    quality of the estimate. Use Ctrl + right mouse click to reset.
    """

    name = 'MS'
    version = '1.0'
    pluginType = PLUGINTYPE.DISPLAY
    iconFile = 'MS.png'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.previewFileTypes = ['.txt']
        self.file = Path()
        self._x = self._y = None
        self.paperAction: 'StateAction | None' = None
        self.dataClipboardIcon = self.makeIcon('clipboard-paste-document-text.png')

    def initGUI(self) -> None:
        self.mzCalc = MZCalculator(parentPlugin=self)
        super().initGUI()
        self.initFig()

    def initFig(self) -> None:
        self.provideFig()
        if self.fig:
            self.axes.append(self.fig.add_subplot(111))
            self.mzCalc.setAxis(self.axes[0])  # update axis but reuse picked positions until reset explicitly
        if self.canvas:
            self.canvas.mpl_connect('button_press_event', self.mzCalc.msOnClick)
        self.line = None  # type: ignore  # noqa: PGH003 # self.axes[0].plot([],[])[0]  # dummy plot

    def provideDock(self) -> bool:
        if super().provideDock():
            self.finalizeInit()
            self.afterFinalizeInit()
            return True
        return False

    def finalizeInit(self) -> None:
        super().finalizeInit()
        self.copyAction = self.addAction(event=self.copyClipboard, toolTip=f'{self.name} image to clipboard.', icon=self.imageClipboardIcon, before=self.aboutAction)
        self.dataAction = self.addAction(event=lambda: self.copyLineDataClipboard(line=self.line), toolTip=f'{self.name} data to clipboard.',
                                          icon=self.dataClipboardIcon, before=self.copyAction)
        self.paperAction = self.addStateAction(event=self.plot, toolTipFalse='Plot in paper style.',
                                                iconFalse=self.makeIcon('percent_dark.png' if getDarkMode() else 'percent_light.png'),
                                               toolTipTrue='Plot in normal style.', iconTrue=self.getIcon(), before=self.dataAction, attr='usePaperStyle')

    def runTestParallel(self) -> None:
        if self.initializedDock:
            self.testControl(self.copyAction, value=True)
            self.testControl(self.dataAction, value=True)
            if self.paperAction:
                self.testControl(self.paperAction, not self.paperAction.state)
            self.testPythonPlotCode(closePopup=True)
        super().runTestParallel()

    def supportsFile(self, file: Path) -> bool:
        if super().supportsFile(file):
            first_line = ''
            try:
                with file.open(encoding=self.UTF8) as open_file:
                    first_line = open_file.readline()
            except UnicodeDecodeError:
                return False
            if 'spectrum' in first_line.lower():  # mass spectrum
                return True
        return False

    def loadData(self, file, showPlugin=True) -> None:
        self.provideDock()
        self.file = file
        self.mzCalc.clear()
        self._x, self._y = np.loadtxt(self.file, skiprows=10, usecols=[0, 1], unpack=True)
        self.plot()
        self.raiseDock(showPlugin)

    def plot(self) -> None:
        """Plot MS data."""
        self.axes[0].clear()
        self.axes[0].set_xlabel('m/z (Th)')
        if self.paperAction and self.paperAction.state:
            self.axes[0].spines['right'].set_visible(False)
            self.axes[0].spines['top'].set_visible(False)
            if self._x is not None and self._y is not None:
                self.line = self.axes[0].plot(self._x, self.map_percent(self._x, self.smooth(self._y, 10)),
                                            color=colors.fg if plt.rcParams['axes.facecolor'] == colors.bg else colors.bg)[0]
            self.axes[0].set_ylabel('')
            self.axes[0].set_ylim((1, 100 + 2))
            self.axes[0].set_yticks([1, 50, 100])
            self.axes[0].set_yticklabels(['0', '%', '100'])
        else:
            self.axes[0].set_ylabel('Intensity')
            if self._x is not None and self._y is not None:
                self.line = self.axes[0].plot(self._x, self._y)[0]
            self.axes[0].ticklabel_format(axis='y', style='sci', scilimits=(0, 0))  # use shared exponent for short y labels, even for smaller numbers

        self.axes[0].set_autoscale_on(True)
        self.axes[0].relim()
        self.axes[0].autoscale_view(tight=True, scalex=True, scaley=False)
        self.setLabelMargin(self.axes[0], 0.15)
        if self.navToolBar:
            self.navToolBar.update()  # reset history for zooming and home view
        if self.canvas:
            self.canvas.get_default_filename = lambda: self.file.with_suffix('.pdf').as_posix() if self.file else self.name  # set up save file dialog
        self.mzCalc.update_mass_to_charge()
        self.labelPlot('' if self.paperAction and self.paperAction.state else (self.file.name if self.file else self.name))

    def find_nearest(self, array, value) -> float:
        """Return the nearest value in the given array.

        :param array: Array to search in.
        :type array: np.ndarray
        :param value: Search value.
        :type value: float
        :return: Value nearest to search value.
        :rtype: float
        """
        array = np.asarray(array)
        idx = (np.abs(array - value)).argmin()
        return float(array[idx])

    def map_percent(self, x, y) -> np.ndarray:
        """Map the range between y(x_min) and y(x_max) to 0 to 100 %.

        :param x: X values.
        :type x: np.ndarray
        :param y: Original Y values.
        :type y: np.ndarray
        :return: Mapped input values.
        :rtype: np.ndarray
        """
        x_min_i = np.where(x == self.find_nearest(x, min(x)))[0]
        x_min_i = np.min(x_min_i) if x_min_i.shape[0] > 0 else 0
        x_max_i = np.where(x == self.find_nearest(x, max(x)))[0]
        x_max_i = np.max(x_max_i) if x_max_i.shape[0] > 0 else x.shape[0]
        y_sub = y[x_min_i:x_max_i]
        return (y - np.min(y)) / np.max(y_sub - np.min(y_sub)) * 100

    def smooth(self, y: np.ndarray, box_pts: int) -> np.ndarray:
        """Smooths a 1D array.

        :param y: Array to be smoothed.
        :type y: np.ndarray
        :param box_pts: With of box used for smoothing.
        :type box_pts: int
        :return: convolvedArray
        :rtype: np.ndarray
        """
        box = np.ones(box_pts) / box_pts
        return np.convolve(y, box, mode='same')

    def updateTheme(self) -> None:
        super().updateTheme()
        if self.paperAction:
            self.paperAction.iconFalse = self.makeIcon('percent_dark.png' if getDarkMode() else 'percent_light.png')
            self.paperAction.iconTrue = self.getIcon()
            self.paperAction.updateIcon(self.paperAction.state)

    def generatePythonPlotCode(self) -> str:
        return f"""import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

def map_percent(x, y):
    '''Map the range between y(x_min) and y(x_max) to 0 to 100 %.

    :param x: X values.
    :type x: np.ndarray
    :param y: Original Y values.
    :type y: np.ndarray
    :return: Mapped input values.
    :rtype: np.ndarray
    '''
    x_min_i=np.where(x == find_nearest(x, min(x)))[0]
    x_min_i=np.min(x_min_i) if x_min_i.shape[0] > 0 else 0
    x_max_i=np.where(x == find_nearest(x, max(x)))[0]
    x_max_i=np.max(x_max_i) if x_max_i.shape[0] > 0 else x.shape[0]
    y_sub=y[x_min_i:x_max_i]
    return (y-np.min(y))/np.max(y_sub-np.min(y_sub))*100

def smooth(y, box_pts):
    '''Smooth a 1D array.

    :param y: Array to be smoothed.
    :type y: np.ndarray
    :param box_pts: With of box used for smoothing.
    :type box_pts: int
    :return: convolvedArray
    :rtype: np.ndarray
    '''
    box = np.ones(box_pts)/box_pts
    y_smooth = np.convolve(y, box, mode='same')
    return y_smooth

def find_nearest(array, value):
    '''Return the nearest value in the given array.

    :param array: Array to search in.
    :type array: np.ndarray
    :param value: Search value.
    :type value: float
    :return: Value nearest to search value.
    :rtype: float
    '''
    array = np.asarray(array)
    idx = (np.abs(array - value)).argmin()
    return array[idx]

paperStyle = False

x, y = np.loadtxt('{self.file.as_posix() if self.file else ''}', skiprows=10, usecols=[0, 1], unpack=True)

fig = plt.figure(num='{self.name} plot', constrained_layout=True)
ax = fig.add_subplot(111)
ax.set_xlabel('m/z (Th)')
if paperStyle:
    ax.spines['right'].set_visible(False)
    ax.spines['top'].set_visible(False)
    ax.plot(x, map_percent(x, min(x), max(x), smooth(y, 10)), color='k')[0]
    ax.set_ylabel('')
    ax.set_ylim([1, 100+2])
    ax.set_yticks([1, 50, 100])
    ax.set_yticklabels(['0', '%', '100'])
else:
    ax.set_ylabel('Intensity')
    ax.plot(x, y)[0]
    ax.ticklabel_format(axis='y', style='sci', scilimits=(0, 0))  # use shared exponent for short y labels, even for smaller numbers
fig.show()"""
