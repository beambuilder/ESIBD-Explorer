from pathlib import Path

import numpy as np

from esibd.core import PLUGINTYPE
from esibd.plugins import Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [LINE]


class LINE(Plugin):
    """The Line plugin allows to display simple 2D data.

    It is made to work with simple xy text files with a three line header.
    In most cases you will need to create your own version of this plugin
    that is inheriting from the build in version and redefines how data is
    loaded for your specific data format. See :ref:`sec:plugin_system` for more information.
    """

    documentation = """The Line plugin allows to display simple 2D data. It is made to work
    with simple xy text files with a three line header.
    """

    name = 'Line'
    version = '1.0'
    pluginType = PLUGINTYPE.DISPLAY
    iconFile = 'line.png'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.previewFileTypes = ['.txt']

    def initGUI(self) -> None:
        self.profile = None
        self.file = Path()
        super().initGUI()
        self.initFig()

    def initFig(self) -> None:
        self.provideFig()
        if self.fig:
            self.axes.append(self.fig.add_subplot(111))
        self.line = None  # type: ignore  # noqa: PGH003

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

    def runTestParallel(self) -> None:
        if self.initializedDock:
            self.testControl(self.copyAction, value=True)
            self.testControl(self.dataAction, value=True)
            self.testPythonPlotCode(closePopup=True)
        super().runTestParallel()

    def supportsFile(self, file: Path) -> bool:
        if super().supportsFile(file):
            first_line = ''  # else text file
            try:
                with file.open(encoding=self.UTF8) as open_file:
                    first_line = open_file.readline()
            except UnicodeDecodeError:
                return False
            if 'profile' in first_line.lower():  # afm profile
                return True
        return False

    def loadData(self, file, showPlugin=True) -> None:
        """Plot one dimensional data for multiple file types.

        :param file: The file from which to load data.
        :type file: pathlib.Path
        :param showPlugin: True if display should be shown after loading data. Set to False if multiple plugins load file and other plugins have priority. Defaults to True
        :type showPlugin: bool, optional
        """
        self.provideDock()
        if file.name.endswith('.txt'):  # need to implement handling of different files in future
            self.profile = np.loadtxt(file, skiprows=3, dtype=np.float32)
            self.file = file
            self.plot()
        self.raiseDock(showPlugin)

    def plot(self) -> None:
        self.axes[0].clear()
        if self.profile is not None:
            self.line = self.axes[0].plot(self.profile[:, 0], self.profile[:, 1])[0]
        self.axes[0].set_xlabel('width (m)')
        self.axes[0].set_ylabel('height (m)')
        self.axes[0].autoscale(enable=True)
        self.axes[0].relim()
        self.axes[0].autoscale_view(tight=True, scalex=True, scaley=False)
        self.setLabelMargin(self.axes[0], 0.15)
        if self.canvas:
            self.canvas.draw_idle()
            self.canvas.get_default_filename = lambda: self.file.with_suffix('.pdf').as_posix() if self.file else self.name  # set up save file dialog
        if self.navToolBar:
            self.navToolBar.update()  # reset history for zooming and home view
        self.labelPlot(self.file.name if self.file else 'Line')

    def generatePythonPlotCode(self) -> str:
        return f"""import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

profile = np.loadtxt('{self.file.as_posix() if self.file else ''}', skiprows=3)

fig = plt.figure(num='{self.name} plot', constrained_layout=True)
ax = fig.add_subplot(111)
ax.plot(profile[:, 0], profile[:, 1])[0]
ax.set_xlabel('width (m)')
ax.set_ylabel('height (m)')
fig.show()"""
