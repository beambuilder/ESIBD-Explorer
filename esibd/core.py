"""Internally used functions and classes.

Generally all objects that are used across multiple modules should be defined here to avoid circular imports and keep things consistent.
Whenever it is possible to make definitions only locally where they are needed, this is preferred.
For now, English is the only supported language and use of hard coded error messages etc. in other files is tolerated if they are unique.
"""

import configparser
import os
import re
import sys
import threading
import time
import traceback
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from threading import Thread, Timer, current_thread, main_thread
from typing import TYPE_CHECKING, Any, TextIO, TypeVar, cast

import cv2
import keyboard as kb
import matplotlib.pyplot as plt  # pylint: disable = unused-import  # need to import to access mpl.axes.Axes
import matplotlib.style
import numpy as np
import pyqtgraph as pg
import pyqtgraph.console
import pyqtgraph.console.exception_widget
import pyqtgraph.console.repl_widget
import serial
from matplotlib.axes import Axes
from matplotlib.backend_bases import Event, MouseButton, MouseEvent, ResizeEvent
from matplotlib.backends.backend_qt import NavigationToolbar2QT
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.text import Annotation
from matplotlib.widgets import Cursor
from PIL import Image
from PIL.ImageQt import ImageQt
from PyQt6 import QtWidgets, sip
from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QSharedMemory, QSize, Qt, QTimer, pyqtBoundSignal, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QRadialGradient,
    QTextCursor,
    QValidator,
)
from PyQt6.QtWebEngineWidgets import (
    QWebEngineView,  # pylint: disable = unused-import  # QtWebEngineWidgets must be imported or Qt.AA_ShareOpenGLContexts must be set before a QCoreApplication instance is created
)
from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QGridLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplashScreen,
    QStackedLayout,
    QStatusBar,
    QTabBar,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from esibd.const import *  # pylint: disable = wildcard-import, unused-wildcard-import  # noqa: F403

ParameterWidgetType = Union['PushButton', 'ToolButton', 'PushButton', 'ColorButton', 'CheckBox', 'Label',
                             'CompactComboBox', 'LineEdit', 'LedIndicator', 'LabviewSpinBox', 'LabviewDoubleSpinBox', 'LabviewSciSpinBox']

if TYPE_CHECKING:
    from matplotlib.lines import Line2D
    from matplotlib.typing import ColorType
    from PyQt6.QtGui import QCloseEvent, QContextMenuEvent, QPaintEvent, QResizeEvent, QWheelEvent
    from PyQt6.QtWidgets import QGraphicsSceneMouseEvent

    from esibd.plugins import PID, UCM, Browser, ChannelManager, Console, Device, DeviceManager, Explorer, LiveDisplay, Notes, Plugin, Scan, Settings, StaticDisplay, Text, Tree


class EsibdExplorer(QMainWindow):
    r"""ESIBD Explorer: A comprehensive data acquisition and analysis tool for Electrospray Ion-Beam Deposition experiments and beyond.

    Contains minimal code to start, initialize, and close the program.
    All high level logic is provided by :mod:`~esibd.core`,
    :mod:`~esibd.plugins` and additional
    :class:`plugins<esibd.plugins.Plugin>`.
    """

    loadPluginsSignal = pyqtSignal()

    def __init__(self, app: 'Application') -> None:
        """Set up basic user interface and triggers loading of plugins."""
        super().__init__()
        # switch to GL compatibility mode here to avoid UI glitches later https://stackoverflow.com/questions/77031792/how-to-avoid-white-flash-when-initializing-qwebengineview
        self.app = app
        dummy = QWebEngineView(parent=self)
        dummy.setHtml('dummy')
        dummy.deleteLater()
        self.restoreUiState()
        self.setWindowIcon(QIcon(PROGRAM_ICON.as_posix()))
        self.setWindowTitle(PROGRAM_NAME)
        self.actionFull_Screen = QAction()
        self.actionFull_Screen.triggered.connect(self.toggleFullscreen)
        self.actionFull_Screen.setShortcut('F11')
        self.addAction(self.actionFull_Screen)  # action only works when added to a widget
        self.maximized = False
        self.loadPluginsSignal.connect(self.loadPlugins)
        self.setStatusBar(IconStatusBar())
        QTimer.singleShot(0, self.loadPluginsSignal.emit)  # let event loop start before loading plugins
        self.installEventFilter(self)

    def loadPlugins(self) -> None:
        """Load :class:`plugins<esibd.plugins.Plugin>` in main thread."""
        self.pluginManager = PluginManager()
        self.pluginManager.loadPlugins()

    def toggleFullscreen(self) -> None:
        """Toggles full screen mode."""
        if self.isFullScreen():  # return to previous view
            self.showMaximized() if self.maximized else self.showNormal()  # pylint: disable = expression-not-assigned
        else:  # goFullscreen
            self.maximized = self.isMaximized()  # workaround for bug https://github.com/qutebrowser/qutebrowser/issues/2778
            self.showFullScreen()

    def restoreUiState(self) -> None:
        """Restores size and location of main window."""
        try:
            self.restoreGeometry(qSet.value(GEOMETRY, self.saveGeometry()))
            # Note that the state on startup will not include dynamic displays which open only as needed. Thus the state cannot be restored.
            # * need to restore before starting event loop to avoid Unable to set geometry warning
        except TypeError as e:
            print(f'Could not restore window state: {e}')  # noqa: T201
            self.resize(800, 400)
            self.saveUiState()

    def saveUiState(self) -> None:
        """Save size and location of main window."""
        qSet.setValue(GEOMETRY, self.saveGeometry())
        self.pluginManager.Settings.raiseDock(showPlugin=True)  # need to be visible to give right dimensions
        self.app.processEvents()
        qSet.setValue(SETTINGSWIDTH, self.pluginManager.Settings.mainDisplayWidget.width())  # store width
        qSet.setValue(SETTINGSHEIGHT, self.pluginManager.Settings.mainDisplayWidget.height())  # store height
        qSet.setValue(CONSOLEHEIGHT, self.pluginManager.Console.mainDisplayWidget.height())  # store height

    def closeEvent(self, a0: 'QCloseEvent | None') -> None:
        """Triggers PluginManager to close all plugins and all related communication.

        :param a0: The close event.
        :type a0: QCloseEvent
        """
        if a0:
            if not self.pluginManager.loading:
                if self.closeApplication():
                    a0.accept()
                else:
                    a0.ignore()
            else:
                a0.ignore()

    def closeApplication(self, restart: bool = False, confirm: bool = True) -> bool:
        """Close communication and plugins, restart if applicable.

        :param restart: If True, application will restart in a clean new process, defaults to False
        :type restart: bool, optional
        :param confirm: Indicates if user should confirm in case devices are initialized, defaults to True
        :type confirm: bool, optional
        :return: Returns True if the application is closing.
        :rtype: bool
        """
        if self.pluginManager.DeviceManager.initialized():
            if confirm and not CloseDialog(prompt='Acquisition is still running. Do you really want to close?', ok='Stop communication and scans and close').exec():
                return False
            self.pluginManager.DeviceManager.closeCommunication(closing=True)
        if restart:
            self.pluginManager.logger.print('Restarting.', flag=PRINT.EXPLORER)
        self.pluginManager.closePlugins()
        if restart:
            self.app.sharedAppStr.detach()
            python = sys.executable
            os.execl(python, python, *sys.argv)  # noqa: S606
        return True


class Application(QApplication):
    """Application that has a mouseInterceptor."""

    sharedAppStr: QSharedMemory
    mainWindow: EsibdExplorer
    mouseInterceptor: 'MouseInterceptor'
    splashScreen: 'SplashScreen'


class PluginManager:  # noqa: PLR0904
    """The :class:`~esibd.core.PluginManager` is responsible for loading all internal and external Plugins.

    It catches errors or incompatibilities while loading,
    initializing, and closing plugins. Users will only see the plugin selection
    interface accessed from the :ref:`sec:settings` plugin.
    The :class:`~esibd.core.PluginManager` can be accessed from the :ref:`sec:console` as `PluginManager`.
    It allows plugins to interact by using unique plugin names as attributes, e.g.
    `self.pluginManager.ISEG` or `self.pluginManager.DeviceManager`.
    """

    class SignalCommunicate(QObject):
        """Bundle pyqtSignals."""

        finalizeSignal = pyqtSignal()

    VERSION = 'Version'
    SUPPORTEDVERSION = 'Supported Version'
    ENABLED = 'Enabled'
    PREVIEWFILETYPES = 'PREVIEWFILETYPES'
    DESCRIPTION = 'DESCRIPTION'
    OPTIONAL = 'OPTIONAL'
    PLUGIN_TYPE = 'PLUGINTYPE'
    DEPENDENCYPATH = 'dependencyPath'
    SOURCECODEPATH = 'sourceCodePath'
    ICONFILE = 'iconFile'
    ICONFILEDARK = 'iconFileDark'

    def __init__(self) -> None:
        """Initialize PluginManager."""
        self.app = cast('Application', QApplication.instance())
        self.mainWindow: EsibdExplorer = self.app.mainWindow  # type: ignore  # noqa: PGH003
        self.testing_state = False  # has to be defined before logger
        self.logger = Logger(pluginManager=self)
        self.logger.print('Loading.', flag=PRINT.EXPLORER)
        self.pluginFile: 'Path | None' = None
        self.tree: 'TreeWidget | None' = None
        self.mainWindow.setTabPosition(Qt.DockWidgetArea.LeftDockWidgetArea, QTabWidget.TabPosition.North)
        self.mainWindow.setTabPosition(Qt.DockWidgetArea.RightDockWidgetArea, QTabWidget.TabPosition.North)
        self.mainWindow.setTabPosition(Qt.DockWidgetArea.TopDockWidgetArea, QTabWidget.TabPosition.North)
        self.mainWindow.setTabPosition(Qt.DockWidgetArea.BottomDockWidgetArea, QTabWidget.TabPosition.North)
        self.mainWindow.setDockOptions(QMainWindow.DockOption.AllowTabbedDocks | QMainWindow.DockOption.AllowNestedDocks
                                     | QMainWindow.DockOption.GroupedDragging | QMainWindow.DockOption.AnimatedDocks)
        self.signalComm = self.SignalCommunicate()
        self.signalComm.finalizeSignal.connect(self.finalizeUiState)
        self.plugins = []  # A central plugin list that allows plugins to interact with each other.

        self.pluginNames = []
        self.firstControl: 'Plugin | None' = None
        self.firstDisplay: 'Plugin | None' = None
        self.tabBars = None
        self._loading = 0
        self.finalizing = False
        self.closing = False
        # Note: always instantiate QMessageBox and other QWidgets in __init__ and not on class level to prevent initialization before QApplication
        self.qm = QMessageBox(QMessageBox.Icon.Information, 'Warning!', 'v!', buttons=QMessageBox.StandardButton.Ok)

        # definitions for type hinting
        self.PID: PID
        self.UCM: UCM
        self.Explorer: Explorer
        self.Settings: Settings
        self.Console: Console
        self.Browser: Browser
        self.Tree: Tree
        self.Text: Text
        self.Notes: Notes
        self.DeviceManager: DeviceManager
        self.ChannelManager, self.Device, self.LiveDisplay, self.Scan = self._main_types()

    def _main_types(self) -> tuple[type['ChannelManager'], type['Device'], type['LiveDisplay'], type['Scan']]:
        """Lazy import to allow use of isinstance without circular imports."""
        from esibd.plugins import ChannelManager, Device, LiveDisplay, Scan  # noqa: PLC0415
        return ChannelManager, Device, LiveDisplay, Scan

    @property
    def loading(self) -> bool:
        """Flag that can be used to suppress events while plugins are loading, initializing, or closing."""
        return self._loading != 0

    @loading.setter
    def loading(self, loading: bool) -> None:
        if loading:
            self._loading += 1
        else:
            self._loading -= 1

    def loadPlugins(self) -> None:  # noqa: C901, PLR0915
        """Load all enabled plugins."""
        self.updateTheme()
        self.app.splashScreen.show()

        self.mainWindow.setUpdatesEnabled(False)
        self.loading = True  # some events should not be triggered until after the UI is completely initialized
        self.closing = False

        # self.mainWindow.configPath not yet be available -> use directly from qSet
        self.pluginFile = getValidConfigPath() / 'plugins.ini'
        self.plugins: 'list[Plugin]' = []
        self.pluginNames: list[str] = []
        self.firstControl = None
        self.firstDisplay = None

        self.confParser = configparser.ConfigParser()
        if self.pluginFile.exists():
            self.confParser.read(self.pluginFile)
        self.confParser[INFO] = infoDict('PluginManager')

        import esibd.provide_plugins  # pylint: disable = import-outside-toplevel  # avoid circular import  # noqa: PLC0415
        self.loadPluginsFromModule(Module=esibd.provide_plugins, dependencyPath=internalMediaPath,
                                   sourceCodePath=Path(esibd.provide_plugins.__file__.replace('provide_plugins', 'plugins')))
        self.loadPluginsFromPath(esibdPath / 'examples')
        self.loadPluginsFromPath(esibdPath / 'devices')
        self.loadPluginsFromPath(esibdPath / 'scans')
        self.loadPluginsFromPath(esibdPath / 'displays')
        self.loadPluginsFromPath(esibdPath / 'controls')
        self.loadPluginsFromPath(getValidPluginPath())

        obsoletePluginNames = [name for name in self.confParser if name != Parameter.DEFAULT.upper() and name != INFO and name not in self.pluginNames]
        if len(obsoletePluginNames) > 0:
            self.logger.print(f"Removing obsolete plugin data: {', '.join(obsoletePluginNames)}", flag=PRINT.WARNING)
            for obsoletePluginName in obsoletePluginNames:
                self.confParser.pop(obsoletePluginName)
        if self.pluginFile:
            with self.pluginFile.open('w', encoding=UTF8) as configFile:
                self.confParser.write(configFile)

        if hasattr(self, 'Settings'):
            self.Settings.init()  # init internal settings and settings of devices and scans which have been added in the meantime
        self.provideDocks()  # add plugin docks before loading = False

        if hasattr(self, 'Tree'):
            self.plugins.append(self.plugins.pop(self.plugins.index(self.Tree)))  # move Tree to end to have lowest priority to handle files
        if hasattr(self, 'Text'):
            self.plugins.append(self.plugins.pop(self.plugins.index(self.Text)))  # move Text to end to have lowest priority to handle files
        if hasattr(self, 'PID') and self.PID in self.plugins:
            self.plugins.append(self.plugins.pop(self.plugins.index(self.PID)))  # move PID to end to connectAllSources after all devices are initialized
        if hasattr(self, 'UCM') and self.UCM in self.plugins:
            self.plugins.append(self.plugins.pop(self.plugins.index(self.UCM)))  # move UCM to end to connectAllSources after all devices and PID are initialized
        self.loading = False
        self.finalizing = True
        self.finalizeInit()
        self.afterFinalizeInit()
        self.toggleVideoRecorder()
        self.mainWindow.setUpdatesEnabled(True)
        self.finalizing = False
        self.toggleTitleBarDelayed(update=True, delay=1000)
        QTimer.singleShot(0, self.signalComm.finalizeSignal.emit)  # add delay to make sure application is ready to process updates, but make sure it is done in main thread
        self.app.splashScreen.close()  # close as soon as mainWindow is ready
        if getTestMode():
            self.logger.print('Test mode is active! Simulating Data!', flag=PRINT.WARNING)
        self.logger.print('Ready.', flag=PRINT.EXPLORER)

    def loadPluginsFromPath(self, path: 'Path | None') -> None:
        """Load plugins from a path.

        :param path: The path in which to look for plugins.
        :type path: pathlib.Path
        """
        if path:  # noqa: PLR1702
            for directory in [directory for directory in path.iterdir() if directory.is_dir()]:
                for file in [file for file in directory.iterdir() if file.name.endswith('.py')]:
                    try:
                        Module = dynamicImport(file.stem, file)
                    except Exception as e:  # pylint: disable = broad-except  # we have no control about the exception a plugin can possibly throw here  # noqa: BLE001
                        # No unpredictable Exception in a single plugin should break the whole application
                        self.logger.print(f'Could not import module {file.stem}: {e}', flag=PRINT.ERROR)
                        # Note, this will not show in the Console Plugin which is not yet fully initialized. -> show in separate dialog window:
                        self.qm.setText(f'Could not import module {file.stem}: {e}')
                        self.qm.setIcon(QMessageBox.Icon.Warning)
                        self.qm.open()
                        self.qm.raise_()
                    else:
                        if hasattr(Module, 'providePlugins'):
                            if Module and type(Module.providePlugins()) is list:
                                self.loadPluginsFromModule(Module=Module, dependencyPath=file.parent, sourceCodePath=file)
                            else:
                                self.logger.print(f'Could not load module {file.stem}. Make sure providePlugins returns list of valid plugins.', flag=PRINT.ERROR)
                        # silently ignore dependencies which do not define providePlugins

    def loadPluginsFromModule(self, Module: 'ModuleType', dependencyPath: Path, sourceCodePath: Path) -> None:
        """Load plugins from a module.

        :param Module: A module providing one or multiple plugins.
        :type Module: ModuleType
        :param dependencyPath: The path where dependencies like icons are stored, defaults to None
        :type dependencyPath: pathlib.Path, optional
        :param sourceCodePath: Path to the plugin source code.
        :type sourceCodePath: Path
        """
        for Plugin in Module.providePlugins():
            # requires loading all dependencies, no matter if plugin is used or not
            # if a dependency of an unused plugin causes issues, report it and remove the corresponding file from the plugin folder until fixed.
            # might consider different import mechanism which does not require import unless plugins are enabled.
            self.pluginNames.append(Plugin.name)
            if Plugin.name not in self.confParser:  # add plugin to confParser
                self.confParser.read_dict({Plugin.name: {self.ENABLED: not Plugin.optional, self.VERSION: Plugin.version, self.SUPPORTEDVERSION: Plugin.supportedVersion,
                                                self.DEPENDENCYPATH: dependencyPath, self.SOURCECODEPATH: sourceCodePath, self.ICONFILE: Plugin.iconFile,
                                                self.ICONFILEDARK: Plugin.iconFileDark, self.PLUGIN_TYPE: str(Plugin.pluginType.value), self.PREVIEWFILETYPES: '',
                                                self.DESCRIPTION: Plugin.documentation or Plugin.__doc__, self.OPTIONAL: str(Plugin.optional)}})
            else:  # update
                self.confParser[Plugin.name][self.VERSION] = Plugin.version
                self.confParser[Plugin.name][self.SUPPORTEDVERSION] = Plugin.supportedVersion
                self.confParser[Plugin.name][self.DEPENDENCYPATH] = dependencyPath.as_posix()
                self.confParser[Plugin.name][self.SOURCECODEPATH] = sourceCodePath.as_posix()
                self.confParser[Plugin.name][self.ICONFILE] = Plugin.iconFile
                self.confParser[Plugin.name][self.ICONFILEDARK] = Plugin.iconFileDark
                self.confParser[Plugin.name][self.PLUGIN_TYPE] = str(Plugin.pluginType.value)
                self.confParser[Plugin.name][self.DESCRIPTION] = Plugin.documentation or Plugin.__doc__
                self.confParser[Plugin.name][self.OPTIONAL] = str(Plugin.optional)
            if self.confParser[Plugin.name][self.ENABLED] == 'True':
                plugin = self.loadPlugin(Plugin, dependencyPath=dependencyPath, sourceCodePath=sourceCodePath)
                if plugin:
                    self.confParser[Plugin.name][self.PREVIEWFILETYPES] = ', '.join(plugin.getSupportedFiles())  # requires instance

    def loadPlugin(self, Plugin: 'type[Plugin]', dependencyPath: Path, sourceCodePath: Path) -> 'Plugin | None':
        """Load a single plugin.

        Plugins must have a static name and pluginType.
        'mainWindow' is passed to enable flexible integration, but should only be used at your own risk.
        Enabled state is saved and restored from an independent file and can also be edited using the plugins dialog.

        :param Plugin: The class of the Plugin to be loaded.
        :type Plugin: Plugin
        :param dependencyPath: The path where dependencies like icons are stored, defaults to None
        :type dependencyPath: pathlib.Path, optional
        :param sourceCodePath: Path to the plugin source code.
        :type sourceCodePath: Path
        :return: Instance of the plugin.
        :rtype: class:`~esibd.plugins.Plugin`
        """
        QApplication.processEvents()  # break down expensive initialization to allow update splash screens while loading
        self.logger.print(f'loadPlugin {Plugin.name}', flag=PRINT.DEBUG)
        if not pluginSupported(Plugin.supportedVersion):
            # * we ignore micro (packaging.version name for patch)
            self.logger.print(f'Plugin {Plugin.name} supports {PROGRAM_NAME} {Plugin.supportedVersion}. It is not compatible with {PROGRAM_NAME} {PROGRAM_VERSION}.',
                               flag=PRINT.WARNING)
            return None
        if Plugin.name in [plugin.name for plugin in self.plugins]:
            self.logger.print(f'Ignoring duplicate plugin {Plugin.name}.', flag=PRINT.WARNING)
        else:
            try:
                plugin = Plugin(pluginManager=self, dependencyPath=dependencyPath, sourceCodePath=sourceCodePath)
                setattr(self.__class__, plugin.name, plugin)  # use attributes to access for communication between plugins
            except Exception:  # pylint: disable = broad-except  # we have no control about the exception a plugin can possibly throw  # noqa: BLE001
                # No unpredictable exception in a single plugin should break the whole application
                self.logger.print(f'Could not load plugin {Plugin.name} {Plugin.version}: {traceback.format_exc()}', flag=PRINT.ERROR)
            else:
                self.plugins.append(plugin)
                return plugin
        return None

    def provideDocks(self) -> None:
        """Create docks and positions them as defined by :attr:`~esibd.core.PluginManager.pluginType`."""
        if not hasattr(self, 'topDock'):  # else reuse old
            self.topDock = QDockWidget()  # dummy to align other docks to
            self.topDock.setObjectName('topDock')  # required to restore state
            QApplication.processEvents()
            self.topDock.hide()
        # * when using TopDockWidgetArea there is a superfluous separator on top of the statusbar -> use BottomDockWidgetArea
        # first 4 plugins define layout
        self.mainWindow.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.topDock)
        self.DeviceManager.provideDock()
        self.Settings.provideDock()
        self.Console.provideDock()
        self.Browser.provideDock()
        pluginTypeOrder = [PLUGINTYPE.DEVICEMGR, PLUGINTYPE.CONTROL, PLUGINTYPE.CONSOLE, PLUGINTYPE.CHANNELMANAGER,
                            PLUGINTYPE.INPUTDEVICE, PLUGINTYPE.OUTPUTDEVICE, PLUGINTYPE.SCAN]
        for plugin in sorted((plugin for plugin in self.plugins if plugin.pluginType in pluginTypeOrder),
            key=lambda x: pluginTypeOrder.index(x.pluginType)):
            # Note: Plugins with type PLUGINTYPE.INTERNAL, PLUGINTYPE.DISPLAY will be loaded by their parent items later if needed
            # display plugins will be initialized when needed, internal plugins do not need GUI
            try:
                plugin.provideDock()
            except Exception:  # noqa: BLE001
                self.logger.print(f'Could not load GUI of plugin {plugin.name} {plugin.version}: {traceback.format_exc()}', flag=PRINT.ERROR)
                delattr(self.__class__, plugin.name)  # remove attribute
                self.plugins.pop(self.plugins.index(plugin))  # avoid any further undefined interaction
            self.app.splashScreen.raise_()  # some operations (likely tabifyDockWidget) will cause the main window to get on top of the splash screen

    def finalizeInit(self) -> None:
        """Finalize initialization after all other plugins have been initialized."""
        removePlugins: 'list[Plugin]' = []
        for plugin in self.plugins:
            QApplication.processEvents()
            if plugin.initializedDock:
                try:
                    plugin.finalizeInit()
                except Exception:  # noqa: BLE001
                    self.logger.print(f'Could not finalize plugin {plugin.name} {plugin.version}: {traceback.format_exc()}', flag=PRINT.ERROR)
                    plugin.closeGUI()
                    removePlugins.append(plugin)
        for plugin in removePlugins:
            self.plugins.pop(self.plugins.index(plugin))  # avoid any further undefined interaction

    def afterFinalizeInit(self) -> None:
        """Finalize initialization after all other plugins have been initialized."""
        removePlugins: 'list[Plugin]' = []
        for plugin in self.plugins:
            QApplication.processEvents()
            if plugin.initializedDock:
                try:
                    plugin.afterFinalizeInit()
                except Exception:  # noqa: BLE001
                    self.logger.print(f'Could not complete finalization of plugin {plugin.name} {plugin.version}: {traceback.format_exc()}', flag=PRINT.ERROR)
                    plugin.closeGUI()
                    removePlugins.append(plugin)
        for plugin in removePlugins:
            self.plugins.pop(self.plugins.index(plugin))  # avoid any further undefined interaction

    @property
    def resizing(self) -> bool:
        """Indicates if any plugin is currently resizing."""
        return any(plugin.resizing for plugin in self.plugins)

    @property
    def testing(self) -> bool:
        """Indicates if the PluginManager or any individual plugin is currently testing."""
        for plugin in self.plugins:
            if plugin.testing_state or (isinstance(plugin, self.ChannelManager) and  # noqa: PLR0916
                 ((plugin.staticDisplay and plugin.staticDisplay.testing_state) or (plugin.liveDisplay and plugin.liveDisplay.testing_state))):
                return True
        return self.testing_state

    @testing.setter
    def testing(self, state: bool) -> None:
        self.testing_state = state

    def test(self) -> None:
        """Test most features of for all plugins. Calls :meth:`~esibd.core.PluginManager.runTestParallel`."""
        self.testing = True
        self.Settings.updateSessionPath()  # avoid interference with undefined files from previous test run
        self.logger.print('Start testing.')
        self.logger.openTestLogFile()
        time.sleep(1)
        timer = Timer(0, self.runTestParallel)
        timer.start()
        timer.name = 'TestingThread'
        self.Console.mainConsole.input.setText('PluginManager.stopTest()')  # prepare to stop

    def stopTest(self) -> None:
        """Stop test after current step completes."""
        self.logger.print('Stopping test.')
        self.testing = False
        for plugin in self.plugins:
            plugin.signalComm.testCompleteSignal.emit()

    def runTestParallel(self) -> None:
        """Run test of all plugins from parallel thread."""
        self.logger.print('Start testing all plugins.')
        # this will record the entire test session, consider compressing the file with third party software before publication
        self.DeviceManager.testControl(self.DeviceManager.videoRecorderAction, value=True)
        for plugin in self.plugins:
            self.logger.print(f'Starting testing for {plugin.name} {plugin.version}.')
            plugin.testing = True
            # make sure dock is provided in main thread
            QTimer.singleShot(0, plugin.provideDock)  # most dock will already be open after loading test files, not all test files may be present outside of production environment
            if plugin.waitForCondition(condition=lambda plugin=plugin: hasattr(plugin, 'videoRecorderAction'), timeoutMessage=f'dock of {plugin.name}'):
                plugin.raiseDock(showPlugin=True)
                plugin.runTestParallel()
                if not plugin.waitForCondition(condition=lambda plugin=plugin: not plugin.testing_state, timeout=plugin.testTimeout,
                                               timeoutMessage=f'testing {plugin.name} to complete.'):
                    plugin.signalComm.testCompleteSignal.emit()
            if not self.testing:
                break
            self.DeviceManager.bufferLagging()
        self.DeviceManager.testControl(self.DeviceManager.videoRecorderAction, value=False)
        QTimer.singleShot(0, self.logger.closeTestLogFile)
        self.testing = False

    def showThreads(self) -> None:
        """Show all currently running threads in the Text plugin."""
        self.Text.setText('\n'.join([thread.name for thread in threading.enumerate()]), showPlugin=True)

    def managePlugins(self) -> None:  # noqa: C901, PLR0915
        """Select which plugins should be enabled."""
        if not self.pluginFile:
            return
        dlg = QDialog(self.mainWindow, Qt.WindowType.WindowStaysOnTopHint)
        dlg.resize(800, 400)
        dlg.setWindowTitle('Select Plugins')
        dlg.setWindowIcon(Icon(internalMediaPath / 'block--pencil.png'))
        lay = QGridLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        self.tree = TreeWidget()
        self.tree.setItemDelegate(TransparentTextDelegate(self.tree))
        self.tree.setHeaderLabels(['', 'Name', 'Enabled', 'Version', 'Supported Version', 'Type', 'Preview File Types', 'Description (See tooltips!)'])
        self.tree.setColumnCount(8)
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(2, 50)
        self.tree.setColumnWidth(3, 50)
        self.tree.setColumnWidth(4, 50)
        self.tree.setSortingEnabled(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.toggleSelectionContextMenu)
        header = self.tree.header()
        if header:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(6, 150)
        root = self.tree.invisibleRootItem()
        if not root:
            return
        lay.addWidget(self.tree)
        buttonBox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        okButton = buttonBox.button(QDialogButtonBox.StandardButton.Ok)
        if okButton:
            okButton.setText('Stop communication and restart' if self.DeviceManager.initialized() else 'Restart')
        buttonBox.accepted.connect(dlg.accept)
        buttonBox.rejected.connect(dlg.reject)
        buttonBox.setContentsMargins(5, 5, 5, 5)
        lay.addWidget(buttonBox)
        confParser = configparser.ConfigParser()
        if self.pluginFile.exists():
            confParser.read(self.pluginFile)
        confParser[INFO] = infoDict('PluginManager')
        for name, item in confParser.items():
            if name != Parameter.DEFAULT.upper() and name != INFO:
                self.addPluginTreeWidgetItem(item=item, name=name)
        self.tree.sortByColumn(1, Qt.SortOrder.AscendingOrder)  # name
        self.tree.sortByColumn(2, Qt.SortOrder.DescendingOrder)  # non optional, enabled, disabled

        dlg.setLayout(lay)
        if dlg.exec():
            self.DeviceManager.closeCommunication(closing=True, message='Stopping communication before restarting.')
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            for child in [root.child(i) for i in range(root.childCount())]:
                if child:
                    name = child.text(1)
                    enabled = True
                    internal = True
                    if self.tree.itemWidget(child, 2):
                        enabled = cast('CheckBox', (self.tree.itemWidget(child, 2))).isChecked()
                        internal = False
                    if not internal:
                        confParser[name][self.ENABLED] = str(enabled)
            self.tree.deleteLater()
            self.tree = None
            with self.pluginFile.open('w', encoding=UTF8) as configFile:
                confParser.write(configFile)
            self.mainWindow.closeApplication(restart=True)
            QApplication.restoreOverrideCursor()

    def addPluginTreeWidgetItem(self, item: Mapping, name: str) -> None:
        """Add a row for given plugin. If not a core plugin it can be enabled or disabled using the checkbox.

        :param item: Dictionary with plugin information.
        :type item: Mapping
        :param name: Plugin name.
        :type name: str
        """
        if self.tree:
            pluginTreeWidget = QTreeWidgetItem(self.tree.invisibleRootItem())
            if item[self.ICONFILE]:
                pluginTreeWidget.setIcon(0, Icon(Path(item[self.DEPENDENCYPATH]) / (item[self.ICONFILEDARK] if getDarkMode() and item[self.ICONFILEDARK] else item[self.ICONFILE])))
            else:
                pluginTreeWidget.setIcon(0, Icon(Path(item[self.DEPENDENCYPATH]) / ('help_large_dark.png' if getDarkMode() else 'help_large.png')))
            pluginTreeWidget.setText(1, name)
            if item[self.OPTIONAL] == 'True':
                checkbox = CheckBox()
                checkbox.setChecked(item[self.ENABLED] == 'True')
                self.tree.setItemWidget(pluginTreeWidget, 2, checkbox)
                pluginTreeWidget.setText(2, item[self.ENABLED])
            else:
                pluginTreeWidget.setText(2, 'z Not Optional')
            pluginTreeWidget.setForeground(2, QColor(0, 0, 0, 0))  # make sorting text transparent
            versionLabel = QLabel()
            versionLabel.setText(item[self.VERSION])
            self.tree.setItemWidget(pluginTreeWidget, 3, versionLabel)
            pluginTreeWidget.setText(3, item[self.VERSION])  # needed for sorting
            pluginTreeWidget.setForeground(3, QColor(0, 0, 0, 0))  # make sorting text transparent
            supportedVersionLabel = QLabel()
            supportedVersionLabel.setText(item[self.SUPPORTEDVERSION])
            supportedVersionLabel.setStyleSheet(f"color: {'red' if not pluginSupported(item[self.SUPPORTEDVERSION]) else 'green'}")
            self.tree.setItemWidget(pluginTreeWidget, 4, supportedVersionLabel)
            pluginTreeWidget.setText(4, item[self.SUPPORTEDVERSION])
            pluginTreeWidget.setForeground(4, QColor(0, 0, 0, 0))  # make sorting text transparent
            typeLabel = QLabel()
            typeLabel.setText(item[self.PLUGIN_TYPE])
            self.tree.setItemWidget(pluginTreeWidget, 5, typeLabel)
            pluginTreeWidget.setText(5, item[self.PLUGIN_TYPE])
            pluginTreeWidget.setForeground(5, QColor(0, 0, 0, 0))  # make sorting text transparent
            previewFileTypesLabel = QLabel()
            previewFileTypesLabel.setText(item[self.PREVIEWFILETYPES])
            previewFileTypesLabel.setToolTip(item[self.PREVIEWFILETYPES])
            self.tree.setItemWidget(pluginTreeWidget, 6, previewFileTypesLabel)
            descriptionLabel = QLabel()
            description = item[self.DESCRIPTION]
            if description:
                descriptionLabel.setText(description.splitlines()[0][:100])
                descriptionLabel.setToolTip(description)
            self.tree.setItemWidget(pluginTreeWidget, 7, descriptionLabel)

    def toggleSelectionContextMenu(self, pos: QPoint) -> None:
        """Context menu to toggle selection of plugins.

        :param pos: The position of the context menu.
        :type pos: QPoint
        """
        if self.tree and self.tree.columnAt(pos.x()) == 2:  # noqa: PLR2004
            item = self.tree.itemAt(pos)
            widget = self.tree.itemWidget(item, 2)
            if widget and isinstance(widget, CheckBox):
                menu = QMenu(self.tree)
                menu.addAction('Select All', lambda: self.toggleSelection(enabled=True))
                menu.addAction('Select None', lambda: self.toggleSelection(enabled=False))
                menu.exec(self.tree.mapToGlobal(pos))

    def toggleSelection(self, enabled: bool) -> None:
        """Toggle selection of channels. Mostly used for development purposes when running tests with changing selection of plugins.

        :param enabled: Enable all or none of the channels.
        :type enabled: bool
        """
        if self.tree:
            root = self.tree.invisibleRootItem()
            if root:
                for child in [root.child(i) for i in range(root.childCount())]:
                    if child and self.tree.itemWidget(child, 2):
                        cast('CheckBox', (self.tree.itemWidget(child, 2))).setChecked(enabled)

    def closePlugins(self) -> None:
        """Close all open connections and leave hardware in save state (e.g. voltage off)."""
        self.logger.print('Closing Plugins.', flag=PRINT.EXPLORER)
        qSet.sync()
        self.loading = True  # skip UI updates
        self.mainWindow.saveUiState()
        self.closing = True
        self.mainWindow.setUpdatesEnabled(False)  # do not update window but also do not become unresponsive

        for plugin in self.plugins:
            name = plugin.name  # may not be accessible after del
            version = plugin.version
            try:
                plugin.closeGUI()
                del plugin
                QApplication.processEvents()
            except Exception:  # pylint: disable = broad-except  # we have no control about the exception a plugin can possibly throw  # noqa: BLE001
                # No unpredictable exception in a single plugin should break the whole application
                self.logger.print(f'Could not close plugin {name} {version}: {traceback.format_exc()}', flag=PRINT.ERROR)
        self.logger.close()

    def finalizeUiState(self) -> None:
        """Restores dimensions of core plugins."""
        self.Settings.raiseDock()  # make sure settings tab visible after start
        self.Console.toggleVisible()
        QApplication.processEvents()

        width = qSet.value(SETTINGSWIDTH, self.Settings.mainDisplayWidget.width(), type=int)
        if width is not None:
            self.Settings.mainDisplayWidget.setMinimumWidth(width)
            self.Settings.mainDisplayWidget.setMaximumWidth(width)
        height = qSet.value(SETTINGSHEIGHT, self.Settings.mainDisplayWidget.height(), type=int)
        if height is not None:
            self.Settings.mainDisplayWidget.setMinimumHeight(height)
            self.Settings.mainDisplayWidget.setMaximumHeight(height)
        height = qSet.value(CONSOLEHEIGHT, self.Console.mainDisplayWidget.height(), type=int)
        if height is not None and self.Settings.showConsoleAction and self.Settings.showConsoleAction.state:
            self.Console.mainDisplayWidget.setMinimumHeight(height)
            self.Console.mainDisplayWidget.setMaximumHeight(height)
        QTimer.singleShot(1000, self.resetMainDisplayWidgetLimits)
        self.Explorer.raiseDock()  # only works if given at least .3 ms delay after loadPlugins completed
        self.Browser.raiseDock()

    def resetMainDisplayWidgetLimits(self) -> None:
        """Reset limits to allow for user scaling of plugin sizes."""
        # Needs to be called after releasing event loop or changes will not be applied.
        self.Settings.mainDisplayWidget.setMinimumWidth(100)
        self.Settings.mainDisplayWidget.setMaximumWidth(10000)
        self.Settings.mainDisplayWidget.setMinimumHeight(50)
        self.Settings.mainDisplayWidget.setMaximumHeight(10000)
        self.Console.mainDisplayWidget.setMinimumHeight(50)
        self.Console.mainDisplayWidget.setMaximumHeight(10000)

    def getMainPlugins(self) -> 'list[Plugin]':
        """Return all plugins found in the control section, including devices, controls, and scans."""
        return self.getPluginsByType([PLUGINTYPE.INPUTDEVICE, PLUGINTYPE.OUTPUTDEVICE, PLUGINTYPE.CONTROL, PLUGINTYPE.SCAN])

    def getPluginsByType(self, pluginTypes: PLUGINTYPE | list[PLUGINTYPE]) -> 'list[Plugin]':
        """Return all plugins of the specified type.

        :param pluginTypes: A single type or list of types.
        :type pluginTypes: :meth:`~esibd.const.PLUGINTYPE`
        :return: List of matching plugins.
        :rtype: [:class:`~esibd.plugins.Plugin`]
        """
        if isinstance(pluginTypes, list):
            return [plugin for plugin in self.plugins if plugin.pluginType in pluginTypes]
        return [plugin for plugin in self.plugins if plugin.pluginType == pluginTypes]

    A = TypeVar('A', 'Scan', 'Device', 'ChannelManager')

    def getPluginsByClass(self, parentClasses: 'type[A] | tuple[type[A]]') -> list[A]:
        """Return all plugins of the specified type.

        :param parentClasses: A single class or list of classes.
        :type parentClasses: 'Plugin' | 'list[Plugin]'
        :return: List of matching plugins.
        :rtype: [:class:`~esibd.plugins.Plugin`]
        """
        return [plugin for plugin in self.plugins if isinstance(plugin, parentClasses)]

    def toggleTitleBarDelayed(self, update: bool = False, delay: int = 500) -> None:
        """Delay toggleTitleBar until GUI updates have been completed.

        :param update: Indicates if the list of tabBars should be updated, defaults to False
        :type update: bool, optional
        :param delay: Delay in ms, defaults to 500
        :type delay: int, optional
        """
        QTimer.singleShot(delay, lambda: self.toggleTitleBar(update=update))

    def toggleTitleBar(self, update: bool = False) -> None:
        """Toggles between showing icons or text in tabs.

        :param update: Indicates if the list of tabBars should be updated, defaults to False
        :type update: bool, optional
        """
        if not self.tabBars or update:
            # this is very expensive as it traverses the entire QObject hierarchy, but this is the only way to find a new tabbar that is created by moving docks around
            # keep reference to tabBars. this should only need update if dock topLevelChanged
            self.tabBars = self.mainWindow.findChildren(QTabBar)
        if self.tabBars:
            for tabBar in self.tabBars:
                # has to be called in main thread!
                tabBar.setStyleSheet(
        f'QTabBar::tab {{font-size: 1px; margin-right: -18px; color: transparent}}QTabBar::tab:selected {{font-size: 12px;margin-right: 0px; color: {colors.highlight}}}'
                        if getIconMode() == 'Icons' else '')
        if not self.loading:
            for plugin in self.plugins:
                if plugin.initializedDock:
                    plugin.toggleTitleBar()

    def updateTheme(self) -> None:
        """Update application theme while showing a splash screen if necessary."""
        if not self.loading:
            self.app.splashScreen.show()
            self.mainWindow.setUpdatesEnabled(False)
        style = QApplication.style()
        if style:
            pal = style.standardPalette()
            pal.setColor(QPalette.ColorRole.Base, QColor(colors.bg))
            pal.setColor(QPalette.ColorRole.AlternateBase, QColor(colors.bg))
            pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors.bg))
            pal.setColor(QPalette.ColorRole.Window, QColor(colors.bg))
            pal.setColor(QPalette.ColorRole.Button, QColor(colors.bgAlt2))  # also comboboxes
            pal.setColor(QPalette.ColorRole.Text, QColor(colors.fg))
            pal.setColor(QPalette.ColorRole.ToolTipText, QColor(colors.fg))
            pal.setColor(QPalette.ColorRole.WindowText, QColor(colors.fg))
            pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors.fg))
            pal.setColor(QPalette.ColorRole.ButtonText, QColor(colors.fg))
            pal.setColor(QPalette.ColorRole.BrightText, QColor(colors.highlight))
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor(colors.highlight))
            self.styleSheet = f"""
            QTreeView::item {{border: none; outline: 0;}}
            QLineEdit     {{background-color:{colors.bgAlt2};}}
            QPlainTextEdit{{background-color:{colors.bgAlt2};}}
            QSpinBox      {{background-color:{colors.bgAlt2}; color:{colors.fg}; border-style:none;}}
            QDoubleSpinBox{{background-color:{colors.bgAlt2}; color:{colors.fg}; border-style:none;}}
            QMainWindow::separator      {{background-color:{colors.bgAlt2};    width:4px; height:4px;}}
            QMainWindow::separator:hover{{background-color:{colors.highlight}; width:4px; height:4px;}}
            QWidget::separator          {{background-color:{colors.bgAlt2};    width:4px; height:4px;}}
            QToolBar{{background-color:{colors.bgAlt1}; margin:0px 0px 0px 0px;}}
            QToolBarExtension {{qproperty-icon: url({(internalMediaPath / 'chevron_double_dark.png').as_posix()
                                                     if getDarkMode() else (internalMediaPath / 'chevron_double_light.png').as_posix()});}}
            QToolTip{{background-color: {colors.bg}; color: {colors.fg}; border: black solid 1px}}
            QCheckBox::indicator         {{border:1px solid {colors.fg}; width: 12px;height: 12px;}}
            QCheckBox::indicator:checked {{border:1px solid {colors.fg}; width: 12px;height: 12px; image: url({(internalMediaPath / 'check_dark.png').as_posix()
                                                                                                    if getDarkMode() else (internalMediaPath / 'check.png').as_posix()})}}
            QTabBar::tab         {{margin:0px 0px 2px 0px; padding:4px; border-width:0px; }}
            QTabBar::tab:selected{{margin:0px 0px 0px 0px; padding:4px; border-bottom-width:2px; color:{colors.highlight};
              border-bottom-color:{colors.highlight}; border-style:solid;}}"""
            # QMainWindow::separator Warning: The style sheet has no effect when the QDockWidget is undocked as Qt uses native top level windows when undocked.
            # QLineEdit     {{border-color:{fg}; border-width:1px; border-style:solid;}}
            # QPlainTextEdit{{border-color:{fg}; border-width:1px; border-style:solid;}}
            # QStatusBar::item {{border: 1px solid red;}}
            # QCheckBox::indicator{{border:1px solid {fg};}}
            # QWidget::separator:hover{{background-color:{colors.highlight}; width:4px; height:4px;}}  # causes focus on hover -> other widgets loose focus
            QApplication.setPalette(pal)
            self.mainWindow.setStyleSheet(self.styleSheet)
            plt.style.use('dark_background' if getDarkMode() else 'default')
            plt.rcParams['figure.facecolor'] = colors.bg
            plt.rcParams['axes.facecolor'] = colors.bg
            for plugin in self.plugins:
                if plugin.initializedDock:
                    try:
                        plugin.updateTheme()
                    except Exception:  # noqa: BLE001
                        self.logger.print(f'Error while updating plugin {plugin.name} theme: {traceback.format_exc()}')
            if not (self.loading or self.finalizing) and self.app.splashScreen:
                self.mainWindow.setUpdatesEnabled(True)
                self.app.splashScreen.close()
            self.toggleTitleBarDelayed(update=True)

    def reconnectSource(self, name: str) -> None:
        """Try to reconnect linked channels if applicable.

        This is needed e.g. after renaming, moving, or deleting channels.
        If the channel has been deleted, the reconnection attempt will fail and and the linking channel will indicated that no source has been found.

        :param name: Channel name to reconnect.
        :type name: str
        """
        # keep docstring synchronized with PID.reconnectSource and UCM.reconnectSource
        for staticDisplay in self.DeviceManager.getActiveStaticDisplays():
            staticDisplay.reconnectSource(name)
        for scan in cast('list[Scan]', self.getPluginsByType(PLUGINTYPE.SCAN)):
            scan.reconnectSource(name)
        if hasattr(self, 'PID'):
            self.PID.reconnectSource(name)
        if hasattr(self, 'UCM'):
            self.UCM.reconnectSource(name)

    def connectAllSources(self, update: bool = True) -> None:
        """Connect all available source channels.

        :param update: Indicates that all channels should be (re-)connected. Otherwise will only attempt to connect channels that are not yet connected. Defaults to True
        :type update: bool, optional
        """
        # keep docstring synchronized with PID.connectAllSources and UCM.connectAllSources
        for staticDisplay in self.DeviceManager.getActiveStaticDisplays():
            staticDisplay.connectAllSources()
        for scan in cast('list[Scan]', self.getPluginsByType(PLUGINTYPE.SCAN)):
            scan.connectAllSources()
        if hasattr(self, 'PID'):
            self.PID.connectAllSources(update=update)
        if hasattr(self, 'UCM'):
            self.UCM.connectAllSources(update=update)
            self.UCM.clearPlot()

    def toggleVideoRecorder(self) -> None:
        """Toggles visibility of videoRecorderActions for all plugins."""
        show = self.Settings.showVideoRecorders
        for plugin in self.plugins:
            if plugin.initializedDock and hasattr(plugin, 'videoRecorderAction'):
                plugin.videoRecorderAction.setVisible(show)
                if isinstance(plugin, self.ChannelManager):
                    if plugin.liveDisplayActive() and plugin.liveDisplay:
                        plugin.liveDisplay.videoRecorderAction.setVisible(show)
                    if plugin.staticDisplayActive() and plugin.staticDisplay:
                        plugin.staticDisplay.videoRecorderAction.setVisible(show)
                    if plugin.channelPlotActive() and plugin.channelPlot:
                        plugin.channelPlot.videoRecorderAction.setVisible(show)
                if hasattr(plugin, 'display') and plugin.displayActive() and plugin.display:
                    plugin.display.videoRecorderAction.setVisible(show)


class Logger(QObject):
    """Redirect stderr and stdout to logfile while still sending them to :ref:`sec:console` as well.

    Also shows messages on Status bar.
    Use :meth:`~esibd.plugins.Plugin.print` to send messages to the logger.
    """

    printFromThreadSignal = pyqtSignal(str, str, PRINT)
    maxErrorCount = 10

    def __init__(self, pluginManager: PluginManager) -> None:
        """Initialize a logger.

        :param pluginManager: The central pluginManager allows to send logs to the Console plugin.
        :type pluginManager: PluginManager
        """
        super().__init__()
        self.pluginManager = pluginManager
        self.active = False
        self.testLogFileActive = False
        self.lock = TimeoutLock(lockParent=self)
        self.purgeTo = 10000
        self.purgeLimit = 30000
        self.lastCallTime = None
        self.logFile: 'TextIO | None' = None
        self.testLogFile: 'TextIO | None' = None
        self.logFilePath: 'Path | None' = None
        self.testLogFilePath: 'Path | None' = None
        self.errorCount = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self.purge)
        self.timer.setInterval(3600000)  # every 1 hour
        self.printFromThreadSignal.connect(self.print)
        self.backLog = []  # stores messages to be displayed later if console is not initialized
        self.open()

    def open(self) -> None:
        """Activates logging of Plugin.print statements, stdout, and stderr to the log file."""
        if not self.active:
            self.logFilePath = getValidConfigPath() / f'{PROGRAM_NAME.lower()}.log'
            self.terminalOut = sys.stdout
            self.terminalErr = sys.stderr
            sys.stderr = sys.stdout = self  # redirect all calls to stdout and stderr to the write function of our logger
            self.log = self.logFilePath.open('a', encoding='utf-8-sig')  # pylint: disable=consider-using-with  # keep file open instead of reopening for every new line
            self.active = True
            self.timer.start()

    def openLog(self) -> None:
        """Open the log file in an external program."""
        if self.logFilePath and self.logFilePath.exists():
            openInDefaultApplication(self.logFilePath)
        else:
            self.print('Start logging to create log file.')

    def openTestLogFile(self, tester: str = '') -> None:
        """Open the test log file for writing.

        :param tester: The testing plugin
        :type tester: str
        """
        self.testLogFilePath = self.pluginManager.Settings.getMeasurementFileName('_test.log')
        self.testLogFile = self.testLogFilePath.open('w', encoding='utf-8-sig')
        self.testStartTime = datetime.now()
        self.tester = tester
        self.testLogFileActive = True

    def closeTestLogFile(self) -> None:
        """Add header and close test log file.

        This runs in the main thread!
        """
        assert self.testLogFilePath
        assert self.testLogFile
        self.print(f'Finalizing and opening test log: {self.testLogFilePath.name}')
        self.pluginManager.Explorer.goToCurrentSession()
        self.pluginManager.Explorer.raiseDock(showPlugin=True)
        self.testLogFileActive = False
        self.testLogFile.close()
        seconds = int((datetime.now() - self.testStartTime).total_seconds())
        with self.testLogFilePath.open('r', encoding='utf-8-sig') as original:
            lines = original.readlines()
            original.seek(0)
            content = original.read()
            with self.testLogFilePath.open('w', encoding='utf-8-sig') as header:
                header.write(f'{PROGRAM_NAME} {PROGRAM_VERSION!s} Test Report\n')
                if self.tester:
                    header.write(f'Testing Plugin: {self.tester}\n')
                header.write(f"""Test Mode: {getTestMode()}
Debug Mode: {getDebugMode()}
Log Level: {getLogLevel(asString=True)}
Begin: {self.testStartTime.strftime('%Y-%m-%d %H:%M:%S')}
End: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Total Time: {seconds // 60:02d}:{seconds % 60:02d}
Total Lines: {len(lines)} lines
Messages: {content.count('ℹ️')} ℹ️
Warnings: {content.count('⚠️')} ⚠️
Errors: {content.count('❌')} ❌
Debug Messages: {content.count('🪲')} 🪲
Verbose Messages: {content.count('🆅')} 🆅
Trace Messages: {content.count('🆃')} 🆃
Generated files: {len(list(self.testLogFilePath.parent.glob('*')))}
\n\n""")  # noqa: RUF001
                header.write(content)
        self.pluginManager.Explorer.activeFileFullPath = self.testLogFilePath
        self.pluginManager.Explorer.displayContent()

    def write(self, message: str, fromConsole: bool = False) -> None:
        """Direct messages to terminal, log file, and :ref:`sec:console`.

        Called directly from stdout or stderr or indirectly via :meth:`~esibd.plugins.Plugin.print`.

        :param message: The message.
        :type message: str
        :param fromConsole: Indicates if called from Console while executing code and intercepting stdout.
        :type fromConsole: bool
        """
        if self.active:
            self.writeToLogAndTerminal(message=message)
        if hasattr(self.pluginManager, 'Console') and hasattr(self.pluginManager.Console, 'mainConsole'):
            # handles new lines in system error messages better than Console.repl.write()
            # needs to run in main_thread
            self.pluginManager.Console.write(message.rstrip('\n') if fromConsole else message)
        else:
            self.backLog.append(message)

    def writeToLogAndTerminal(self, message: str) -> None:
        """Write to log files only. Save to use without creating stdout recursion.

        :param message: The message.
        :type message: str
        """
        with self.lock.acquire_timeout(1) as lock_acquired:
            if self.terminalOut:  # after packaging with pyinstaller the program will not be connected to a terminal
                self.terminalOut.write(message)  # write to original stdout
            if lock_acquired:
                self.log.write(message)  # write to log file
                self.log.flush()
            if self.testLogFile and self.testLogFileActive:
                self.testLogFile.write(message)  # write to test log file
                self.testLogFile.flush()

    def purge(self) -> None:
        """Purges the log file to keep it below purgeLimit."""
        # ca. 12 ms, only call once per hour. lock makes sure there is not race conditions with open reference
        assert self.logFilePath
        with self.lock.acquire_timeout(1) as lock_acquired:
            if lock_acquired:
                with self.logFilePath.open('r', encoding=UTF8) as original:
                    lines = original.readlines()
                if len(lines) > self.purgeLimit:
                    with self.logFilePath.open('w', encoding='utf-8-sig') as purged:
                        for line in lines[-self.purgeTo:]:
                            purged.write(line)

    def print(self, message: str, sender: str = f'{PROGRAM_NAME} {PROGRAM_VERSION}', flag: PRINT = PRINT.MESSAGE) -> None:  # only used for program messages  # noqa: C901, PLR0912
        """Augments messages and redirects to log file, statusbar, and console.

        :param message: A short and descriptive message.
        :type message: str
        :param sender: The name of the sending plugin.
        :type sender: str, optional
        :param flag: Signals the status of the message, defaults to :attr:`~esibd.const.PRINT.MESSAGE`
        :type flag: :class:`~esibd.const.PRINT`, optional
        """
        if current_thread() is not main_thread():
            # redirect to main thread if needed to avoid changing GUI from parallel thread.
            self.printFromThreadSignal.emit(message, sender, flag)
            return
        logLevel = cast('int', getLogLevel())
        match flag:
            case PRINT.EXPLORER:
                flagString = '❖ '  # noqa: RUF001
            case PRINT.WARNING:
                flagString = '⚠️'
            case PRINT.ERROR:
                flagString = '❌'
            case PRINT.CONSOLE:
                flagString = '🖳'
                sender = ''  # obvious due to unicode symbol
            case PRINT.DEBUG:
                flagString = '🪲'
                if logLevel < 1:
                    return
            case PRINT.VERBOSE:
                flagString = '🆅'
                if logLevel < 2:  # noqa: PLR2004
                    return
            case PRINT.TRACE:
                flagString = '🆃'
                if logLevel < 3:  # noqa: PLR2004
                    return
            case _:  # PRINT.MESSAGE
                flagString = 'ℹ️'  # noqa: RUF001
        timerString = ''
        if logLevel > 0:
            ms = ((datetime.now() - self.lastCallTime).total_seconds() * 1000) if self.lastCallTime is not None else 0
            timerString = f'🕐 {ms:5.0f} ms '
            self.lastCallTime = datetime.now()
        first_line = message.split('\n', maxsplit=1)[0]
        sender = sender + ':' if sender else ''  # no : needed if no explicit sender
        message_status = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {sender} {first_line}"
        message = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {timerString}{flagString} {sender} {message}"
        if flag == PRINT.CONSOLE:
            self.writeToLogAndTerminal(f'{message}\n')
        elif self.active:
            print(message)  # redirects to write if active  # noqa: T201
        else:
            print(message)  # only to stdout if not active  # noqa: T201
            self.write(f'\n{message}')  # call explicitly
        statusBar = cast('IconStatusBar', self.pluginManager.mainWindow.statusBar())
        if statusBar:
            statusBar.showMessage(message_status)
            statusBar.setFlag(flag)

    def flush(self) -> None:
        """Flushes content to log file."""
        if self.active:
            self.log.flush()

    def close(self) -> None:
        """Disables logging and restores stdout and stderr."""
        if self.active:
            self.log.close()
            self.active = False
            sys.stdout = self.terminalOut  # restore previous
            sys.stderr = self.terminalErr  # restore previous
        self.timer.stop()


class CloseDialog(QDialog):
    """Dialog to confirm closing the program."""

    def __init__(self, title: str = f'Close {PROGRAM_NAME}?', ok: str = 'Close', prompt: str = 'Do you really want to close?') -> None:
        """Initialize CloseDialog.

        :param title: Dialog title, defaults to f'Close {PROGRAM_NAME}?'
        :type title: str, optional
        :param ok: Label of confirmation button, defaults to 'Close'
        :type ok: str, optional
        :param prompt: Dialog prompt, defaults to 'Do you really want to close?'
        :type prompt: str, optional
        """
        super().__init__()

        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        self.setModal(True)
        buttonBox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        okButton = buttonBox.button(QDialogButtonBox.StandardButton.Ok)
        if okButton:
            okButton.setText(ok)
        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)
        self.dialogLayout = QVBoxLayout()
        self.dialogLayout.addWidget(QLabel(prompt))
        self.dialogLayout.addWidget(buttonBox)
        self.setLayout(self.dialogLayout)
        cancelButton = buttonBox.button(QDialogButtonBox.StandardButton.Cancel)
        if cancelButton:
            cancelButton.setFocus()


class DynamicNp:
    """A numpy.array that dynamically increases its size in increments to prevent frequent memory allocation while growing."""

    # based on https://stackoverflow.com/questions/7133885/fastest-way-to-grow-a-numpy-numeric-array
    def __init__(self, initialData: 'np.typing.NDArray[np.float64 | np.float32] | None' = None,
                  max_size: 'int | None' = None, dtype: type = np.float32) -> None:
        """Initialize DynamicNp.

        :param initialData: Initial data, defaults to None
        :type initialData: np.ndarray, optional
        :param max_size: Array will be thinned out if max_size is reached, defaults to None
        :type max_size: int | None, optional
        :param dtype: Use float64 for time data, defaults to np.float32
        :type dtype: type, optional
        """
        self.dtype = dtype
        self.init(initialData, max_size)

    def init(self, initialData: 'np.typing.NDArray[np.float64 | np.float32] | None' = None, max_size: 'int | None' = None) -> None:
        """Initialize DynamicNp. This is also used if data is cropped or padded.

        :param initialData: Initial data, defaults to None
        :type initialData: np.ndarray, optional
        :param max_size: Initial maximal size. Will extend dynamically as needed. Defaults to None
        :type max_size: int, optional
        """
        self.data: np.typing.NDArray[np.float64 | np.float32] = (np.zeros((2000,), dtype=self.dtype)
                                                                                                    if initialData is None or initialData.shape[0] == 0 else initialData)
        self.capacity: int = self.data.shape[0]
        self.size = 0 if initialData is None else initialData.shape[0]
        self.max_size = max_size

    def add(self, x: float, lenT: 'int | None' = None) -> None:
        """Add the new data point and adjust the data array as required.

        :param x: Datapoint or array to be added
        :type x: float
        :param lenT: length of corresponding time array, defaults to None
        :type lenT: int, optional
        """
        if lenT is not None:
            if self.size < lenT:
                # if length does not match length of time, e.g. because channel was enabled later then others or temporarily disabled,
                # pad data with NaN to ensure new data is aligned with time axis
                pad = np.zeros(lenT - self.size)
                pad[:] = np.nan
                self.init(np.hstack([self.get(), pad]), max_size=self.max_size)  # append padding after existing data to account for time without data collection
            if self.size > lenT:
                self.init(self.get()[-lenT:], max_size=self.max_size)  # remove data older than time axis
        if self.size == self.capacity:
            self.capacity *= 4
            newData = np.zeros((self.capacity,), dtype=self.dtype)
            newData[:self.size] = self.data
            self.data = newData
        if self.max_size is not None and self.size >= self.max_size:
            # thin out old data. use only every second value for first half of array to limit RAM use
            a, b = np.array_split(self.get(), 2)  # split array in two halves  # pylint: disable=[unbalanced-tuple-unpacking]  # balance not relevant, as long as it is consistent
            self.size = a[1::2].shape[0] + b.shape[0]  # only thin out older half, take every second item (starting with the second one to avoid keeping the first for every!)
            self.data[:self.size] = np.hstack([a[1::2], b], dtype=self.dtype)  # recombine
            # remove old data as new data is coming in. while this implementation is simpler it limits the length of stored history
        self.data[self.size] = x
        self.size += 1

    def get(self, length: 'int | None' = None, index_min: 'int | None' = None, index_max: 'int | None' = None,
             n: int = 1) -> np.typing.NDArray[np.float64 | np.float32]:  # np.ndarray[Any, np.dtype[np.float32]] | np.ndarray[Any, np.dtype[np.float64]]
        """Return actual values.

        :param length: will return last 'length' values.
        :type length: int
        :param index_min: Index of lower limit.
        :type index_min: int
        :param index_max: Index of upper limit.
        :type index_max: int
        :param n: Will only return every nth value, defaults to 1
        :type n: int, optional
        :return: Values in specified range.
        :rtype: numpy.array
        """
        if length is not None:
            index_min = self.size - length

        # * n cannot be determined internally as it has to match for multiple instances that may have different size (e.g. if one device is initialized later)
        # length: typically length of array to return, relative to end of array. E.g. length relative to a certain point in time.
        # n: use every nth data point
        # simple and works but causes slow update when n is large
        # display update can be jumpy when large n is combined with short time period. This is very rare and can be avoided by slightly higher number of points
        if index_min is not None and index_max is not None:
            return self.data[index_min:index_max][::n]
        if index_min is not None:
            return self.data[index_min - np.remainder(index_min, n):self.size][::n]
        return self.data[:self.size][::n]  # returns everything


class Parameter:  # noqa: PLR0904
    """Parameters are used by Settings and Channels.

    They take care of providing consistent user controls, linking events, input validation,
    context menus, and restoring values.
    Typically they are not initialized directly but via a :meth:`~esibd.core.parameterDict`
    from which Settings and Channels take the relevant information.
    """

    # general keys
    NAME = 'Name'
    UNIT = 'Unit'
    ATTR = 'Attribute'
    ADVANCED = 'Advanced'
    HEADER = 'Header'
    VALUE = 'Value'
    MIN = 'Min'
    MAX = 'Max'
    DEFAULT = 'Default'
    ITEMS = 'Items'
    FIXEDITEMS = 'FixedItems'
    TREE = 'Tree'
    TOOLTIP = 'Tooltip'
    EVENT = 'Event'
    INTERNAL = 'Internal'
    RECORDED = 'Recorded'
    LOGY = 'logY'
    INDICATOR = 'Indicator'
    RESTORE = 'Restore'
    INSTANTUPDATE = 'InstantUpdate'
    PARAMETER_TYPE = 'PARAMETER_TYPE'
    DISPLAYDECIMALS = 'DISPLAYDECIMALS'
    WIDGET = 'WIDGET'

    name: str
    """The parameter name. Only use last element of :attr:`~esibd.core.Parameter.fullName` in case its a path."""
    min: 'float | None'
    """Minimum limit for numerical properties."""
    max: 'float | None'
    """Maximum limit for numerical properties."""
    unit: str = ''
    """Physical unit of the parameter."""
    color: str = ''
    """Color of the parameter. Needed to plot recorded parameters."""
    toolTip: str
    """Tooltip used to describe the parameter."""
    fixedItems: bool
    """Indicates if list of items can be edited by the user or should remain fixed."""
    parameterType: PARAMETERTYPE
    """The parameterType determines which widget is used to represent the parameter in the user interface."""
    advanced: bool
    """If True, parameter will only be visible in advanced mode."""
    header: str
    """Header used for the corresponding column in list of Channels.
    The parameter name is used if not specified.
    Only applies to channel parameters."""
    widget: 'QWidget | None'
    """A custom widget that will be used instead of the automatically provided one."""
    event: 'Callable | None'
    """A function that will be triggered when the parameter value changes."""
    internal: bool
    """Set to True to save parameter value in the registry (using QSetting)
    instead of configuration files. This can help to reduce clutter in
    configuration files and restore essential parameters even if
    configuration files get moved or lost."""
    recorded: bool
    """Indicates that this parameter will be recorded, just like the channel values or backgrounds."""
    display: bool = False
    """Indicates that this parameter will be displayed, just like the channel values or backgrounds."""
    plotCurve: 'PlotCurveItem | PlotDataItem | None'
    """The plotCurve in the corresponding :class:`~esibd.plugins.LiveDisplay`."""
    values: DynamicNp
    """The history of values shown in the :class:`~esibd.plugins.LiveDisplay`.
       Use :meth:`~esibd.core.Channel.getValues` to get a plain numpy.array."""
    attr: str
    """Allows direct access to the parameter. Only applies to channel and settings parameters.

    E.g. The *color* parameter of a channel specifies *attr='color'*
    and can thus be accessed via *channel.color*.

    E.g. The *Session path* parameter in :class:`~esibd.plugins.Settings` specifies
    *attr='sessionPath'* and can thus be accessed via
    *Settings.sessionPath*.

    E.g. The *interval* parameter of a device specifies
    *attr='interval'* and can thus be accessed via *device.interval*.

    E.g. The *notes* parameter of a scan specifies *attr='notes'* and
    can thus be accessed via *scan.notes*."""
    indicator: bool
    """Indicators cannot be edited by the user."""
    instantUpdate: bool
    """By default, events are triggered as soon as the value changes. If set
    to False, certain events will only be triggered if editing is
    finished by the *enter* key or if the widget loses focus."""
    displayDecimals: int
    """Number of decimal places to display if applicable."""
    print: Callable
    """Reference to :meth:`~esibd.plugins.Plugin.print`."""
    fullName: str
    """Will contain path of Setting in HDF5 file if applicable."""
    tree: 'QTreeWidget | None'
    """None, unless the parameter is used for settings."""
    itemWidget: 'QTreeWidgetItem | None'
    """Widget used to display the value of the parameter. None if parameter is part of a channel. Defined if it is part of a Setting."""
    extraEvents: list[Callable]
    """Used to add internal events on top of the user assigned ones."""
    extraContextActions: list['ContextAction']
    """List of extra actions for the parameter context menu."""
    logY: bool
    """Indicates if logarithmic controls and scales should be used."""
    clearingPlotCurve = False
    """Flag to prevent multiple clearing attempts at the same time."""

    class SignalCommunicate(QObject):
        """Bundle pyqtSignals."""

        updateValueSignal = pyqtSignal(object)

    def __init__(self, name: str, parameterParent: 'SettingsManager | Channel', default: 'ParameterType | None' = None,  # noqa: PLR0913, PLR0917
                parameterType: 'PARAMETERTYPE | None' = None,
                column: int = 1, items: str = '', fixedItems: bool = False, widget: 'QWidget | None' = None, internal: bool = False, recorded: bool = False,
                tree: 'QTreeWidget | None' = None, itemWidget: 'QTreeWidgetItem | None' = None, toolTip: str = '', event: 'Callable | None' = None,
                minimum: 'float | None' = None, maximum: 'float | None' = None, unit: str = '', indicator: bool = False, restore: bool = True,
                instantUpdate: bool = True, displayDecimals: int = 2, logY: bool = False) -> None:
        """Initialize a Parameter.

        :param name: Parameter name.
        :type name: str
        :param parameterParent: Parameter parent, defaults to None
        :type parameterParent: SettingsManager | Channel, optional
        :param default: Default value, defaults to None
        :type default: ParameterType | None, optional
        :param parameterType: Widget type, defaults to None
        :type parameterType: PARAMETERTYPE, optional
        :param column: The column of the parameter in the QTreeWidget, defaults to 1
        :type column: int, optional
        :param items: Items for ComboBox, defaults to ''
        :type items: str | None, optional
        :param fixedItems: Determine if items can be edited by user, defaults to False
        :type fixedItems: bool, optional
        :param widget: Custom widget, defaults to None
        :type widget: QWidget, optional
        :param internal: Save to registry instead of file if internal, defaults to False
        :type internal: bool, optional
        :param recorded: Indicates that this parameter will be recorded, just like the channel values or backgrounds, defaults to False
        :type recorded: bool, optional
        :param tree: Tree containing the parameter, defaults to None
        :type tree: QTreeWidget, optional
        :param itemWidget: Item in Tree containing the parameter, defaults to None
        :type itemWidget: QTreeWidgetItem, optional
        :param toolTip: Parameter to, defaults to ''
        :type toolTip: str | None, optional
        :param event: Event called when parameter value changes, defaults to None
        :type event: Callable | None, optional
        :param minimum: Minimal allowed value, defaults to None
        :type minimum: float | None, optional
        :param maximum: Maximal allowed value, defaults to None
        :type maximum: float | None, optional
        :param unit: The physical unit of the parameter, defaults to ''
        :type unit: str, optional
        :param indicator: Indicators cannot be edited by the user, defaults to False
        :type indicator: bool, optional
        :param restore: Determine if the parameter will be saved and restored, defaults to True
        :type restore: bool, optional
        :param instantUpdate: Indicate if the event will be called on every change or only when explicitly confirming with Enter or leaving input area, defaults to True
        :type instantUpdate: bool, optional
        :param displayDecimals: Number of decimal places to display, defaults to 2
        :type displayDecimals: int, optional
        """
        self.parameterParent = parameterParent
        self.parameterType = parameterType or PARAMETERTYPE.LABEL
        self.column = column
        self.print = parameterParent.print
        if '/' not in name and internal:
            # make sure that parameters with simple names are not mixed up between devices.
            # if there is a / this indicates the name is already specific
            # if they are not external the name may be generic but that is fine as it will be loaded from a specific file instead of the shared registry.
            name = f'{self.parameterParent.name}/{name}'
        self.fullName = name
        self.name = Path(name).name
        self.toolTip = toolTip
        self._items: list[str]
        self._items = items.split(',') if items else []
        self.fixedItems = fixedItems
        self.tree = tree
        self.itemWidget = itemWidget
        self.widget = widget
        self.extraEvents = []
        self.extraContextActions = []
        self._valueChanged = False
        self.event = event
        self.internal = internal
        self.recorded = recorded
        self.logY = logY
        self.plotCurve = None
        self.indicator = indicator
        self.restore = restore
        self.instantUpdate = instantUpdate
        self.displayDecimals = displayDecimals
        self.rowHeight: int = int(self.parameterParent.rowHeight if isinstance(self.parameterParent, Channel) and hasattr(self.parameterParent, 'rowHeight')
                          else QLineEdit().sizeHint().height() - 4)
        self.check: 'CheckBox | LedIndicator | None' = None
        self.min = minimum
        self.max = maximum
        self.unit = unit
        self.button: 'PushButton | None' = None
        self.spin: 'LabviewSpinBox | LabviewDoubleSpinBox | LabviewSciSpinBox | None' = None
        self.loading = False
        self._default = None
        if default is not None:
            self.default = default
        if not self.tree:  # if this is part of a QTreeWidget, applyWidget() should be called after this Parameter is added to the tree
            self.applyWidget()  # call after everything else is initialized but before setting value
        self.signalComm = self.SignalCommunicate()
        self.signalComm.updateValueSignal.connect(self.updateValueParallel)

    @property
    def value(self) -> 'ParameterType | None':  # noqa: C901
        """Return value in correct format, based on parameterType."""
        # use widget even for internal settings, should always be synchronized to allow access via both attribute and qSet
        value = None
        if self.parameterType == PARAMETERTYPE.COMBO:
            value = self.combo.currentText()
        if self.parameterType == PARAMETERTYPE.INTCOMBO:
            value = int(self.combo.currentText())
        if self.parameterType == PARAMETERTYPE.FLOATCOMBO:
            value = float(self.combo.currentText())
        elif self.parameterType == PARAMETERTYPE.TEXT:
            value = self.line.text()
        elif self.parameterType in {PARAMETERTYPE.INT, PARAMETERTYPE.FLOAT, PARAMETERTYPE.EXP}:
            if self.spin:
                value = self.spin.value()
        elif self.parameterType == PARAMETERTYPE.BOOL:
            if self.check:
                value = self.check.isChecked()
            elif self.button:
                self.button.isChecked()
        elif self.parameterType == PARAMETERTYPE.COLOR:
            value = cast('QColor', self.colorButton.color(mode='qcolor')).name()
        elif self.parameterType == PARAMETERTYPE.LABEL:
            value = self.label.text()
        elif self.parameterType == PARAMETERTYPE.PATH:
            value = Path(self.label.text())
        return value

    @value.setter
    def value(self, value: 'ParameterType | None') -> None:  # noqa: C901, PLR0912
        if self.internal:
            qSet.setValue(self.fullName, value)
            if self._items is not None:
                qSet.setValue(self.fullName + self.ITEMS, ','.join(self.items))
        if self.parameterType == PARAMETERTYPE.BOOL:
            value = value if isinstance(value, (bool, np.bool_)) else value in {'True', 'true'}  # accepts strings (from ini file or qSet) and bools
            if self.check:
                self.check.setChecked(value)
            elif self.button:
                self.button.setChecked(value)
        elif self.parameterType in {PARAMETERTYPE.INT, PARAMETERTYPE.FLOAT, PARAMETERTYPE.EXP}:
            if isinstance(self.spin, LabviewSpinBox):
                self.spin.setValue(np.nan if isinstance(value, float) and np.isnan(value) else int(float(cast('float | int | str', value))))  # type: ignore  # noqa: PGH003
            elif isinstance(self.spin, (LabviewDoubleSpinBox, LabviewSciSpinBox)):
                self.spin.setValue(float(cast('float | str', value)))
        elif self.parameterType == PARAMETERTYPE.COLOR:
            self.colorButton.setColor(value, finished=True)
        elif self.parameterType in {PARAMETERTYPE.COMBO, PARAMETERTYPE.INTCOMBO, PARAMETERTYPE.FLOATCOMBO}:
            if value is None:
                i = 0
            else:
                i = self.combo.findText(str(value))
                if i == -1 and self.parameterType is PARAMETERTYPE.FLOATCOMBO:
                    i = self.combo.findText(str(int(float(cast('str | float', value)))))  # try to find int version if float version not found. e.g. 1 instead of 1.0
            if i == -1:
                self.print(f'Value {value} not found for {self.fullName}. Defaulting to {self.combo.itemText(0)}.', flag=PRINT.WARNING)
                self.combo.setCurrentIndex(0)
            else:
                self.combo.setCurrentIndex(i)
        elif self.parameterType == PARAMETERTYPE.TEXT:
            self.line.setText(str(value))  # input may be of type Path from pathlib -> needs to be converted to str for display in lineEdit
            if self.indicator and self.tree:
                self.line.setToolTip(str(value))
                self.line.setCursorPosition(0)
                self.tree.scheduleDelayedItemsLayout()  # Otherwise only called if value changed by user
        elif self.parameterType in {PARAMETERTYPE.LABEL, PARAMETERTYPE.PATH}:
            self.label.setText(str(value))
            self.label.setToolTip(str(value))
            if not self.indicator:
                self.changedEvent()  # emit here as it is not emitted by the label

    def setValueWithoutEvents(self, value: 'ParameterType | None') -> None:
        """Set the parameter value without triggering valueChanged Events.

        In most cases, use parameter.value = value instead.

        :param value: The new value.
        :type value: ParameterType | None
        """
        self.value = value
        self._valueChanged = False

    def getValues(self, *, length: 'int | None' = None, index_min: 'int | None' = None,
                  index_max: 'int | None' = None, n: int = 1, **kwargs) -> np.typing.NDArray[np.float64 | np.float32]:  # pylint: disable = unused-argument  # use consistent arguments for all versions of getValues  # noqa: ARG002
        """Return plain Numpy array of values.

        Signature matching :meth:`~esibd.core.Channel.getValues`

        :param length: will return last 'length' values.
        :type length: int
        :param index_min: Index of lower limit.
        :type index_min: int
        :param index_max: Index of upper limit.
        :type index_max: int
        :param n: Will only return every nth value, defaults to 1
        :type n: int, optional
        :param kwargs: Used to match signature with :meth:`~esibd.core.Channel.getValues`. Note that the subtractBackground keyword will be ignored.
        :type kwargs:
        :return: The array of values.
        :rtype: np.ndarray[Any, np.dtype[np.float32]]
        """
        return self.values.get(length=length, index_min=index_min, index_max=index_max, n=n) if self.recorded else None  # type: ignore  # noqa: PGH003

    def updateDisplay(self) -> None:
        """Toggle display of parameter."""
        self.display = not self.display
        self.clearPlotCurve()
        if isinstance(self.parameterParent, Channel):
            self.parameterParent.updateDisplay()

    @property
    def legendName(self) -> str:
        """Return full Name including channel and parameter name if applicable."""
        return f'{self.parameterParent.name}.{self.name}' if isinstance(self.parameterParent, Channel) else self.name

    @property
    def linewidth(self) -> int:
        """Return linewidth of the associated Channel if applicable."""
        return self.parameterParent.linewidth if isinstance(self.parameterParent, Channel) else 4

    @property
    def enabled(self) -> bool:
        """Return enabled of the associated Channel if applicable."""
        return self.parameterParent.enabled if isinstance(self.parameterParent, Channel) else False

    @property
    def real(self) -> bool:
        """Return real of the associated Channel if applicable."""
        return self.parameterParent.real if isinstance(self.parameterParent, Channel) else False

    @property
    def smooth(self) -> int:
        """Return smooth of the associated Channel if applicable."""
        return self.parameterParent.smooth if isinstance(self.parameterParent, Channel) else 0

    @property
    def displayGroup(self) -> str:
        """Return displayGroup of the associated Channel if applicable."""
        return self.parameterParent.displayGroup if isinstance(self.parameterParent, Channel) else '1'

    @property
    def time(self) -> DynamicNp | None:
        """Return time of the associated Channel if applicable."""
        return self.parameterParent.time if isinstance(self.parameterParent, Channel) else None

    def getDevice(self) -> 'ChannelManager | Device | Scan':
        """Return the device of the associated Channel if applicable."""
        return self.parameterParent.getDevice() if isinstance(self.parameterParent, Channel) else None  # type: ignore  # noqa: PGH003

    def getQtLineStyle(self) -> Qt.PenStyle:
        """Return the linestyle of the associated Channel if applicable."""
        return self.parameterParent.getQtLineStyle() if isinstance(self.parameterParent, Channel) else Qt.PenStyle.SolidLine

    def clearPlotCurve(self) -> None:
        """Clear the plotCurve."""
        if self.plotCurve and not self.clearingPlotCurve:
            self.clearingPlotCurve = True
            device = self.getDevice()
            self.plotCurve.curveParent.removeItem(self.plotCurve)  # plotWidget still tries to access this even if deleted -> need to explicitly remove!
            if isinstance(self.plotCurve.curveParent, ViewBox):
                self.plotCurve.curveLegend.removeItem(self.plotCurve)
            self.plotCurve.clear()
            self.plotCurve.deleteLater()
            self.plotCurve = None
            if isinstance(device, self.parameterParent.pluginManager.Device) and device.liveDisplay:
                device.liveDisplay.updateLegend = True
            self.clearingPlotCurve = False

    def convertDataDisplay(self, data: np.ndarray) -> np.ndarray:
        """Overwrite to apply scaling and offsets to data before it is displayed. Use, e.g., to convert to another unit.

        This should only affect display in :class:`LiveDisplays<esibd.plugins.LiveDisplay>`.

        :param data: Original data.
        :type data: np.ndarray
        :return: Scaled data.
        :rtype: np.ndarray
        """
        return data

    def getDisplayUnit(self) -> str:
        """Return the parameter unit."""
        return self.unit

    def updateValueParallel(self, value: ParameterType) -> None:  # used to update from external threads
        """Update the value (thread safe).

        :param value: new value
        :type value: ParameterType
        """
        self.value = value  # pylint: disable=[attribute-defined-outside-init]  # attribute defined by makeWrapper

    @property
    def default(self) -> 'ParameterType | None':
        """The default value."""
        return self._default

    @default.setter
    def default(self, default: ParameterType) -> None:
        # casting does not change anything if the value is already supplied in the right type, but will convert strings to correct value if needed
        if self.parameterType == PARAMETERTYPE.BOOL:
            self._default = default
        elif self.parameterType == PARAMETERTYPE.INT:
            self._default = int(cast('float | str', default))
        elif self.parameterType in {PARAMETERTYPE.FLOAT, PARAMETERTYPE.EXP}:
            self._default = float(cast('float | str', default))
        else:
            self._default = str(default)

    @property
    def items(self) -> list[str]:
        """List of items for parameters with a combobox."""
        if self.parameterType in {PARAMETERTYPE.COMBO, PARAMETERTYPE.INTCOMBO, PARAMETERTYPE.FLOATCOMBO}:
            return [self.combo.itemText(i) for i in range(self.combo.count())]
        return []

    def settingEvent(self) -> None:
        """Extend to manage changes to settings."""

    def changedEvent(self) -> None:
        """Event that is triggered when the value of the Parameter changes.

        Depending on the type and configuration of Parameter specific additional events will be triggered.
        """
        if not (self.loading or (self.parameterParent.loading)):
            self.settingEvent()  # always save changes even when event is not triggered
            if not self.instantUpdate and self.parameterType in {PARAMETERTYPE.INT, PARAMETERTYPE.FLOAT, PARAMETERTYPE.EXP}:
                if self._valueChanged:
                    self._valueChanged = False  # reset and continue event loop
                else:
                    return  # ignore editingFinished if content has not changed
            # ! Settings event has to be triggered before main event to make sure internal parameters are updated and available right away
            # ! if you have 100 channels which update at 10 Hz, changedEvent can be called 1000 times per second.
            # ! adding a print statement to the terminal, console plugin, and statusbar at that rate might make the application unresponsive.
            # ! only uncomment for specific tests.
            # self.print(f'ChangedEvent for {self.fullName}.', flag=PRINT.VERBOSE)  # noqa: ERA001
            for event in self.extraEvents:
                # extraEvents should be triggered even if value is NaN, e.g. to update relay channels to NaN
                if event:
                    event()
            if self.event:
                if isinstance(self.value, float) and np.isnan(self.value):
                    # do not trigger events after changing value to NaN
                    self._valueChanged = False
                    return
                self.event()

    def applyChangedEvent(self) -> None:  # noqa: C901
        """Assign events to the corresponding controls.

        Even indicators should be able to trigger events, e.g. to update dependent channels.
        """
        if self.parameterType in {PARAMETERTYPE.COMBO, PARAMETERTYPE.INTCOMBO, PARAMETERTYPE.FLOATCOMBO}:
            self.safeConnect(self.combo, self.combo.currentIndexChanged, self.changedEvent)
        elif self.parameterType == PARAMETERTYPE.TEXT:
            self.safeConnect(self.line, self.line.userEditingFinished, self.changedEvent)
        elif self.parameterType in {PARAMETERTYPE.INT, PARAMETERTYPE.FLOAT, PARAMETERTYPE.EXP}:
            if self.spin:
                if self.instantUpdate:
                    # by default trigger events on every change, not matter if through user interface or software
                    self.safeConnect(self.spin, self.spin.valueChanged, self.changedEvent)
                else:
                    self.safeConnect(self.spin, self.spin.valueChanged, self.setValueChanged)
                    self.safeConnect(self.spin, self.spin.editingFinished, self.changedEvent)
        elif self.parameterType == PARAMETERTYPE.BOOL:
            if isinstance(self.check, QCheckBox):
                self.safeConnect(self.check, self.check.stateChanged, self.changedEvent)
            elif isinstance(self.check, QAction):
                self.safeConnect(self.check, self.check.toggled, self.changedEvent)
            elif isinstance(self.check, (QToolButton, QPushButton)):
                self.safeConnect(self.check, self.check.clicked, self.changedEvent)
        elif self.parameterType == PARAMETERTYPE.COLOR:
            self.safeConnect(self.colorButton, self.colorButton.sigColorChanged, self.changedEvent)
        elif self.parameterType in {PARAMETERTYPE.LABEL, PARAMETERTYPE.PATH}:
            pass  # self.label.changeEvent.connect(self.changedEvent)  # no change events for labels

    def safeConnect(self, control: QWidget, signal: pyqtBoundSignal, event: Callable) -> None:
        """Make sure there is never more than one event assigned to the signal.

        :param control: The control emitting the signal.
        :type control: QWidget
        :param signal: The signal.
        :type signal: pyqtSignal
        :param event: The event to be connected to the signal.
        :type event: Callable
        """
        if control.receivers(signal) > 0:
            signal.disconnect()
        if event:
            signal.connect(event)

    def setValueChanged(self) -> None:
        """Indicate that value actually changed.

        The changedEvent might be ignored e.g. if there was an input but the value did not change.
        """
        self._valueChanged = True

    def setToDefault(self) -> None:
        """Set Parameter value to its default."""
        if self.default:
            if self.parameterType in {PARAMETERTYPE.COMBO, PARAMETERTYPE.INTCOMBO, PARAMETERTYPE.FLOATCOMBO}:
                i = self.combo.findText(str(self.default))
                if i == -1:  # add default entry in case it has been deleted
                    self.print(f'Adding Default value {self.default} for {self.fullName}.', flag=PRINT.WARNING)
                    self.addItem(str(cast('str | float', self.default)))
            self.value = self.default

    def makeDefault(self) -> None:
        """Make current value the default value."""
        if self.value is not None:
            self.default = self.value

    def applyWidget(self) -> None:  # noqa: C901, PLR0912, PLR0915
        """Create UI widget depending on :attr:`~esibd.core.Parameter.parameterType`.

        Links dedicated :attr:`~esibd.core.Parameter.widget` if provided.
        """
        if self.parameterType in {PARAMETERTYPE.COMBO, PARAMETERTYPE.INTCOMBO, PARAMETERTYPE.FLOATCOMBO}:
            self.combo = CompactComboBox() if self.widget is None else cast('CompactComboBox', self.widget)
            self.combo.setMaximumWidth(100)
            if self.widget is not None:  # potentially reuse widget with old data!
                self.combo.clear()
            for item in [item.strip(' ') for item in self._items]:
                self.combo.insertItem(self.combo.count(), item)
        elif self.parameterType == PARAMETERTYPE.TEXT:
            self.line = LineEdit(parentParameter=self, tree=self.tree) if self.widget is None else cast('LineEdit', self.widget)
            self.line.setFrame(False)
            self.setEnabled(not self.indicator)
        elif self.parameterType in {PARAMETERTYPE.INT, PARAMETERTYPE.FLOAT, PARAMETERTYPE.EXP}:
            if self.widget is None:
                if self.parameterType == PARAMETERTYPE.INT:
                    self.spin = LabviewSpinBox(indicator=self.indicator)
                elif self.parameterType == PARAMETERTYPE.FLOAT:
                    self.spin = LabviewDoubleSpinBox(indicator=self.indicator, displayDecimals=self.displayDecimals)
                else:  # PARAMETERTYPE.EXP
                    self.spin = LabviewSciSpinBox(indicator=self.indicator, displayDecimals=self.displayDecimals)
                lineEdit = self.spin.lineEdit()
                if lineEdit:
                    lineEdit.setObjectName(self.fullName)
            else:
                self.spin = cast('LabviewSpinBox', self.widget)
        elif self.parameterType == PARAMETERTYPE.BOOL:
            if self.widget is None:
                if self.indicator:
                    self.check = LedIndicator()
                    self.check.setMinimumSize(self.rowHeight - 10, self.rowHeight - 10)
                    self.check.setMaximumSize(self.rowHeight - 10, self.rowHeight - 10)
                    self.setEnabled(True)  # left clicks are suppressed, right clicks open context menu
                else:
                    self.check = CheckBox()
                    self.setEnabled(not self.indicator)
            else:
                self.check = cast('CheckBox', self.widget)
                self.setEnabled(not self.indicator)
        elif self.parameterType == PARAMETERTYPE.COLOR:
            if self.widget is None:
                self.colorButton = ColorButton()
                self.colorButton.padding = (2, 2, -3, -3)
            else:
                self.colorButton = cast('ColorButton', self.widget)
        elif self.parameterType in {PARAMETERTYPE.LABEL, PARAMETERTYPE.PATH}:
            self.label = Label() if self.widget is None else cast('Label', self.widget)
        if self.spin:  # apply limits  # no limits by default to avoid unpredictable behavior.
            if self.min is not None:
                self.spin.setMinimum(int(self.min)) if isinstance(self.spin, LabviewSpinBox) else self.spin.setMinimum(self.min)
            if self.max is not None:
                self.spin.setMaximum(int(self.max)) if isinstance(self.spin, LabviewSpinBox) else self.spin.setMaximum(self.max)
        if self.tree:
            if not self.itemWidget:
                if self.widget is None and isinstance(self, Setting):  # widget has already been provided and added to the GUI independently
                    self.tree.setItemWidget(self, 1, self.getWidget())
            else:
                widget = self.getWidget()
                if widget:
                    self.tree.setItemWidget(self.itemWidget, self.column, self.containerize(widget))  # container required to hide widgets reliable
        self.applyChangedEvent()

        widget = self.getWidget()
        if widget:
            widget.setToolTip(self.toolTip)
            widget.setMinimumHeight(self.rowHeight)  # always keep entire row at consistent height
            widget.setMaximumHeight(self.rowHeight)
            widget.setObjectName(self.fullName)
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(self.initContextMenu)

    def containerize(self, widget: 'ParameterWidgetType') -> QWidget:
        """Add a container around the widget that ensures correct handling of visibility and color changes.

        :param widget: The widget to be added to a container.
        :type widget: QWidget
        :return: The container containing the widget.
        :rtype: QWidget
        """
        # just hiding widget using setVisible(False) is not reliable due to bug https://bugreports.qt.io/browse/QTBUG-13522
        # use a wrapping container as a workaround https://stackoverflow.com/questions/71707347/how-to-keep-qwidgets-in-qtreewidget-hidden-during-resize?noredirect=1#comment126731693_71707347
        container = QWidget()
        containerLayout = QGridLayout(container)
        containerLayout.setContentsMargins(0, 0, 0, 0)
        widget.setSizePolicy(QSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.MinimumExpanding))
        widget.container = container  # used to have proper background color independent of widget visibility
        containerLayout.addWidget(widget)
        return container

    def setHeight(self, height: 'float | None' = None) -> None:  # noqa: C901
        """Set the height of the Parameter while accounting for different types of Parameter widgets.

        :param height: Target height, defaults to None
        :type height: float, optional
        """
        if self.parameterType not in {PARAMETERTYPE.COMBO, PARAMETERTYPE.INTCOMBO, PARAMETERTYPE.BOOL, PARAMETERTYPE.COLOR, PARAMETERTYPE.FLOATCOMBO,
                                   PARAMETERTYPE.TEXT, PARAMETERTYPE.INT, PARAMETERTYPE.FLOAT, PARAMETERTYPE.EXP, PARAMETERTYPE.LABEL, PARAMETERTYPE.PATH}:
            return
        if height is None:
            height = self.rowHeight
        scaling = height / (QLineEdit().sizeHint().height() - 4)
        self.rowHeight = int(height)
        widget = self.getWidget()
        if widget:
            widget.setMinimumHeight(self.rowHeight)
            widget.setMaximumHeight(self.rowHeight)
            font = widget.font()
            font.setPointSize(int(height / 2))
            if self.parameterType in {PARAMETERTYPE.COMBO, PARAMETERTYPE.INTCOMBO, PARAMETERTYPE.FLOATCOMBO}:
                self.combo.setFont(font)
            elif self.parameterType == PARAMETERTYPE.TEXT:
                self.line.setFont(font)
            elif self.parameterType in {PARAMETERTYPE.INT, PARAMETERTYPE.FLOAT, PARAMETERTYPE.EXP}:
                if self.spin:
                    self.spin.setMinimumWidth(int(scaling * 50) + 10)  # empirical fixed width
                    lineEdit = self.spin.lineEdit()
                    if lineEdit:
                        lineEdit.setFont(font)
            elif self.parameterType == PARAMETERTYPE.BOOL:
                if isinstance(self.check, QCheckBox):
                    checkBoxHeight = min(self.rowHeight - 4, QCheckBox().sizeHint().height() - 2)
                    self.check.checkBoxHeight = checkBoxHeight  # remember for updateColor
                    self.check.setStyleSheet(f'QCheckBox::indicator {{ width: {checkBoxHeight}; height: {checkBoxHeight};}}')
                elif isinstance(self.check, (QToolButton, QPushButton)):
                    iconHeight = min(self.rowHeight, QCheckBox().sizeHint().height())
                    self.check.setFont(font)
                    self.check.setIconSize(QSize(iconHeight, iconHeight))
            elif self.parameterType in {PARAMETERTYPE.LABEL, PARAMETERTYPE.PATH}:
                self.label.setFont(font)

    def getWidget(self) -> 'ParameterWidgetType | None':
        """Return the widget used to display the Parameter value in the user interface."""
        widget = None
        if self.parameterType in {PARAMETERTYPE.COMBO, PARAMETERTYPE.INTCOMBO, PARAMETERTYPE.FLOATCOMBO}:
            widget = self.combo
        elif self.parameterType == PARAMETERTYPE.TEXT:
            widget = self.line
        elif self.parameterType in {PARAMETERTYPE.INT, PARAMETERTYPE.FLOAT, PARAMETERTYPE.EXP}:
            widget = self.spin
        elif self.parameterType == PARAMETERTYPE.BOOL:
            widget = self.check or self.button
        elif self.parameterType == PARAMETERTYPE.COLOR:
            widget = self.colorButton
        elif self.parameterType in {PARAMETERTYPE.LABEL, PARAMETERTYPE.PATH}:
            widget = self.label
        return widget

    def setEnabled(self, enabled: bool) -> None:
        """Control user access using setEnabled or setReadOnly depending on the widget type.

        :param enabled: The requested enabled state.
        :type enabled: bool
        """
        widget = self.getWidget()
        if widget:
            if isinstance(widget, (QPushButton | QToolButton | CheckBox | QLabel | CompactComboBox | LedIndicator)):
                widget.setEnabled(enabled)
            else:
                widget.setReadOnly(not enabled)

    def setVisible(self, visible: bool) -> None:
        """Set parameter visibility.

        :param visible: Visible or hidden.
        :type visible: bool
        """
        widget = self.getWidget()
        if widget:
            widget.setVisible(visible)

    def addItem(self, value: str) -> None:
        """Add an item to a combobox and selects it.

        :param value: The new value to be added.
        :type value: str
        """
        # should only be called for WIDGETCOMBO settings
        if self.validateComboInput(value) and self.combo.findText(str(value)) == -1:  # only add item if not already in list
            self.combo.insertItem(self.combo.count(), str(value))
            self.value = value

    def removeCurrentItem(self) -> None:
        """Remove currently selected item from combobox."""
        if len(self.items) > 1:
            self.combo.removeItem(self.combo.currentIndex())
        else:
            self.print('List cannot be empty.', flag=PRINT.WARNING)

    def editCurrentItem(self, value: str) -> None:
        """Edits currently selected item of a combobox.

        :param value: The new value to be validated.
        :type value: str
        """
        if self.validateComboInput(value):
            self.combo.setItemText(self.combo.currentIndex(), str(value))
            self.changedEvent()  # is not triggered by setItemText

    def validateComboInput(self, value: str) -> bool:  # noqa: PLR0911
        """Validate input for comboboxes.

        :param value: The new value to be validated.
        :type value: str
        :return: True if valid.
        :rtype: bool
        """
        if self.parameterType == PARAMETERTYPE.COMBO:
            return True
        if self.parameterType == PARAMETERTYPE.INTCOMBO:
            try:
                int(value)
            except ValueError:
                self.print(f'{value} is not an integer!', flag=PRINT.ERROR)
                return False
            else:
                if self.min is not None and self.max is not None:
                    self.print(f'{value} is not in allowed range from {self.min} to {self.max}!', flag=PRINT.ERROR)
                    return self.min < int(value) < self.max
                return True
        elif self.parameterType == PARAMETERTYPE.FLOATCOMBO:
            try:
                float(value)
            except ValueError:
                self.print(f'{value} is not a float!', flag=PRINT.ERROR)
                return False
            else:
                if self.min is not None and self.max is not None:
                    self.print(f'{value} is not in allowed range from {self.min} to {self.max}!', flag=PRINT.ERROR)
                    return self.min < float(value) < self.max
                return True
        return False

    def equals(self, value: ParameterType) -> bool:
        """Return True if a representation of value matches the value of the Parameter.

        :param value: The value to compare against.
        :type value: ParameterType
        :return: True if value equals self.value.
        :rtype: bool
        """
        equals: bool = False
        if self.parameterType == PARAMETERTYPE.BOOL:
            equals = self.value == value if isinstance(value, (bool, np.bool_)) else self.value == (value in {'True', 'true'})  # accepts strings (from ini file or qSet) and bools
        elif self.parameterType in {PARAMETERTYPE.INT, PARAMETERTYPE.INTCOMBO}:
            equals = self.value == int(cast('float', value))
        elif self.parameterType in {PARAMETERTYPE.FLOAT, PARAMETERTYPE.FLOATCOMBO}:
            equals = f'{self.value:.{self.displayDecimals}f}' == f"{float(cast('float', value)):.{self.displayDecimals}f}"
        elif self.parameterType == PARAMETERTYPE.EXP:
            equals = f'{self.value:.{self.displayDecimals}e}' == f"{float(cast('float', value)):.{self.displayDecimals}e}"
        elif self.parameterType == PARAMETERTYPE.COLOR:
            equals = self.value == value.name() if isinstance(value, QColor) else self.value == value
        elif self.parameterType in {PARAMETERTYPE.TEXT, PARAMETERTYPE.LABEL, PARAMETERTYPE.PATH}:
            equals = self.value == str(value)  # input may be of type Path from pathlib -> needs to be converted to str for display in lineEdit
        else:
            equals = self.value == value
        return equals

    def formatValue(self, value: 'ParameterType | None' = None, escapePercent: bool = False) -> str:
        """Format value as a string, depending on Parameter type.

        :param value: A value to be formatted using the Parameter formatting, defaults to None and uses self.value
        :type value: ParameterType, optional
        :param escapePercent: % has to be escaped by %% for use with configparser.
        :type escapePercent: bool
        :return: Formatted value as text.
        :rtype: str
        """
        value = value if value is not None else self.value
        if value is None:
            return str(value)
        if self.parameterType in {PARAMETERTYPE.INT, PARAMETERTYPE.INTCOMBO}:
            return f'{int(cast("str | float", value))}'
        if self.parameterType in {PARAMETERTYPE.FLOAT, PARAMETERTYPE.FLOATCOMBO, PARAMETERTYPE.EXP}:
            if self.parameterType == PARAMETERTYPE.EXP:
                return f'{float(cast("float", value)):.{self.displayDecimals}e}'
            return f'{float(cast("str | float", value)):.{self.displayDecimals}f}'
        return str(value).replace('%', '%%') if escapePercent else str(value)

    def initContextMenu(self, pos: QPoint) -> None:
        """Initialize the context menu of the parent at the location of the Parameter.

        :param pos: The position of the context menu.
        :type pos: QPoint
        """
        widget = self.getWidget()
        if widget:
            self.parameterParent.initSettingsContextMenuBase(self, widget.mapToGlobal(pos))


def parameterDict(name: str = '', value: 'ParameterType | None' = None, default: 'ParameterType | None' = None, minimum: 'float | None' = None, maximum: 'float | None' = None,  # noqa: PLR0913, PLR0917
                unit: str = '', toolTip: str = '', items: 'str | None' = None,
                fixedItems: bool = False, tree: 'QTreeWidget | None' = None, parameterType: 'PARAMETERTYPE | None' = None, advanced: bool = False, header: str = '',
                widget: 'QWidget | None' = None, event: 'Callable | None' = None, internal: bool = False, recorded: bool = False,
                attr: str = '', indicator: bool = False, restore: bool = True, logY: bool = False,
                instantUpdate: bool = True, displayDecimals: int = 2) -> dict[str, ParameterType | QTreeWidget | PARAMETERTYPE | QWidget | Callable | None]:
    """Provide default values for all properties of a Parameter.

    :param name: The Parameter name. Only use last element of :attr:`~esibd.core.Parameter.fullName` in case its a path, defaults to ''
    :type name: str, optional
    :param value: The value of the Parameter in any supported type, defaults to None
    :type value: ParameterType, optional
    :param default: The default value of the Parameter in any supported type, defaults to None
    :type default: ParameterType, optional
    :param minimum: Minimum limit for numerical properties, defaults to None
    :type minimum: float, optional
    :param maximum: Maximum limit for numerical properties., defaults to None
    :type maximum: float, optional
    :param unit: The physical unit of the parameter., defaults to ''
    :type unit: str, optional
    :param toolTip: ToolTip used to describe the Parameter, defaults to ''
    :type toolTip: str, optional
    :param items: Coma separated list of options for parameters with a comboBox, defaults to None
    :type items: str, optional
    :param fixedItems: Indicates if list of items can be edited by the user or should remain fixed., defaults to False
    :type fixedItems: bool, optional
    :param tree: None, unless the Parameter is used for settings., defaults to None
    :type tree: QTreeWidget, optional
    :param parameterType: The parameterType determines which widget is used to represent the Parameter in the user interface, defaults to None
    :type parameterType: esibd.core.PARAMETERTYPE, optional
    :param advanced: If True, Parameter will only be visible in advanced mode, defaults to False
    :type advanced: bool, optional
    :param header: Header used for the corresponding column in list of Channels. The Parameter name is used if not specified. Only applies to Channel Parameters. Defaults to ''
    :type header: str, optional
    :param widget: A custom widget that will be used instead of the automatically provided one., defaults to None
    :type widget: QWidget, optional
    :param event: A function that will be triggered when the Parameter value changes, defaults to None
    :type event: Callable, optional
    :param internal: Set to True to save Parameter value in the registry (using QSetting) instead of configuration files.
        This can help to reduce clutter in configuration files and restore essential Parameters even if configuration files get moved or lost. Defaults to False
    :type internal: bool, optional
    :param recorded: Indicates that this parameter will be recorded, just like the channel values or backgrounds, defaults to False
    :type recorded: bool, optional
    :param attr: Allows direct access to the Parameter. Only applies to Channel and Settings Parameters. Defaults to ''
    :type attr: str, optional
    :param indicator: Indicators cannot be edited by the user, defaults to False
    :type indicator: bool, optional
    :param restore: Indicates if the parameter will be restored, defaults to True. Note that temp parameters of channels will never be restored.
    :type restore: bool, optional
    :param logY: Indicates if logarithmic controls and scales should be used.
    :type logY: bool, optional
    :param instantUpdate: By default, events are triggered as soon as the value changes.
        If set to False, certain events will only be triggered if editing is finished by the *enter* key or if the widget loses focus. Defaults to True
    :type instantUpdate: bool, optional
    :param displayDecimals: Number of decimal places to display if applicable, defaults to 2
    :type displayDecimals: int, optional
    :return: Dictionary containing all Parameter information.
    :rtype: dict[str, ParameterType]
    """
    return {Parameter.NAME: name, Parameter.VALUE: value, Parameter.DEFAULT: default if default is not None else value, Parameter.MIN: minimum, Parameter.MAX: maximum,
            Parameter.UNIT: unit, Parameter.ADVANCED: advanced, Parameter.HEADER: header, Parameter.TOOLTIP: toolTip, Parameter.ITEMS: items, Parameter.FIXEDITEMS: fixedItems,
            Parameter.TREE: tree, Parameter.PARAMETER_TYPE: parameterType, Parameter.WIDGET: widget, Parameter.EVENT: event, Parameter.INTERNAL: internal,
            Parameter.RECORDED: recorded, Parameter.ATTR: attr, Parameter.INDICATOR: indicator, Parameter.RESTORE: restore, Parameter.INSTANTUPDATE: instantUpdate,
            Parameter.DISPLAYDECIMALS: displayDecimals, Parameter.LOGY: logY}


class Setting(QTreeWidgetItem, Parameter):
    """Parameter to be used as general settings with dedicated UI controls instead of being embedded in a channel."""

    toolTip: str  # replaces function of same name of QTreeWidgetItem

    def __init__(self, value: 'ParameterType | None' = None, parentItem: 'QTreeWidgetItem | None' = None, advanced: bool = False, **kwargs) -> None:
        """Initialize Setting.

        :param value: Setting value, defaults to None
        :type value: ParameterType | None, optional
        :param parentItem: Setting parent in tree, defaults to None
        :type parentItem: QTreeWidgetItem | None, optional
        :param advanced: Only visible in advanced mode if True, defaults to False
        :type advanced: bool, optional
        """
        # use keyword arguments rather than positional to avoid issues with multiple inheritance
        # https://stackoverflow.com/questions/9575409/calling-parent-class-init-with-multiple-inheritance-whats-the-right-way
        super().__init__(**kwargs)
        self.advanced = advanced  # only display this Parameter in advanced mode, implementation differs from Parameter
        if self.tree and parentItem:  # some settings may be attached to dedicated controls
            self.parentItem = parentItem
            self.parentItem.addChild(self)  # has to be added to parent before widgets can be added!
            self.setData(0, Qt.ItemDataRole.DisplayRole, self.name)
            self.setToolTip(0, self.toolTip)
            self.applyWidget()
        self.loading = True
        if self.internal:
            if self.widget is not None:
                if self._items is not None:
                    itemsStr = qSet.value(self.fullName + self.ITEMS)
                    self._items = itemsStr.split(',') if itemsStr else self._items
                self.applyWidget()
            self.value = qSet.value(self.fullName, self.default) if self.restore else self.default  # trigger assignment to widget
        else:
            self.value = value if self.restore else self.default  # use setter to distinguish data types based on other fields
        self.loading = False

    def setWidget(self, widget: QWidget) -> None:
        """Allow to change to custom widget after initialization. E.g. to move a Setting to a more logical position outside the Settings tree.

        :param widget: An appropriate widget to display the Setting value.
        :type widget: QWidget
        """
        initialValue = self.value
        self.widget = widget
        self.applyWidget()  # will overwrite ToolTip -> restore if value specific
        self.loading = True  # only change widget, do not trigger event when value is restored
        self.value = initialValue
        self.loading = False
        if self.parentItem:
            self.parentItem.removeChild(self)  # remove old entry from tree

    def resetWidget(self) -> None:
        """Return widget back to the Settings tree."""
        initialValue = self.value
        self.widget = None
        if self.parentItem:
            self.parentItem.addChild(self)
        self.applyWidget()
        self.loading = True  # only change widget, do not trigger event when value is restored
        self.value = initialValue
        self.loading = False

    def settingEvent(self) -> None:
        """Execute internal validation based on Setting type.

        Saves parameters to file or qSet to make sure they can be restored even after a crash.
        Finally executes Setting specific event if applicable.
        """
        if not self.indicator:  # Setting indicators should never need to trigger events
            if self.parameterType == PARAMETERTYPE.PATH:
                path, changed = validatePath(cast('Path | None', self.value), cast('Path', self.default))
                if changed:
                    self.value = path
            if self.internal:
                qSet.setValue(self.fullName, self.value)
                if self._items is not None:
                    qSet.setValue(self.fullName + self.ITEMS, ','.join(self.items))
            elif not isinstance(self.parameterParent, Channel):  # save non internal parameters to file
                self.parameterParent.saveSettings(useDefaultFile=True)


class RelayChannel:
    """Channel that wraps another sourceChannel. Used to display and access some elements of sourceChannel in other parts of the program."""

    sourceChannel: 'Channel | None' = None
    sourceParameter: 'Parameter | None' = None
    recordingData: 'np.ndarray | DynamicNp | None' = None
    recordingBackground: 'np.ndarray | DynamicNp | None' = None
    channelParent: 'Device'
    unit: 'str'

    def getRecordingData(self) -> 'np.ndarray | None':
        """SourceChannel.getRecordingData() if available. Default provided."""
        return self.recordingData.get() if isinstance(self.recordingData, DynamicNp) else self.recordingData

    def getDevice(self) -> 'ChannelManager | Device | Scan':
        """SourceChannel.getDevice() if available. Default provided."""
        return self.sourceChannel.getDevice() if self.sourceChannel else self.channelParent

    def subtractBackgroundActive(self) -> bool:
        """SourceChannel.subtractBackgroundActive() if available. Default provided."""
        return cast('Device', self.sourceChannel.getDevice()).subtractBackgroundActive() if self.sourceChannel else False

    @property
    def recording(self) -> bool:
        """SourceChannel.recording if available. Default provided."""
        return self.sourceChannel.getDevice().recording if self.sourceChannel else False

    def getValues(self, *, length: 'int | None' = None, index_min: 'int | None' = None,
                   index_max: 'int | None' = None, n: int = 1, subtractBackground: bool = False) -> 'np.ndarray | None':
        """SourceChannel.getValues() if available. Default provided.

        :param length: will return last 'length' values.
        :type length: int
        :param index_min: Index of lower limit.
        :type index_min: int
        :param index_max: Index of upper limit.
        :type index_max: int
        :param n: Will only return every nth value, defaults to 1
        :type n: int, optional
        :param subtractBackground: Indicates if the background should be subtracted, defaults to False
        :type subtractBackground: bool, optional
        :return: The array of values.
        :rtype: np.ndarray
        """
        return self.sourceChannel.getValues(length=length, index_min=index_min, index_max=index_max, n=n, subtractBackground=subtractBackground) if self.sourceChannel else None

    @property
    def value(self) -> int | float:  # | None:
        """SourceChannel.value if available. Default provided."""
        # leave decision to subtract background to be handled explicitly at higher level.
        if self.sourceChannel:
            return self.sourceChannel.value
        return None  # type: ignore  # noqa: PGH003

    @value.setter
    def value(self, value: float | None) -> None:
        if self.sourceChannel:
            self.sourceChannel.value = value  # type: ignore  # noqa: PGH003

    @property
    def enabled(self) -> bool:
        """SourceChannel.enabled if available. Default provided."""
        return self.sourceChannel.enabled if self.sourceChannel else False

    @property
    def active(self) -> bool:
        """SourceChannel.active if available. Default provided."""
        return self.sourceChannel.active if self.sourceChannel else True

    @property
    def real(self) -> bool:
        """SourceChannel.real if available. Default provided."""
        return self.sourceChannel.real if self.sourceChannel else False

    @property
    def acquiring(self) -> bool:
        """SourceChannel.acquiring if available. Default provided."""
        return self.sourceChannel.acquiring if self.sourceChannel else False

    @property
    def min(self) -> int | float:  # | None:
        """SourceChannel.min if available. Default provided."""
        return self.sourceChannel.min if self.sourceChannel else None  # type: ignore  # noqa: PGH003

    @property
    def max(self) -> int | float:  # | None:
        """SourceChannel.max if available. Default provided."""
        return self.sourceChannel.max if self.sourceChannel else None  # type: ignore  # noqa: PGH003

    # implement "display" Channel specific, some may prefer to use their internal display!
    # implement "unit" Channel specific

    @property
    def smooth(self) -> int:
        """SourceChannel.smooth if available. Default provided."""
        return self.sourceChannel.smooth if self.sourceChannel else 0

    @property
    def color(self) -> str:
        """SourceChannel.color if available. Default provided."""
        if self.sourceParameter:
            return self.sourceParameter.color
        if self.sourceChannel:
            return self.sourceChannel.color
        return '#e8e8e8'

    @property
    def linewidth(self) -> int:
        """SourceChannel.linewidth if available. Default provided."""
        return self.sourceChannel.linewidth if self.sourceChannel else 4

    @property
    def linestyle(self) -> str:
        """SourceChannel.linestyle if available. Default provided."""
        return self.sourceChannel.linestyle if self.sourceChannel else 'solid'

    def getQtLineStyle(self) -> Qt.PenStyle:
        """SourceChannel.QtLineStyle if available. Default provided."""
        return self.sourceChannel.getQtLineStyle() if self.sourceChannel else Qt.PenStyle.DotLine

    @property
    def logY(self) -> bool:
        """SourceChannel.logY if available. Default provided."""
        if self.sourceChannel:
            return self.sourceChannel.logY
        return self.unit in {'mbar', 'Pa'}

    @property
    def waitToStabilize(self) -> bool:
        """SourceChannel.waitToStabilize if available. Default provided."""
        return self.sourceChannel.waitToStabilize if self.sourceChannel else False

    @waitToStabilize.setter
    def waitToStabilize(self, waitToStabilize: bool) -> None:
        if self.sourceChannel:
            self.sourceChannel.waitToStabilize = waitToStabilize


class MetaChannel(RelayChannel):
    """Manage metadata associated with a channel by a :class:`~esibd.plugins.Scan` or :class:`~esibd.plugins.LiveDisplay`.

    Allows to restore data even if corresponding channels do not exist anymore.

    name : str
        The scan channel name is usually the same as the name of the corresponding
        :class:`~esibd.core.Channel`.
    data : numpy.array
        Scan data is saved and restored automatically.
        Each scan is free to define its data with arbitrary dimensions.
    initial : var
        Initial value. Used to restore all conditions after scan has completed.
    background : numpy.array
        If used, has to be of same dimension as data.
    unit : str
        The channel unit.
    channel: :class:`~esibd.core.PluginManager`
        The actual channel, if it exists.
    """

    def __init__(self, parentPlugin: 'ChannelManager | Scan | StaticDisplay', name: str = '', unit: str = '',  # noqa: PLR0913, PLR0917
                 recordingData: 'np.ndarray | DynamicNp | None' = None, initialValue: 'float | None' = None,
                 recordingBackground: 'np.ndarray | None' = None, inout: 'INOUT | None' = None) -> None:
        """Initialize MetaChannel.

        :param parentPlugin: Channel Parent, defaults to None
        :type parentPlugin: ChannelManager | Scan, optional
        :param name: Channel name, defaults to ''
        :type name: str | None, optional
        :param unit: Channel unit, defaults to ''
        :type unit: str, optional
        :param recordingData: Channel data, defaults to None
        :type recordingData: np.ndarray, optional
        :param initialValue: Initial value, defaults to None
        :type initialValue: float | None, optional
        :param inout: Channel type, defaults to None
        :type inout: INOUT, optional
        """
        self.parentPlugin = parentPlugin
        self.name = name
        self.recordingData = recordingData
        self.initialValue = initialValue
        self.recordingBackground = recordingBackground
        self.unit = unit
        self.sourceChannel = None
        self.inout = inout
        self.updateValueSignal: pyqtBoundSignal = None  # type: ignore  # noqa: PGH003
        self.connectSource()

    def connectSource(self, giveFeedback: bool = False) -> None:
        """Connect the sourceChannel.

        :param giveFeedback: Report on success of connection, defaults to False
        :type giveFeedback: bool, optional
        """
        # Will only be called when using MetaChannel directly. ScanChannel will implements its own version.
        if self.name == 'Time':
            return
        if not self.inout:
            self.sourceChannel = self.parentPlugin.pluginManager.DeviceManager.getChannelByName(self.name, inout=INOUT.OUT)
            if not self.sourceChannel:
                self.sourceChannel = self.parentPlugin.pluginManager.DeviceManager.getChannelByName(self.name, inout=INOUT.IN)
        else:
            self.sourceChannel = self.parentPlugin.pluginManager.DeviceManager.getChannelByName(self.name, inout=self.inout)
        if self.sourceChannel and hasattr(self.sourceChannel, Parameter.VALUE.lower()):
            self.initialValue = self.sourceChannel.value
            self.unit = self.sourceChannel.unit
            self.updateValueSignal = self.sourceChannel.signalComm.updateValueSignal
        else:
            self.initialValue = None
            self.unit = ''
            self.updateValueSignal = None  # type: ignore  # noqa: PGH003
        if giveFeedback:
            if not self.sourceChannel:
                self.parentPlugin.print(f'Source channel {self.name} could not be reconnected.', flag=PRINT.ERROR)
            else:
                self.parentPlugin.print(f'Source channel {self.name} successfully reconnected.', flag=PRINT.DEBUG)

    def display(self) -> bool:
        """Indicate of the source channel should be displayed."""
        return self.sourceChannel.display if self.sourceChannel else True

    def onDelete(self) -> None:
        """Provide onDelete for channel API consistency."""


class Channel(QTreeWidgetItem):  # noqa: PLR0904
    """Represent a virtual or real Parameter and manage all data and metadata related to that Parameter.

    Each :ref:`device<sec:devices>` can only have one type of channel, but channels have dynamic interfaces
    that allow to account for differences in the physical backend.
    Channels provide a consistent and structured interface to inputs and
    outputs. In the advanced mode, channels can be duplicated, moved, or
    deleted. You may also edit channels directly in the corresponding .ini
    file in the config path (import after edit or changes will be lost).

    Channels are accessible from any plugin using :meth:`~esibd.plugins.DeviceManager.getChannelByName`.
    This, and other features like linking channels by equations, depends on the usage of unique and descriptive channel names.
    """

    class SignalCommunicate(QObject):
        """Bundle pyqtSignals."""

        updateValueSignal = pyqtSignal(float)
        waitUntilStableSignal = pyqtSignal(int)

    channelParent: 'ChannelManager | Device | Scan'
    """The Device or Scan containing this channel."""
    print: Callable
    """Reference to :meth:`~esibd.plugins.Plugin.print`."""
    tree: 'QTreeWidget | None'
    """TreeWidget containing the channel widgets."""
    inout: INOUT = INOUT.NONE
    """Reference to :class:`~esibd.plugins.Device.inout`."""
    plotCurve: 'PlotCurveItem | PlotDataItem | None'
    """The plotCurve in the corresponding :class:`~esibd.plugins.LiveDisplay`."""
    lastAppliedValue: 'ParameterType | None'
    """Reference to last value. Allows to decide if hardware update is required."""
    parameters: list[Parameter]
    """List of channel parameters."""
    displayedParameters: list[str]
    """List of Parameters that determines which Parameters are shown in the
       user interface and in what order. Compare :meth:`~esibd.core.Channel.insertDisplayedParameter`.
       If your custom Parameter is not in this list it will not be visible in the user interface."""
    values: DynamicNp
    """The history of values shown in the :class:`~esibd.plugins.LiveDisplay`.
       Use :meth:`~esibd.core.Channel.getValues` to get a plain numpy.array."""
    backgrounds: DynamicNp
    """List of backgrounds. Only defined if corresponding device uses backgrounds."""
    useDisplays: bool = False
    convertDataDisplay: 'Callable | None' = None
    useBackgrounds: bool = False
    useMonitors: bool = False
    logY: bool = False
    """Indicates if logarithmic controls and scales should be used."""
    waitToStabilize = False
    """Indicates if the device is stabilized. Will return NaN if unstable."""
    invalid_chars: list[str]
    """Invalid characters will be removed from list of valid characters of the channel name."""
    controller: 'DeviceController'

    def __init__(self, channelParent: 'ChannelManager | Scan', tree: 'QTreeWidget | None' = None) -> None:
        """Initialize a Channel.

        :param channelParent: Channel Parent
        :type channelParent: ChannelManager | Scan, optional
        :param tree: Channel tree, defaults to None
        :type tree: QTreeWidget, optional
        """
        super().__init__()  # need to init without tree, otherwise channels will always appended to the end when trying to change order using insertTopLevelItem
        self.channelParent = channelParent
        self.pluginManager = self.channelParent.pluginManager
        self.print = self.channelParent.print
        if isinstance(self.channelParent, self.pluginManager.ChannelManager):
            self.useDisplays = self.channelParent.useDisplays
            self.convertDataDisplay = self.channelParent.convertDataDisplay
            self.useBackgrounds = self.channelParent.useBackgrounds
            self.useMonitors = self.channelParent.useMonitors
            if self.channelParent.logY:
                self.logY = self.channelParent.logY
        self.tree = tree  # may be None for internal default channels
        self.plotCurve = None
        self.rowHeight = QLineEdit().sizeHint().height() - 4
        self.signalComm = self.SignalCommunicate()
        self.signalComm.updateValueSignal.connect(self.updateValueParallel)
        self.signalComm.waitUntilStableSignal.connect(self.waitUntilStable)
        self.lastAppliedValue = None  # keep track of last value to identify what has changed
        self.parameters = []
        self.displayedParameters = []
        self.controller = None  # type: ignore  # noqa: PGH003
        self.defaultStyleSheet = None  # will be initialized when color is set
        self.warningStyleSheet = 'background: rgb(255,0,0)'
        self.warningState = False
        self.invalid_chars = [r'\.', '/']

        if isinstance(self.channelParent, self.pluginManager.ChannelManager):
            self.inout = self.channelParent.inout

        # Generate property for direct access of Parameter values.
        # NOTE: This assigns properties directly to class and only works as it uses a method that is specific to the current instance.
        # NOTE: Assigning the property directly to the instance "self" does not work as it just becomes a regular object.
        # NOTE: Errors are expected when accessing these properties for instances that do not implement the corresponding parameter.
        #       These will never be accessed, with the exception of dir(channel), e.g. used by Tree.inspect where errors should be caught and reported only for higher log levels.
        for name, default in self.getSortedDefaultChannel().items():
            if Parameter.ATTR in default and default[Parameter.ATTR] is not None:
                setattr(self.__class__, default[Parameter.ATTR], makeWrapper(name))

        for column, (name, default) in enumerate(self.getSortedDefaultChannel().items()):
            self.parameters.append(Parameter(parameterParent=self, name=name, parameterType=default[Parameter.PARAMETER_TYPE],
                                            items=default.get(Parameter.ITEMS, ''),
                                            fixedItems=default.get(Parameter.FIXEDITEMS, False),
                                            minimum=default.get(Parameter.MIN, None), maximum=default.get(Parameter.MAX, None),
                                            unit=default.get(Parameter.UNIT, ''),
                                            toolTip=default.get(Parameter.TOOLTIP, ''),
                                            internal=default.get(Parameter.INTERNAL, False),
                                            recorded=default.get(Parameter.RECORDED, False),
                                            logY=default.get(Parameter.LOGY, False),
                                            indicator=default.get(Parameter.INDICATOR, False),
                                            restore=default.get(Parameter.RESTORE, True),
                                            instantUpdate=default.get(Parameter.INSTANTUPDATE, True),
                                            displayDecimals=default.get(Parameter.DISPLAYDECIMALS, 2),
                                            itemWidget=self, column=column, tree=self.tree,
                                            event=default.get(Parameter.EVENT, None)))

        self.clearHistoryInternal()  # clearHistory call during initialization initializes history
    HEADER = 'HEADER'
    SELECT = 'Select'
    COLLAPSE = 'Collapse'
    ENABLED = 'Enabled'
    NAME = 'Name'
    VALUE = 'Value'
    BACKGROUND = 'Background'
    EQUATION = 'Equation'
    DISPLAY = 'Display'
    ACTIVE = 'Active'
    REAL = 'Real'
    SMOOTH = 'Smooth'
    LINEWIDTH = 'Linewidth'
    LINESTYLE = 'Linestyle'
    DISPLAYGROUP = 'Group'
    SCALING = 'Scaling'
    COLOR = 'Color'
    MIN = 'Min'
    MAX = 'Max'
    OPTIMIZE = 'Optimize'
    MONITOR = 'Monitor'
    UNIT = 'Unit'
    ADDITEM = 'Add Item'
    EDITITEM = 'Edit Item'
    REMOVEITEM = 'Remove Item'
    ADDPARTOCONSOLE = 'Add Parameter to Console'
    ADDCHANTOCONSOLE = 'Add Channel to Console'
    NOTES = 'Notes'

    @property
    def loading(self) -> bool:
        """Indicates if the corresponding device is currently loading."""
        return self.getDevice().loading

    @property
    def unit(self) -> str:
        """The unit of the corresponding device."""
        device = self.getDevice()
        return device.unit if isinstance(device, self.pluginManager.Device) else ''

    def getDisplayUnit(self) -> str:
        """Return the unit of the corresponding device. May represent display unit if defined."""
        device = self.getDevice()
        return device.getDisplayUnit() if isinstance(device, self.pluginManager.Device) else self.unit

    @property
    def legendName(self) -> str:
        """Name used in legends. Matches signature of Parameters."""
        return self.name

    @property
    def time(self) -> DynamicNp | None:
        """The time axis of the corresponding device."""
        device = self.getDevice()
        return device.time if isinstance(device, self.pluginManager.Device) else None

    @property
    def acquiring(self) -> bool:
        """Indicates if the channel is acquiring data."""
        if self.controller:
            return self.controller.acquiring
        device = self.getDevice()
        if isinstance(device, self.pluginManager.Device) and device.controller:
            return device.controller.acquiring
        return False

    @property
    def initialized(self) -> bool:
        """Indicate if the channel is initialized and ready for plotting."""
        if self.real and not self.enabled:
            return False
        if not self.active and not self.useMonitors:
            return True  # no initialization needed as value is provided by equation
        if self.controller:
            return self.controller.initialized
        device = self.getDevice()
        if isinstance(device, self.pluginManager.Device):
            return device.initialized
        return True

    def getDefaultChannel(self) -> dict[str, dict]:
        """Define Parameter(s) of the default Channel.

        This is also use to assign parameterTypes, if Parameters are visible outside of advanced mode,
        and many other parameter properties. See :meth:`~esibd.core.parameterDict`.
        If parameters do not exist in the settings file, the default Parameter will be added.
        Overwrite in dependent classes as needed.

        NOTE: While parameters with attributes will be automatically accessible, it is good practice to define the type of the attribute for type checking.
        """
        # definitions for type hinting
        self.collapse: bool
        self.select: bool
        self.enabled: bool
        self.name: str
        self.value: float | int  # | None ignore once, avoid frequent checking
        self.equation: str
        self.active: bool
        self.real: bool
        self.scaling: str
        self.color: str
        self.monitor: float | int  # | None ignore once, avoid frequent checking
        self.display: bool
        self.smooth: int
        self.linewidth: int
        self.linestyle: str
        self.displayGroup: str
        self.min: float
        self.max: float
        self.optimize: bool
        self.background: float

        channel = {}
        channel[self.COLLAPSE] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL,
                                    toolTip='Collapses all channels of same color below.', event=lambda: self.collapseChanged(toggle=True), attr='collapse', header=' ')
        channel[self.SELECT] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, advanced=True,
                                    toolTip='Select channel for deleting, moving, or duplicating.', attr='select',
                                    event=(lambda: self.channelParent.channelSelection(selectedChannel=self)
                                           if isinstance(self.channelParent, self.pluginManager.ChannelManager) else None))
        channel[self.ENABLED] = parameterDict(value=True, parameterType=PARAMETERTYPE.BOOL, advanced=True,
                                    header='E', toolTip='If enabled, channel will communicate with the device.',
                                    event=self.enabledChanged, attr='enabled')
        channel[self.NAME] = parameterDict(value=f'{self.channelParent.name}_parameter', parameterType=PARAMETERTYPE.TEXT, advanced=False, attr='name',
                                               event=self.nameChanged)
        channel[self.VALUE] = parameterDict(value=np.nan if self.inout == INOUT.OUT else 0,
                                               parameterType=(PARAMETERTYPE.EXP if isinstance(self.channelParent, self.pluginManager.ChannelManager)
                                                              and self.channelParent.logY else PARAMETERTYPE.FLOAT),
                                               advanced=False, header='Unit', attr='value',
                                               event=self.valueChanged,
                                               indicator=self.inout == INOUT.OUT)
        channel[self.EQUATION] = parameterDict(value='', parameterType=PARAMETERTYPE.TEXT, advanced=True, attr='equation',
                                    event=self.equationChanged)
        channel[self.ACTIVE] = parameterDict(value=True, parameterType=PARAMETERTYPE.BOOL, advanced=True,
                                    header='A', toolTip='If not active, value will be determined from equation.',
                                    event=self.activeChanged, attr='active')
        channel[self.REAL] = parameterDict(value=True, parameterType=PARAMETERTYPE.BOOL, advanced=True,
                                    header='R', toolTip='Check for physically exiting channels.',
                                    event=self.realChanged, attr='real')
        channel[self.SCALING] = parameterDict(value='normal', default='normal', parameterType=PARAMETERTYPE.COMBO, advanced=True, attr='scaling',
                                               event=self.scalingChanged,
                                                       items='small, normal, large, larger, huge', toolTip='Scaling used to display channels.')
        # * avoid using middle gray colors, as the bitwise NOT which is used for the caret color has very poor contrast
        # https://stackoverflow.com/questions/55877769/qt-5-8-qtextedit-text-cursor-color-wont-change
        channel[self.COLOR] = parameterDict(value='#e8e8e8', parameterType=PARAMETERTYPE.COLOR, advanced=True,
                                    event=self.updateColor, attr='color')
        if self.useMonitors:
            channel[self.MONITOR] = parameterDict(value=np.nan, parameterType=PARAMETERTYPE.FLOAT, advanced=False,
                                                  event=self.monitorChanged, attr='monitor', indicator=True)
        if self.useDisplays:
            channel[self.DISPLAY] = parameterDict(value=True, parameterType=PARAMETERTYPE.BOOL, advanced=False,
                                        header='D', toolTip='Display channel history.',
                                        event=self.updateDisplay, attr='display')
            channel[self.SMOOTH] = parameterDict(value='0', parameterType=PARAMETERTYPE.INTCOMBO, advanced=True,
                                            items='0, 2, 4, 8, 16, 32', attr='smooth',
                                            toolTip='Smooth using running average with selected window.')
            channel[self.LINEWIDTH] = parameterDict(value='4', parameterType=PARAMETERTYPE.INTCOMBO, advanced=True,
                                            items='2, 4, 6, 8, 10, 12, 14, 16', attr='linewidth', event=self.updateDisplay, toolTip='Line width used in plots.')
            channel[self.LINESTYLE] = parameterDict(value='solid', parameterType=PARAMETERTYPE.COMBO, advanced=True,
                                            items='solid, dotted, dashed, dashdot', attr='linestyle', event=self.updateDisplay, toolTip='Line style used in plots.')
            channel[self.DISPLAYGROUP] = parameterDict(value='1', default='1', parameterType=PARAMETERTYPE.COMBO, advanced=True, attr='displayGroup', event=self.updateDisplay,
                                                           items='0, 1, 2, 3, 4, 5, 6, 7, 8, 9', fixedItems=False, toolTip='Used to group channels in the live display.')
        if self.inout == INOUT.IN:
            channel[self.MIN] = parameterDict(value=-50, parameterType=PARAMETERTYPE.FLOAT, advanced=True,
                                    event=self.updateMin, attr='min', header='Min       ')
            channel[self.MAX] = parameterDict(value=+50, parameterType=PARAMETERTYPE.FLOAT, advanced=True,
                                    event=self.updateMax, attr='max', header='Max       ')
            channel[self.OPTIMIZE] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, advanced=False,
                                    header='O', toolTip='Selected channels will be optimized using GA.', attr='optimize')
        if self.useBackgrounds:
            channel[self.BACKGROUND] = parameterDict(value=0, parameterType=PARAMETERTYPE.FLOAT, advanced=False,
                                header='BG      ', attr='background')
        return channel

    def getSortedDefaultChannel(self) -> dict[str, dict]:
        """Return default Channel parameters sorted in the order defined by :attr:`~esibd.core.Channel.displayedParameters`."""
        self.setDisplayedParameters()
        return {k: self.getDefaultChannel()[k] for k in self.displayedParameters}

    def insertDisplayedParameter(self, parameter: str, before: str) -> None:
        """Insert your custom Parameter before an existing Parameter in :attr:`~esibd.core.Channel.displayedParameters`.

        :param parameter: The new Parameter to insert.
        :type parameter: :class:`~esibd.core.Parameter`
        :param before: The existing Parameter before which the new one will be placed.
        :type before: :class:`~esibd.core.Parameter`
        """
        self.displayedParameters.insert(self.displayedParameters.index(before), parameter)

    def setDisplayedParameters(self) -> None:
        """Determine which parameters to use and in what order.

        Extend using :meth:`~esibd.core.Channel.insertDisplayedParameter` to add more parameters.
        """
        if self.useDisplays:
            self.displayedParameters = [self.COLLAPSE, self.SELECT, self.ENABLED, self.NAME, self.VALUE, self.EQUATION, self.DISPLAY,
                                    self.ACTIVE, self.REAL, self.SMOOTH, self.LINEWIDTH, self.LINESTYLE, self.DISPLAYGROUP, self.SCALING, self.COLOR]
        else:
            self.displayedParameters = [self.COLLAPSE, self.SELECT, self.ENABLED, self.NAME, self.VALUE, self.EQUATION,
                                    self.ACTIVE, self.REAL, self.SCALING, self.COLOR]
        if self.useMonitors:
            self.displayedParameters.insert(self.displayedParameters.index(self.VALUE) + 1, self.MONITOR)
        if not isinstance(self, ScanChannel):
            if self.inout == INOUT.IN:
                self.insertDisplayedParameter(self.MIN, before=self.EQUATION)
                self.insertDisplayedParameter(self.MAX, before=self.EQUATION)
                self.insertDisplayedParameter(self.OPTIMIZE, before=self.DISPLAY)
            if self.inout != INOUT.NONE and self.useBackgrounds:
                self.insertDisplayedParameter(self.BACKGROUND, before=self.DISPLAY)

    def tempParameters(self) -> list[str]:
        """List of parameters, such as live signal or status, that will not be saved and restored."""
        tempParameters = []
        if self.inout == INOUT.OUT:
            tempParameters.append(self.VALUE)
        if self.useBackgrounds:
            tempParameters.append(self.BACKGROUND)
        if self.useMonitors:
            tempParameters.append(self.MONITOR)
        return tempParameters

    def getParameterByName(self, name: str) -> Parameter:  # | None
        """Get a Channel Parameter by name.

        :param name: The name of the Parameter.
        :type name: str
        :return: The requested Parameter.
        :rtype: esibd.core.Parameter
        """
        parameter = next((parameter for parameter in self.parameters if parameter.name.strip().lower() == name.strip().lower()), None)
        if not parameter:
            self.print(f'Could not find Parameter {name}.', flag=PRINT.DEBUG)
        return parameter  # type: ignore  # noqa: PGH003 Rather ignore None warning once here than deal with it for every function call.

    def getRecordedParameters(self) -> list[Parameter]:
        """Return parameters that are recorded."""
        return [parameter for parameter in self.parameters if parameter.recorded]

    def asDict(self, includeTempParameters: bool = False, formatValue: bool = False, escapePercent: bool = False) -> dict[str, ParameterType]:
        """Return a dictionary containing all Channel Parameters and their values.

        :param includeTempParameters: If True, dict will contain temporary Parameters, Defaults to False
        :type includeTempParameters: bool, optional
        :param formatValue: Indicates if the value should be formatted as a string, Defaults to False
        :type formatValue: bool, optional
        :param escapePercent: % has to be escaped by %% for use with configparser.
        :type escapePercent: bool
        :return: The channel as dictionary.
        :rtype: dict[str, ParameterType]
        """
        channel_dict = {}
        for parameter in self.parameters:
            if includeTempParameters or parameter.name not in self.tempParameters():
                channel_dict[parameter.name] = parameter.formatValue(escapePercent=escapePercent) if formatValue else parameter.value
        return channel_dict

    def updateValueParallel(self, value: float) -> None:  # used to update from external threads
        """Update the value (thread safe).

        :param value: new value
        :type value: float
        """
        self.value = value  # pylint: disable=[attribute-defined-outside-init]  # attribute defined by makeWrapper

    def applyValue(self, apply: bool = False) -> None:
        """Apply value to device if value has changed or explicitly requested.

        :param apply: If True, value will be applied even if it has not changed. Defaults to None
        :type apply: bool
        """
        if self.real and ((self.value != self.lastAppliedValue) or apply):
            self.lastAppliedValue = self.value
            if self.controller:
                self.controller.applyValueFromThread(self)
            elif isinstance(self.channelParent, self.pluginManager.Device):
                controller = self.channelParent.controller
                if controller:
                    controller.applyValueFromThread(self)

    def activeChanged(self) -> None:
        """Update channel after active state has changed."""
        self.toggleBackgroundVisible()
        self.updateColor()
        if not self.channelParent.loading:
            self.pluginManager.DeviceManager.globalUpdate(inout=self.inout)

    def valueChanged(self) -> None:
        """Update after value has changed."""
        if self.inout == INOUT.IN:
            self.pluginManager.DeviceManager.globalUpdate(inout=self.inout)

    def equationChanged(self) -> None:
        """Update channel after equation has changed."""
        if not self.channelParent.loading:
            self.pluginManager.DeviceManager.globalUpdate(inout=self.inout)

    def collapseChanged(self, toggle: bool = True) -> None:
        """Update visible channels after collapse has changed.

        :param toggle: Toggles display of visible channels, defaults to True
        :type toggle: bool, optional
        """
        cast('QPushButton', self.getParameterByName(self.COLLAPSE).getWidget()).setIcon(self.channelParent.makeCoreIcon('toggle-expand.png' if self.collapse else 'toggle.png'))
        if toggle and not self.channelParent.loading:  # otherwise only update icon
            self.channelParent.toggleAdvanced()

    def waitUntilStable(self, wait: int) -> None:
        """Wait for signal to stabilize.

        Will return NaN until stable.
        Make sure to call from the main_thread.

        :param wait: Wait time in ms.
        :type wait: int
        """
        self.waitToStabilize = True
        QTimer().singleShot(wait, self.resetUnstable)

    def resetUnstable(self) -> None:
        """Indicate signal is stable."""
        self.waitToStabilize = False

    def appendValue(self, lenT: int, nan: bool = False) -> None:  # noqa: C901
        """Append a datapoint to the recorded values.

        :param lenT: length of corresponding time array, defaults to None
        :type lenT: int, optional
        :param nan: If True, np.nan is used instead of current value. This should mark a new collection and prevent interpolation in regions without data.
        :type nan: bool
        """
        if nan:
            if self.inout == INOUT.OUT:
                self.value = np.nan  # keep user defined value for input devices, leave undefined until output device provides value
            if self.useMonitors:
                self.monitor = np.nan
            self.values.add(x=np.nan, lenT=lenT)
            for parameter in self.getRecordedParameters():
                parameter.values.add(x=np.nan, lenT=lenT)
        elif self.useMonitors and self.enabled and self.real:
            if self.monitor is not None:
                self.values.add(x=self.monitor, lenT=lenT)
        elif self.value is not None:
            self.values.add(x=self.value, lenT=lenT)
        if self.useBackgrounds:
            self.backgrounds.add(x=self.background, lenT=lenT)
        for parameter in self.getRecordedParameters():
            if isinstance(parameter.value, (float, int)):
                parameter.values.add(x=parameter.value, lenT=lenT)

    def getValues(self, *, length: 'int | None' = None, index_min: 'int | None' = None, index_max: 'int | None' = None, n: int = 1,
                   subtractBackground: bool = False) -> np.typing.NDArray[np.float64 | np.float32]:  # pylint: disable = unused-argument  # use consistent arguments for all versions of getValues
        """Return plain Numpy array of values.

        Note that background subtraction only affects what is displayed, the raw signal and background curves are always retained.

        :param length: will return last 'length' values.
        :type length: int
        :param index_min: Index of lower limit.
        :type index_min: int
        :param index_max: Index of upper limit.
        :type index_max: int
        :param n: Will only return every nth value, defaults to 1
        :type n: int, optional
        :param subtractBackground: Indicates if the background should be subtracted, defaults to False
        :type subtractBackground: bool, optional
        :return: The array of values.
        :rtype: np.ndarray[Any, np.dtype[np.float32]]
        """
        if self.useBackgrounds and subtractBackground:
            values = self.values.get(length=length, index_min=index_min, index_max=index_max, n=n)
            backgrounds = self.backgrounds.get(length=length, index_min=index_min, index_max=index_max, n=n)
            if values.shape[0] == backgrounds.shape[0]:
                return (values - backgrounds)
            if isinstance(self.channelParent, self.pluginManager.Device):
                self.print(f'Channel {self.name}: Values and backgrounds have inconsistent size. Using values only. Will be repaired after next recording.'
                           f' Otherwise check for corrupt restore file {Path(self.pluginManager.Settings.configPath) / self.channelParent.confh5.strip("_")}.\n'
                           'Delete restore file to regenerate it (all history will be lost!) or repair it manually.', flag=PRINT.WARNING)
            else:
                self.print('Channel {self.name}: Values and backgrounds have inconsistent size. Using values only. Will be repaired after next recording.', flag=PRINT.WARNING)
        return self.values.get(length=length, index_min=index_min, index_max=index_max, n=n)

    def clearHistory(self) -> None:  # overwrite as needed, e.g. when keeping history of more than one parameter
        """Clear all history data including backgrounds if applicable."""
        self.clearHistoryInternal()

    def clearHistoryInternal(self) -> None:
        """Clear all history data including backgrounds if applicable."""
        # only used for initialization
        if isinstance(self.channelParent, self.pluginManager.Device):
            if self.pluginManager.DeviceManager and (self.pluginManager.Settings and not self.pluginManager.Settings.loading):
                self.values = DynamicNp(max_size=self.channelParent.maxDataPoints)
            self.clearPlotCurve()
            if self.useBackgrounds:
                self.backgrounds = DynamicNp(max_size=self.channelParent.maxDataPoints)
            for parameter in self.getRecordedParameters():
                parameter.values = DynamicNp(max_size=self.channelParent.maxDataPoints)

    def clearPlotCurve(self) -> None:
        """Clear the plot curve. It will be recreated (with updated values and settings) next time plot is called."""
        device = self.getDevice()
        if isinstance(device, self.pluginManager.ChannelManager):
            if self.plotCurve:
                # all plot curves need to have a curveParent so they can be removed gracefully
                self.plotCurve.curveParent.removeItem(self.plotCurve)  # plotWidget still tries to access this even if deleted -> need to explicitly remove!
                if isinstance(self.plotCurve.curveParent, ViewBox):
                    self.plotCurve.curveLegend.removeItem(self.plotCurve)
                self.plotCurve.clear()
                self.plotCurve.deleteLater()
                self.plotCurve = None
                if device.liveDisplay:
                    device.liveDisplay.updateLegend = True
            for parameter in self.getRecordedParameters():
                parameter.clearPlotCurve()

    def getDevice(self) -> 'ChannelManager | Device | Scan':
        """Get the device. Overwrite for more specific cases like relay channels where the channel parent is not the device."""
        return self.channelParent

    def getIcon(self, desaturate: bool = False) -> 'Icon':
        """Get the channel Icon. This is typically identical with the corresponding device Icon, but some devices may have channel specific Icons.

        :param desaturate: Indicates if color should be removed from icon, defaults to False
        :type desaturate: bool, optional
        :return: Icon
        :rtype: :class:`~esibd.core.Icon`
        """
        return self.getDevice().getIcon(desaturate=desaturate)

    def getQtLineStyle(self) -> Qt.PenStyle:
        """Get Qt.PenStyle matching matplotlib linestyle."""
        match self.linestyle:
            case 'dotted':
                return Qt.PenStyle.DotLine
            case 'dashed':
                return Qt.PenStyle.DashLine
            case 'dashdot':
                return Qt.PenStyle.DashDotLine
            case _:  # solid
                return Qt.PenStyle.SolidLine

    def updateColor(self) -> QColor:  # noqa: C901, PLR0912
        """Apply new color to all controls."""
        if getDarkMode():
            color = QColor(self.color).darker(150) if self.active else QColor(self.color).darker(200)  # indicate passive channels by darker color
        else:
            color = QColor(self.color) if self.active else QColor(self.color).darker(115)  # indicate passive channels by darker color
        qb = QBrush(color)
        for i in range(len(self.parameters) + 1):  # use highest index
            self.setBackground(i, qb)  # use correct color even when widgets are hidden
        i = 0
        for parameter in self.parameters:
            if parameter.recorded:
                # vary color slightly to help distinguish recorded parameters from recorded channel values.
                i += 1
                parameter.color = similar_but_distinct_color(color_hex=self.color, index=i)
            else:
                parameter.color = self.color
            widget = parameter.getWidget()
            if widget:
                widget.container.setStyleSheet(f'background-color: {color.name()};')
                if isinstance(widget, QToolButton):
                    widget.setStyleSheet(f"""QToolButton {{background-color: {color.name()}}}
                                    QToolButton:checked {{background-color: {color.darker(150 if getDarkMode() else 120).name()};
                                    border-style: inset; border-width: 2px; border-color: 'gray';}}""")
                elif isinstance(widget, QComboBox):
                    pass
                elif isinstance(widget, QCheckBox):
                    checkBoxHeight = widget.checkBoxHeight if hasattr(widget, 'checkBoxHeight') else min(self.rowHeight - 4, QCheckBox().sizeHint().height() - 2)
                    widget.setStyleSheet(f'QCheckBox{{background-color: {color.name()}; color:{colors.fg}}} '
                                         f'QCheckBox::indicator {{ width: {checkBoxHeight}; height: {checkBoxHeight};}}')
                elif isinstance(widget, QPushButton):
                    widget.setStyleSheet(f'background-color: {color.name()}; color:{colors.fg}; margin:0px; border:none;')
                else:
                    widget.setStyleSheet(f'background-color: {color.name()}; color:{colors.fg}; margin:0px;')
        self.updateDisplay()
        self.defaultStyleSheet = f'background-color: {color.name()}'
        if not self.channelParent.loading and self.channelParent.pluginType in {PLUGINTYPE.INPUTDEVICE, PLUGINTYPE.OUTPUTDEVICE}:
            # Update color in relay channels.
            # Only called by device channels! Relay channels should not call this to prevent infinite loops.
            self.channelParent.pluginManager.reconnectSource(self.name)
        return color

    def scalingChanged(self) -> None:
        """Adjust height of all Channel Parameters according to new scaling value."""
        normalHeight = QLineEdit().sizeHint().height() - 4
        match self.scaling:
            case 'small':
                self.rowHeight = int(normalHeight * .6)
            case 'normal':
                self.rowHeight = normalHeight
            case 'large':
                self.rowHeight = normalHeight * 1.5
            case 'larger':
                self.rowHeight = normalHeight * 2
            case _:  # 'huge'
                self.rowHeight = normalHeight * 4
        for parameter in self.parameters:
            parameter.setHeight(self.rowHeight)
        if not self.loading and self.tree:
            self.tree.scheduleDelayedItemsLayout()

    def sizeHint(self, column) -> QSize:  # pylint: disable = missing-param-doc, missing-type-doc  # noqa: ANN001, ARG002
        """Provide a custom size hint based on the item's content."""
        return QSize(100, int(self.rowHeight))  # Width is not relevant

    def realChanged(self) -> None:
        """Extend as needed. Already linked to real checkbox."""
        if self.ENABLED in self.displayedParameters:
            enabledWidget = self.getParameterByName(self.ENABLED).getWidget()
            if enabledWidget:
                enabledWidget.setVisible(self.real)
                self.toggleBackgroundVisible()
                if self.useMonitors:
                    monitorWidget = self.getParameterByName(self.MONITOR).getWidget()
                    if monitorWidget:
                        monitorWidget.setVisible(self.real)
                if not self.channelParent.loading:
                    self.pluginManager.DeviceManager.globalUpdate(inout=self.inout)

    def enabledChanged(self) -> None:
        """Extend as needed. Already linked to enabled checkbox."""
        if not self.channelParent.loading and isinstance(self.channelParent, self.pluginManager.Device):
            self.toggleBackgroundVisible()
            self.pluginManager.DeviceManager.globalUpdate(inout=self.inout)
            self.clearPlotCurve()
            if self.enabled:
                self.channelParent.appendData(nan=True)  # prevent interpolation to old data
            if not self.channelParent.recording and self.channelParent.liveDisplay:
                self.channelParent.liveDisplay.plot(apply=True)

    def toggleBackgroundVisible(self) -> bool:
        """Show the background widget if applicable and hides it otherwise.

        :return: True if background is visible.
        :rtype: bool
        """
        if self.useBackgrounds:
            backgroundVisible = self.enabled and self.active and self.real
            self.getParameterByName(self.BACKGROUND).setVisible(backgroundVisible)
            if not backgroundVisible:
                self.background = 0
            return backgroundVisible
        return False

    def nameChanged(self) -> None:
        """Update display and linked channels if channel name changed."""
        if self.inout == INOUT.OUT:
            self.updateDisplay()
        self.pluginManager.connectAllSources()

    def updateDisplay(self) -> None:
        """Update live and static displays."""
        if not self.channelParent.loading and self.useDisplays and isinstance(self.channelParent, self.pluginManager.ChannelManager):
            self.clearPlotCurve()
            if not self.channelParent.recording and self.channelParent.liveDisplay:
                self.channelParent.liveDisplay.plot(apply=True)
            self.pluginManager.DeviceManager.updateStaticPlot()

    def monitorChanged(self) -> None:
        """Highlights monitors if they deviate to far from set point. Extend for custom monitor logic if applicable."""
        device = self.getDevice()
        if isinstance(device, self.pluginManager.Device) and self.monitor is not None and self.value is not None:
            self.updateWarningState(self.enabled and (hasattr(self.channelParent, 'controller') and device.controller and
                                                  device.controller.acquiring) and device.isOn() and abs(self.monitor - self.value) > 1)

    def updateWarningState(self, warn: bool = False) -> None:
        """Update warningState of monitor and applies corresponding style sheet.

        :param warn: Indicates if the warningStyleSheet should be used, defaults to False
        :type warn: bool, optional
        """
        if warn != self.warningState:
            self.warningState = warn
            monitorWidget = self.getParameterByName(self.MONITOR).getWidget()
            if monitorWidget:
                monitorWidget.setStyleSheet(self.warningStyleSheet if warn else self.defaultStyleSheet)

    def initGUI(self, item: dict) -> None:  # noqa: C901, PLR0912
        """Initialize Channel GUI.

        Call after corresponding QTreeWidgetItem has been added to tree.
        Item needs parent for all graphics operations.

        :param item: Dictionary containing all channel information.
        :type item: dict
        """
        for parameter in self.parameters:
            parameter.applyWidget()
        name_parameter = self.getParameterByName(self.NAME)
        # remove / in names. this is valid in all other fields unless explicitly removed
        # ! needs to be removed before value is loaded from file!
        if name_parameter.parameterType == PARAMETERTYPE.TEXT:
            lineEdit = cast('LineEdit', name_parameter.getWidget())
            for invalid_char in self.invalid_chars:
                lineEdit.valid_chars = lineEdit.valid_chars.replace(invalid_char, '')
        for name, default in self.getSortedDefaultChannel().items():
            # add default value if not found in file. Will be saved to file later.
            if name in item and name not in self.tempParameters() and default[Parameter.RESTORE]:
                self.getParameterByName(name).value = item[name]
            else:
                self.getParameterByName(name).value = default[self.VALUE]
                if isinstance(self.channelParent, self.pluginManager.ChannelManager) and name not in self.tempParameters() and default[Parameter.RESTORE] and not len(item) < 2:  # noqa: PLR2004
                    # len(item) < 2 -> only provided name -> generating default file
                    self.print(f'Added missing parameter {name} to channel {item[self.NAME]} using default value {default[self.VALUE]}.')
                    self.channelParent.channelsChanged = True
        if name_parameter.parameterType == PARAMETERTYPE.TEXT:
            name_parameter.line.allowEmptyText = False  # names cannot be empty
        if self.inout != INOUT.NONE and self.EQUATION in self.displayedParameters:
            line = self.getParameterByName(self.EQUATION).line
            line.setMinimumWidth(200)
            font = line.font()
            font.setPointSize(8)
            line.setFont(font)
        if self.SELECT in self.displayedParameters:
            select = self.getParameterByName(self.SELECT)
            initialValue = select.value or False
            select.widget = ToolButton()  # hard to spot checked QCheckBox. QPushButton is too wide -> overwrite internal widget to QToolButton
            select.applyWidget()
            select.widget.setMaximumHeight(select.rowHeight)  # default too high
            select.widget.setText(self.SELECT.title())
            select.widget.setMinimumWidth(5)
            select.widget.setCheckable(True)
            select.value = initialValue
        if self.DISPLAY in self.displayedParameters:
            display = self.getParameterByName(self.DISPLAY)
            for parameter in self.getRecordedParameters():
                display.extraContextActions.append(ContextAction(text=f'Toggle display of {parameter.name}', event=parameter.updateDisplay))
        if self.COLLAPSE in self.displayedParameters:
            collapse = self.getParameterByName(self.COLLAPSE)
            initialValue = collapse.value or False
            collapse.widget = QPushButton()
            collapse.widget.setCheckable(True)
            collapse.widget.setStyleSheet('QPushButton{border:none;}')
            collapse.applyWidget()
            collapse.value = initialValue
            collapse.widget.setIcon(self.channelParent.makeCoreIcon('toggle-expand.png' if self.collapse else 'toggle.png'))
        if self.inout != INOUT.NONE:
            self.updateColor()
            self.realChanged()
            if self.inout == INOUT.IN:
                self.updateMin()
                self.updateMax()
        self.scalingChanged()

    def updateMin(self) -> None:
        """Apply new minimum to value widget."""
        spin = self.getParameterByName(self.VALUE).spin
        if spin:
            if isinstance(spin, LabviewSpinBox):
                spin.setMinimum(int(self.min))
            else:
                spin.setMinimum(self.min)
            if not self.channelParent.loading:
                self.pluginManager.reconnectSource(self.name)  # update limits in relay channels

    def updateMax(self) -> None:
        """Apply new maximum to value widget."""
        spin = self.getParameterByName(self.VALUE).spin
        if spin:
            if isinstance(spin, LabviewSpinBox):
                spin.setMaximum(int(self.max))
            else:
                spin.setMaximum(self.max)
        if not self.channelParent.loading:
            self.pluginManager.reconnectSource(self.name)  # update limits in relay channels

    def onDelete(self) -> None:
        """Extend to handle events on deleting. E.g. handle references that should remain available."""
        self.clearPlotCurve()

    def initSettingsContextMenuBase(self, parameter: Parameter | Setting, pos: QPoint) -> None:
        """General implementation of a context menu.

        The relevant actions will be chosen based on the type and properties of the :class:`~esibd.core.Parameter`.

        :param parameter: The parameter for which the context menu is requested.
        :type parameter: esibd.core.Parameter
        :param pos: The position of the context menu.
        :type pos: QPoint
        """
        settingsContextMenu = QMenu(self.tree)
        for contextAction in parameter.extraContextActions:
            contextAction.action = settingsContextMenu.addAction(contextAction.text)
        addChannelToConsoleAction = settingsContextMenu.addAction(self.ADDCHANTOCONSOLE)
        addParameterToConsoleAction = settingsContextMenu.addAction(self.ADDPARTOCONSOLE)
        # if parameter.parameterType in [PARAMETERTYPE.COMBO, PARAMETERTYPE.INTCOMBO, PARAMETERTYPE.FLOATCOMBO]:
        #     NOTE channels do only save current value but not the items -> thus editing items is currently not supported
        if not settingsContextMenu.actions():
            return
        settingsContextMenuAction = settingsContextMenu.exec(pos)
        if settingsContextMenuAction:  # no option selected (NOTE: if it is None this could trigger a non initialized action which is also None if not tested here)
            if settingsContextMenuAction is addChannelToConsoleAction:
                self.pluginManager.Console.addToNamespace('channel', parameter.parameterParent)
                self.pluginManager.Console.execute(command='channel')
            elif settingsContextMenuAction is addParameterToConsoleAction:
                self.pluginManager.Console.addToNamespace('parameter', parameter)
                self.pluginManager.Console.execute(command='parameter')
            else:
                for contextAction in parameter.extraContextActions:
                    if settingsContextMenuAction is contextAction.action:
                        contextAction.event()
                        break


class ScanChannel(RelayChannel, Channel):
    """Minimal UI for abstract PID channel."""

    recordingData: 'np.ndarray | None'  # overwrite in child classes as needed: 'np.ndarray | DynamicNp | None'
    line: 'Line2D'

    def __init__(self, scan: 'Scan', **kwargs) -> None:
        """Initialize ScanChannel.

        :param scan: Parent Scan.
        :type scan: Scan
        """
        self.scan = scan
        Channel.__init__(self, channelParent=scan, **kwargs)
        self.sourceChannel = None
        self.recordingData = None
        self.initialValue = None
        self.updateValueSignal = None

    def onDelete(self) -> None:  # noqa: D102
        super().onDelete()
        self.removeEvents()

    DEVICE = 'Device'

    def getDevice(self) -> 'Device':  # noqa: D102
        # scan channel devices will always be of type Device
        return super().getDevice()  # type: ignore  # noqa: PGH003

    # definitions for type hinting
    display: bool
    unit: str
    notes: str

    def getDefaultChannel(self) -> dict[str, dict]:  # noqa: D102
        channel = super().getDefaultChannel()
        channel.pop(Channel.SELECT)
        channel.pop(Channel.ACTIVE)
        channel.pop(Channel.EQUATION)
        channel.pop(Channel.REAL)
        channel.pop(Channel.COLOR)
        channel.pop(Channel.COLLAPSE)
        channel[self.VALUE][Parameter.INDICATOR] = True
        channel[self.VALUE][Parameter.HEADER] = 'Value'
        channel[self.NAME][Parameter.PARAMETER_TYPE] = PARAMETERTYPE.LABEL
        channel[self.NAME][Parameter.INDICATOR] = True
        if self.scan.useDisplayParameter:
            channel[self.DISPLAY] = parameterDict(value=True, parameterType=PARAMETERTYPE.BOOL, advanced=False,
                                        header='D', toolTip='Display channel history.',
                                        event=self.updateDisplay, attr='display')
        channel[self.DEVICE] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, advanced=False,
                                                 toolTip='Source: Unknown.', header=' ')
        channel[self.UNIT] = parameterDict(value='', parameterType=PARAMETERTYPE.LABEL, attr='unit', header='Unit   ', indicator=True)
        channel[self.NOTES] = parameterDict(value='', parameterType=PARAMETERTYPE.LABEL, advanced=True, attr='notes', indicator=True)
        return channel

    def tempParameters(self) -> list[str]:
        """Return list of tempParameters.

        ScanChannels are not restored from file, thus every parameter is a tempParameter.
        """
        tempParameters = [*super().tempParameters(), self.VALUE, self.DEVICE, self.NOTES, self.SCALING]
        if self.scan.useDisplayParameter:
            tempParameters += [self.DISPLAY]
        return tempParameters

    def setDisplayedParameters(self) -> None:  # noqa: D102
        super().setDisplayedParameters()
        self.displayedParameters.remove(self.COLLAPSE)
        self.displayedParameters.remove(self.ENABLED)
        self.displayedParameters.remove(self.ACTIVE)
        self.displayedParameters.remove(self.EQUATION)
        self.displayedParameters.remove(self.REAL)
        self.displayedParameters.remove(self.COLOR)
        self.displayedParameters.remove(self.SELECT)
        self.insertDisplayedParameter(self.DEVICE, self.NAME)
        self.insertDisplayedParameter(self.UNIT, before=self.SCALING)
        if self.scan.useDisplayParameter:
            self.insertDisplayedParameter(self.DISPLAY, before=self.SCALING)
        self.insertDisplayedParameter(self.NOTES, before=self.SCALING)

    def initGUI(self, item: dict) -> None:  # noqa: D102
        super().initGUI(item)
        device = self.getParameterByName(self.DEVICE)
        device.widget = QPushButton()
        device.widget.setStyleSheet('QPushButton{border:none;}')
        device.applyWidget()
        if self.scan.useDisplayParameter:
            self.display = True

    def connectSource(self, giveFeedback: bool = False) -> None:  # noqa: C901, PLR0912, PLR0915
        """Connect the sourceChannel.

        :param giveFeedback: Report on success of connection, defaults to False
        :type giveFeedback: bool, optional
        """
        channel_name = self.name
        parameter_name = ''
        if '.' in self.name:
            channel_name = self.name.split('.')[0]
            parameter_name = self.name.split('.')[1]  # if this is defined, Scan will not connect to the value but to the specified parameter of the sourceChannel
        self.sourceChannel = self.scan.pluginManager.DeviceManager.getChannelByName(channel_name, inout=INOUT.OUT)
        if not self.sourceChannel:
            self.sourceChannel = self.scan.pluginManager.DeviceManager.getChannelByName(channel_name, inout=INOUT.IN)
        # if self.unit != '' and self.sourceChannel and self.unit != self.sourceChannel.unit:
        #     Found a channel that has the same name but likely belongs to another device.
        #     In most cases the only consequence is using the wrong color.
        #     Handle in specific scan if other channel specific properties are relevant
        if self.sourceChannel:
            self.sourceParameter = self.sourceChannel.getParameterByName(parameter_name) if parameter_name else None
            if self.sourceParameter and self.sourceParameter.parameterType not in {PARAMETERTYPE.INT, PARAMETERTYPE.FLOAT, PARAMETERTYPE.EXP}:
                self.print(f'Cannot link to parameter {parameter_name} on channel {channel_name} as only parameters of type INT, FLOAT, and EXP are supported for linking.')
                self.sourceParameter = None  # Do not link
                self.sourceChannel = None  # Do not link
        devicePushButton = cast('QPushButton', self.getParameterByName(self.DEVICE).getWidget())
        if self.sourceChannel:
            self.initialValue = self.sourceParameter.value if self.sourceParameter else self.sourceChannel.value
            self.updateValueSignal = self.sourceParameter.signalComm.updateValueSignal if self.sourceParameter else self.sourceChannel.signalComm.updateValueSignal
            devicePushButton.setIcon(self.sourceChannel.getIcon(desaturate=(not self.sourceChannel.acquiring and not self.sourceChannel.getDevice().recording)))
            full_source = (f'Source: {self.sourceChannel.getDevice().name}.{self.sourceChannel.name}.{parameter_name}' if self.sourceParameter else
                           f'Source: {self.sourceChannel.getDevice().name}.{self.sourceChannel.name}')
            devicePushButton.setToolTip(f'Source: {full_source}')
            value = self.getParameterByName(self.VALUE)
            if self.sourceParameter:
                value.parameterType = self.sourceParameter.parameterType
                value.applyWidget()
                self.value = cast('float | None', self.sourceParameter.value)
                self.sourceParameter.extraEvents.append(self.relayValueEvent)
                self.unit = self.sourceParameter.unit
            elif self.sourceChannel.useMonitors and self.sourceChannel.real:
                value.parameterType = self.sourceChannel.getParameterByName(self.MONITOR).parameterType
                value.applyWidget()
                self.value = self.sourceChannel.monitor
                self.sourceChannel.getParameterByName(self.MONITOR).extraEvents.append(self.relayValueEvent)
            else:
                value.parameterType = self.sourceChannel.getParameterByName(self.VALUE).parameterType
                value.applyWidget()
                self.value = self.sourceChannel.value
                self.sourceChannel.getParameterByName(self.VALUE).extraEvents.append(self.relayValueEvent)
            if not self.unit:  # do not overwrite unit if set explicitly
                self.unit = self.sourceChannel.unit
            self.notes = f'Source: {self.sourceChannel.getDevice().name}.{self.sourceChannel.name}'
        else:
            self.initialValue = np.nan
            self.updateValueSignal = None
            devicePushButton.setIcon(self.scan.makeCoreIcon('help_large_dark.png' if getDarkMode() else 'help_large.png'))
            devicePushButton.setToolTip('Source: Unknown')
            self.notes = f'Could not find {self.name}'
        self.getParameterByName(self.DEVICE).setHeight()
        self.updateColor()
        self.scalingChanged()
        if giveFeedback:
            if self.sourceChannel:
                self.print(f'Source channel {self.name} successfully reconnected.', flag=PRINT.DEBUG)
            else:
                self.print(f'Source channel {self.name} could not be reconnected.', flag=PRINT.ERROR)

    def relayValueEvent(self) -> None:
        """Update value when sourceChannel.value changed."""
        if self.sourceChannel:
            # Note self.value should only be used as a display. it should show the background corrected value if applicable
            # the uncorrected value should be accessed using self.sourceChannel.value or self.getValues
            try:
                if self.sourceParameter:
                    self.value = cast('float | None', self.sourceParameter.value)
                elif self.sourceChannel.useMonitors and self.sourceChannel.real:
                    self.value = self.sourceChannel.monitor
                else:
                    device = self.sourceChannel.getDevice()
                    sourceChannelValue = self.sourceChannel.value
                    if isinstance(device, self.pluginManager.Device) and sourceChannelValue:
                        self.value = sourceChannelValue - self.sourceChannel.background if device.subtractBackgroundActive() else sourceChannelValue
            except RuntimeError:
                self.removeEvents()

    def removeEvents(self) -> None:
        """Remove extra events from sourceChannel."""
        if self.sourceChannel:
            if self.sourceParameter and self.relayValueEvent in self.sourceParameter.extraEvents:
                self.sourceParameter.extraEvents.remove(self.relayValueEvent)
            if self.sourceChannel.useMonitors and self.sourceChannel.real:
                if self.relayValueEvent in self.sourceChannel.getParameterByName(self.MONITOR).extraEvents:
                    self.sourceChannel.getParameterByName(self.MONITOR).extraEvents.remove(self.relayValueEvent)
            elif self.relayValueEvent in self.sourceChannel.getParameterByName(self.VALUE).extraEvents:
                self.sourceChannel.getParameterByName(self.VALUE).extraEvents.remove(self.relayValueEvent)

    def updateDisplay(self) -> None:  # noqa: D102
        # in general scan channels should be passive, but we need to react to changes in which Channels should be displayed
        if self.scan.display and self.scan.displayActive() and not self.loading and not self.scan.initializing:
            self.scan.display.initFig()
            self.scan.plot(update=self.scan.recording, done=not self.scan.recording)

    @property
    def loading(self) -> bool:  # noqa: D102
        return self.scan.loading


class ParameterWidget:
    """Implement functionality shared by all parameter widgets."""

    container: QWidget


class LabviewSpinBox(QSpinBox, ParameterWidget):
    """Implements handling of arrow key events based on curser position similar as in LabView."""

    def __init__(self, indicator: bool = False) -> None:
        """Initialize LabviewSpinBox.

        :param indicator: Indicators will be read only, defaults to False
        :type indicator: bool, optional
        """
        self.NAN = 'NaN'
        self._is_nan = False
        super().__init__()
        self.indicator = indicator
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setMinimumWidth(54)
        self.setRange(np.iinfo(np.int32).min, np.iinfo(np.int32).max)  # limit explicitly if needed, this seems more useful than the [0, 100] default range
        if indicator:
            self.setReadOnly(True)
            self.preciseValue = 0

    def keyPressEvent(self, event: QKeyEvent) -> None:  # pylint: disable = missing-param-doc
        """Allow to manually rest a value from NaN."""
        if Qt.Key.Key_0 <= event.key() <= Qt.Key.Key_9 or event.key() == Qt.Key.Key_Minus:
            self._is_nan = False
        super().keyPressEvent(event)

    def contextMenuEvent(self, e: 'QContextMenuEvent | None') -> None:  # pylint: disable = missing-param-doc, missing-type-doc
        """Suppresses context menu for indicators."""
        if e and self.indicator:
            e.ignore()
        else:
            super().contextMenuEvent(e)

    def wheelEvent(self, e: 'QWheelEvent | None') -> None:  # pylint: disable = missing-param-doc, missing-type-doc
        """Overwrite to disable accidental change of values via the mouse wheel."""
        if e:
            e.ignore()

    def valueFromText(self, text: 'str | None') -> int:
        """Convert text to value.

        :param text: Input text.
        :type text: str
        :return: value
        :rtype: int
        """
        if text and not self._is_nan:
            return int(text)
        return -1  # returning float np.nan breaks internal handling, still np.nan will be returned by value() if applicable

    def validate(self, input: 'str | None', pos: int) -> tuple[QValidator.State, str, int]:  # pylint: disable = missing-param-doc, missing-function-docstring, missing-type-doc  # noqa: A002, D102
        if self._is_nan:
            return (QValidator.State.Acceptable, self.NAN, pos)
        return super().validate(input, pos)

    def textFromValue(self, v: int) -> str:  # pylint: disable = missing-param-doc, missing-type-doc
        """Make sure nan and inf will be represented by NaN."""
        if np.isnan(v) or np.isinf(v):
            return self.NAN
        return super().textFromValue(v)

    def value(self) -> int:
        """Return nan instead of trying convert it."""
        if self._is_nan:
            return np.nan  # type: ignore  # noqa: PGH003
        return super().value()

    def setValue(self, val: int) -> None:  # pylint: disable = missing-param-doc, missing-type-doc
        """Display nan and inf as text."""
        lineEdit = self.lineEdit()
        if (np.isnan(val) or np.isinf(val)) and lineEdit:
            self._is_nan = True
            lineEdit.setText(self.NAN)  # needed in rare cases where setting to nan would set to maximum
        else:
            self._is_nan = False
            super().setValue(val)

    def stepBy(self, steps: int) -> None:  # pylint: disable = missing-param-doc, missing-type-doc
        """Handle stepping value depending con caret position."""
        lineEdit = self.lineEdit()
        if lineEdit:
            text = lineEdit.text()
            cur = lineEdit.cursorPosition()
            pos = len(text) - cur
            if cur == 0 and '-' not in text:  # left of number
                pos = len(text) - 1
            if cur <= 1 and '-' in text:  # left of number
                pos = len(text) - 2
            val = self.value() + 10**pos * steps  # use steps for sign
            self.setValue(val)
            # keep cursor position fixed relative to .
            newText = lineEdit.text()
            if len(newText) > len(text):
                if cur == 0 and '-' not in text:
                    lineEdit.setCursorPosition(2)
                elif cur <= 1 and '-' in text:
                    lineEdit.setCursorPosition(3)
                else:
                    lineEdit.setCursorPosition(cur + 1)
            elif len(newText) < len(text):
                lineEdit.setCursorPosition(max(cur - 1, 0))


class LabviewDoubleSpinBox(QDoubleSpinBox, ParameterWidget):
    """Implements handling of arrow key events based on curser position similar as in LabView."""

    def __init__(self, indicator: bool = False, displayDecimals: int = 2) -> None:
        """Initialize a LabviewDoubleSpinBox.

        :param indicator: Indicators will be read only, defaults to False
        :type indicator: bool, optional
        :param displayDecimals: Number of displayed decimal points, defaults to 2
        :type displayDecimals: int, optional
        """
        self.NAN = 'NaN'
        self._is_nan = False
        super().__init__()
        self.indicator = indicator
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setRange(-np.inf, np.inf)  # limit explicitly if needed, this seems more useful than the [0, 100] default range
        self.setDisplayDecimals(displayDecimals)
        if indicator:
            self.setReadOnly(True)
            self.preciseValue = 0

    def keyPressEvent(self, event: QKeyEvent) -> None:  # pylint: disable = missing-param-doc
        """Allow to manually rest a value from NaN."""
        if Qt.Key.Key_0 <= event.key() <= Qt.Key.Key_9 or event.key() == Qt.Key.Key_Minus:
            self._is_nan = False
        super().keyPressEvent(event)

    def contextMenuEvent(self, e: 'QContextMenuEvent | None') -> None:  # pylint: disable = missing-param-doc, missing-type-doc
        """Do not use context menu for indicators."""
        if e and self.indicator:
            e.ignore()
        else:
            super().contextMenuEvent(e)

    def setDisplayDecimals(self, displayDecimals: int) -> None:
        """Set display precision.

        :param displayDecimals: Number of displayed decimals.
        :type displayDecimals: int
        """
        # decimals used for display.
        self.displayDecimals = displayDecimals
        # keep internal precision higher if explicitly defined. ensure minimum precision corresponds to display
        self.setDecimals(max(self.displayDecimals, self.decimals()))
        self.setValue(self.value())

    def valueFromText(self, text: 'str | None') -> float:
        """Convert text to value.

        :param text: Input text.
        :type text: str
        :return: value
        :rtype: float
        """
        if text and not self._is_nan:
            return float(text)
        return np.nan

    def validate(self, input: 'str | None', pos: int) -> tuple[QValidator.State, str, int]:  # pylint: disable = missing-param-doc, missing-function-docstring, missing-type-doc  # noqa: A002, D102
        if self._is_nan:
            return (QValidator.State.Acceptable, self.NAN, pos)
        return super().validate(input, pos)

    def textFromValue(self, v: float) -> str:  # pylint: disable = missing-param-doc, missing-type-doc
        """Make sure nan and inf will be represented by NaN."""
        if np.isnan(v) or np.isinf(v):
            return self.NAN
        return f'{v:.{self.displayDecimals}f}'

    def value(self) -> float:
        """Return nan instead of trying convert it."""
        if self._is_nan:
            return np.nan
        return super().value()

    def setValue(self, val: float) -> None:  # pylint: disable = missing-param-doc, missing-type-doc
        """Display nan and inf as text."""
        lineEdit = self.lineEdit()
        if (np.isnan(val) or np.isinf(val)) and lineEdit:
            self._is_nan = True
            lineEdit.setText(self.NAN)  # needed in rare cases where setting to nan would set to maximum
        else:
            self._is_nan = False
            super().setValue(val)

    def wheelEvent(self, e: 'QWheelEvent | None') -> None:  # pylint: disable = missing-param-doc, missing-type-doc
        """Overwrite to disable accidental change of values via the mouse wheel."""
        if e:
            e.ignore()

    def stepBy(self, steps: int) -> None:  # pylint: disable = missing-param-doc, missing-type-doc  # noqa: C901, PLR0912
        """Handle stepping value depending con caret position. This implementation works with negative numbers and of number of digits before the dot."""
        if self._is_nan:
            return
        lineEdit = self.lineEdit()
        if lineEdit:
            text = lineEdit.text()
            cur = lineEdit.cursorPosition()
            dig = len(text.strip('-').split('.')[0])
            # first digit
            if cur <= 1 or (cur <= 2 and '-' in text):  # noqa: PLR2004
                pos = dig - 1
            # digits before decimal
            elif cur < dig and '-' not in text:
                pos = dig - cur
            elif cur < dig + 1 and '-' in text:
                pos = dig - cur + 1
            # last digit before decimal
            elif (cur == dig and '-' not in text) or (cur == dig + 1 and '-' in text):
                pos = 0
            # first digit after decimal
            elif (cur == dig + 1 and '-' not in text) or (cur == dig + 2 and '-' in text):
                pos = -1
            # remaining digits after decimal
            else:
                pos = dig - cur + 2 if '-' in text else dig - cur + 1
            val = self.value() + 10**pos * steps  # use steps for sign
            self.setValue(val)
            # keep cursor position fixed relative to .
            newText = lineEdit.text()
            if len(newText) > len(text):
                if cur == 0 and '-' not in text:
                    lineEdit.setCursorPosition(2)
                elif cur <= 1 and '-' in text:
                    lineEdit.setCursorPosition(3)
                else:
                    lineEdit.setCursorPosition(cur + 1)
            elif len(newText) < len(text):
                lineEdit.setCursorPosition(max(cur - 1, 0))


class LabviewSciSpinBox(LabviewDoubleSpinBox):
    """Spinbox for scientific notation."""

    # inspired by https://gist.github.com/jdreaver/0be2e44981159d0854f5
    # Regular expression to find floats. Match groups are the whole string, the
    # whole coefficient, the decimal part of the coefficient, and the exponent part.

    class FloatValidator(QValidator):
        """Validates input for correct scientific notation."""

        _float_re = re.compile(r'(([+-]?\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?)')

        def valid_float_string(self, string):  # pylint: disable = missing-param-doc, missing-type-doc, missing-function-docstring  # noqa: ANN001, ANN201, D102
            match = self._float_re.search(string)
            return match.groups()[0] == string if match else False

        def validate(self, a0, a1):  # pylint: disable = missing-param-doc, missing-function-docstring, missing-type-doc  # noqa: ANN001, ANN201, D102
            # a0 is string, a1 is position
            if a0 and self.valid_float_string(a0):
                return self.State.Acceptable, a0, a1
            if a0 and (not a0 or a0[a1 - 1] in 'e.-+'):
                return self.State.Intermediate, a0, a1
            return self.State.Invalid, a0 or '', a1

        def fixup(self, a0):  # pylint: disable = missing-param-doc, missing-function-docstring, missing-type-doc  # noqa: ANN001, ANN201, D102
            if a0:
                match = self._float_re.search(a0)
                return match.groups()[0] if match else ''
            return ''

    def __init__(self, indicator: bool = False, displayDecimals: int = 2) -> None:
        """Initialize a LabviewSciSpinBox.

        :param indicator: Indicators will be read only, defaults to False
        :type indicator: bool, optional
        :param displayDecimals: Number of displayed decimals, defaults to 2
        :type displayDecimals: int, optional
        """
        self.validator = self.FloatValidator()
        super().__init__(indicator=indicator, displayDecimals=displayDecimals)
        self.setDecimals(1000)  # need this to allow internal handling of data as floats 1E-20 = 0.0000000000000000001

    def validate(self, input: 'str | None', pos: int) -> tuple[QValidator.State, str, int]:  # pylint: disable = missing-param-doc, missing-function-docstring, missing-type-doc  # noqa: A002, D102
        if self._is_nan:
            return (QValidator.State.Acceptable, self.NAN, pos)
        return self.validator.validate(input, pos)

    def fixup(self, str: 'str | None') -> str:  # pylint: disable = missing-param-doc, missing-function-docstring, missing-type-doc  # noqa: A002, D102
        return self.validator.fixup(str)

    def textFromValue(self, v: float) -> str:  # noqa: D102
        if np.isnan(v) or np.isinf(v):
            return self.NAN
        return f'{v:.{self.displayDecimals}E}'.replace('E-0', 'E-')

    DIGITS_BEFORE_DOT = 1

    def stepBy(self, steps: int) -> None:  # noqa: D102
        if self.text() == self.NAN:
            return
        lineEdit = self.lineEdit()
        if lineEdit:
            text = lineEdit.text()
            cur = lineEdit.cursorPosition()
            v, p = text.split('E')
            sign = '-' if p[0] == '-' else ''
            pot = ''.join(list(p[1:].lstrip('0')))
            pot = pot or '0'
            if (cur <= self.DIGITS_BEFORE_DOT + 1 and '-' not in v) or (cur <= self.DIGITS_BEFORE_DOT + 2 and '-' in v):
                pos = 0  # step by 1
            else:  # right of dot
                pos = self.DIGITS_BEFORE_DOT + 2 - cur if '-' in v else self.DIGITS_BEFORE_DOT + 1 - cur  # step by 0.1, 0.01, ...
            self.setValue(float(str(float(v) + 10**pos * steps) + 'E' + sign + pot))
            # keep cursor position fixed relative to .
            newText = lineEdit.text()
            if len(newText) > len(text):
                if cur == 0 and '-' not in v:
                    lineEdit.setCursorPosition(2)
                elif cur <= 1 and '-' in v:
                    lineEdit.setCursorPosition(3)
                else:
                    lineEdit.setCursorPosition(cur + 1)
            elif len(newText) < len(text):
                lineEdit.setCursorPosition(max(cur - 1, 0))


class ControlCursor(Cursor):
    """Extending internal implementation to get draggable cursor."""

    # based on https://matplotlib.org/3.5.0/gallery/misc/cursor_demo.html
    def __init__(self, ax: Axes, color: 'ColorType', **kwargs) -> None:
        """Initialize a ControlCursor.

        :param ax: The axis on which the cursor is drawn.
        :type ax: mpl.axes.Axes
        :param color: Cursor color.
        :type color: mpl.typing.ColorType
        """
        self.ax = ax
        super().__init__(ax, **kwargs)
        self.lineh.set_color(color)
        self.linev.set_color(color)

    def onmove(self, event) -> None:  # noqa: ANN001, D102
        pass

    def ondrag(self, event) -> None:  # pylint: disable = missing-param-doc, missing-type-doc  # noqa: ANN001
        """Continuously updates cursor position."""
        if event.button == MouseButton.LEFT and kb.is_pressed('ctrl') and event.xdata is not None:
            super().onmove(event)

    def setPosition(self, x, y) -> None:  # pylint: disable = missing-param-doc, missing-type-doc  # noqa: ANN001
        """Emulated mouse event to set position."""
        [xpix, ypix] = self.ax.transData.transform((x, y))
        event = MouseEvent(name='', canvas=self.ax.figure.canvas, x=xpix, y=ypix, button=MouseButton.LEFT)  # type: ignore  # noqa: PGH003
        super().onmove(event)

    def getPosition(self):  # noqa: ANN201
        """Get current position."""
        return self.linev.get_data()[0][0], self.lineh.get_data()[1][1]  # type: ignore  # noqa: PGH003

    def updatePosition(self) -> None:
        """Update position. Usually required to reset position after cursors have been lost by drawing other elements."""
        self.setPosition(*self.getPosition())


class RestoreNumberComboBox(QComboBox):
    """ComboBox that allows to restore its value upon restart using an internal :class:`~esibd.core.Setting`."""

    def __init__(self, parentPlugin: 'Plugin', default: str, items: str, attr: str, use_float: bool = True, **kwargs) -> None:
        """Initialize a RestoreFloatComboBox.

        :param parentPlugin: Parent plugin.
        :type parentPlugin: Plugin
        :param default: Default value.
        :type default: str
        :param items: Combo box items as comma separated string.
        :type items: str
        :param attr: Attribute used to save and restore.
        :type attr: str
        """
        super().__init__(parent=parentPlugin)
        self.parentPlugin = parentPlugin
        self.print = self.parentPlugin.print
        self.attr = attr
        self.fullName = f'{self.parentPlugin.name}/{self.attr}'
        self.parentPlugin.pluginManager.Settings.loading = True
        self.setting = Setting(parameterParent=self.parentPlugin.pluginManager.Settings, name=self.fullName,
                                parameterType=PARAMETERTYPE.FLOATCOMBO if use_float else PARAMETERTYPE.INTCOMBO,
                               value=qSet.value(self.fullName, default), default=default,
                                items=items, widget=self, internal=True, **kwargs)
        self.parentPlugin.pluginManager.Settings.loading = False


class RestoreIntComboBox(RestoreNumberComboBox):
    """ComboBox that allows to restore its value upon restart using an internal :class:`~esibd.core.Setting`."""

    def __init__(self, **kwargs) -> None:
        """Specify use of int."""
        super().__init__(**kwargs, use_float=False)


class RestoreFloatComboBox(RestoreNumberComboBox):
    """ComboBox that allows to restore its value upon restart using an internal :class:`~esibd.core.Setting`."""

    def __init__(self, **kwargs) -> None:
        """Specify use of float."""
        super().__init__(**kwargs, use_float=True)


class Label(QLabel, ParameterWidget):
    """QLabel as ParameterWidget."""


class PushButton(QPushButton, ParameterWidget):
    """PushButton as ParameterWidget."""


class ColorButton(pg.ColorButton, ParameterWidget):
    """ColorButton as ParameterWidget."""


class TreeWidgetItem(QTreeWidgetItem):
    """TreeWidgetItem with path_info."""

    path_info: Path
    obj: 'QObject | Any'


class CheckBox(QCheckBox, ParameterWidget):
    """Allows to set values in a widget in a consistent way."""

    checkBoxHeight: int

    class SignalCommunicate(QObject):
        """Bundle pyqtSignals."""

        setValueFromThreadSignal = pyqtSignal(bool)

    def __init__(self) -> None:
        """Initialize a CheckBox."""
        super().__init__()
        self.signalComm = self.SignalCommunicate()
        self.signalComm.setValueFromThreadSignal.connect(self.setValue)

    def setValue(self, value: bool) -> None:
        """Set value using consistent API for Parameter widgets.

        :param value: The new checked state.
        :type value: bool
        """
        self.setChecked(value)


class ToolButton(QToolButton, ParameterWidget):
    """Allows to set values in a widget in a consistent way."""

    class SignalCommunicate(QObject):
        """Bundle pyqtSignals."""

        setValueFromThreadSignal = pyqtSignal(bool)

    def __init__(self) -> None:
        """Initialize a ToolButton."""
        super().__init__()
        self.signalComm = self.SignalCommunicate()
        self.signalComm.setValueFromThreadSignal.connect(self.setValue)

    def setValue(self, value: bool) -> None:
        """Set value using consistent API for parameter widgets.

        :param value: New checked state.
        :type value: bool
        """
        self.setChecked(value)


class Icon(QIcon):
    """QIcon that allows to save the icon file name. Allows to reuse icon elsewhere, e.g., for html about dialog."""

    def __init__(self, file: str | Path, pixmap: 'QPixmap | None' = None, desaturate: bool = False) -> None:
        """Initialize and Icon.

        :param file: Path to icon file.
        :type file: str | Path
        :param pixmap: Pixmap to use for icon, defaults to None
        :type pixmap: QPixmap, optional
        :param desaturate: Will turn icon to gray scale if True, defaults to False
        :type desaturate: bool, optional
        """
        if isinstance(file, Path):
            file = file.as_posix()
        if desaturate:
            image = Image.open(file).convert('RGBA')
            r, g, b, a = image.split()
            grayscale = Image.merge('RGB', (r, g, b)).convert('L')
            pixmap = QPixmap.fromImage(ImageQt(Image.merge('RGBA', (grayscale, grayscale, grayscale, a))))
        if not pixmap:
            super().__init__(file)
        else:
            super().__init__(pixmap)
        self.fileName = file  # remember for later access


@dataclass
class ContextAction:
    """An action that can be added to a context menu."""

    text: str
    event: Callable
    action: 'QAction | None' = None


class Action(QAction):
    """An Action that allows to change values from another thread and retains a reference to icon an toolTip."""

    fileName: str

    class SignalCommunicate(QObject):
        """Bundle pyqtSignals."""

        setValueFromThreadSignal = pyqtSignal(bool)

    def __init__(self, icon: Icon, toolTip: str, parentPlugin: 'Plugin') -> None:
        """Initialize an Action.

        :param icon: Action icon.
        :type icon: Icon
        :param toolTip: Action tooltip.
        :type toolTip: str
        :param parentPlugin: Action parent.
        :type parentPlugin: esibd.plugins.Plugin
        """
        super().__init__(icon, toolTip, parentPlugin)
        self._icon = icon
        self._toolTip = toolTip
        self.signalComm = self.SignalCommunicate()
        self.signalComm.setValueFromThreadSignal.connect(self.setValue)

    @property
    def state(self) -> bool:
        """Return the state. API consistent with other Action classes."""
        return self.isChecked()

    @state.setter
    def state(self, state: bool) -> None:
        self.setChecked(state)

    def getIcon(self) -> Icon:
        """Get the icon. API consistent with other Action classes."""
        return self._icon

    def getToolTip(self) -> str:
        """Get the tooltip. API consistent with other Action classes."""
        return self._toolTip

    def setValue(self, value: bool) -> None:
        """Set action state. API consistent with other Action classes.

        :param value: New state.
        :type value: bool
        """
        self.setChecked(value)


class StateAction(Action):
    """Show different icons depending on a state.

    Values are restored using QSettings if name is provided.
    """

    def __init__(self, parentPlugin: 'Plugin', toolTipFalse: str = '', *, iconFalse: Icon, toolTipTrue: str = '', iconTrue: 'Icon | None' = None, event: 'Callable | None' = None,  # noqa: PLR0913
                 before: 'QAction | None' = None, attr: str = '', restore: bool = True, defaultState: bool = False) -> None:
        """Initialize a StateAction.

        :param parentPlugin: Action parent.
        :type parentPlugin: Plugin
        :param toolTipFalse: ToolTip for False state, defaults to ''
        :type toolTipFalse: str, optional
        :param iconFalse: Icon for False state, defaults to None
        :type iconFalse: QIcon, optional
        :param toolTipTrue: ToolTip for True state, defaults to ''
        :type toolTipTrue: str, optional
        :param iconTrue: Icon for True state, defaults to None
        :type iconTrue: QIcon, optional
        :param event: Action event, defaults to None
        :type event: Callable | None, optional
        :param attr: Attribute used to save and restore state, defaults to ''
        :type attr: str | None, optional
        :param restore: Restore value if True, defaults to True
        :type restore: bool, optional
        :param defaultState: Used unless changed by user, defaults to False
        :type defaultState: bool, optional
        """
        super().__init__(iconFalse, toolTipFalse, parentPlugin)
        self.parentPlugin = parentPlugin
        self.iconFalse = iconFalse
        self.toolTipFalse = toolTipFalse
        self.iconTrue = iconTrue or iconFalse
        self.toolTipTrue = toolTipTrue or toolTipFalse
        self.setCheckable(True)
        self.toggled.connect(self.updateIcon)
        self.setToolTip(self.toolTipFalse)
        self.attr = attr
        self.fullName = ''
        if self.attr:
            self.fullName = f'{self.parentPlugin.name}/{self.attr}'
            self.setObjectName(self.fullName)
        else:
            self.setObjectName(f'{self.parentPlugin.name}/{self.toolTipFalse}')
        if event:
            self.triggered.connect(event)
        if restore and self.fullName:
            self.state = qSet.value(self.fullName, defaultValue=defaultState, type=bool)
        else:
            self.state = False  # init
        if self.parentPlugin.titleBar:
            if before:
                self.parentPlugin.titleBar.insertAction(before, self)
            else:
                self.parentPlugin.titleBar.addAction(self)

    def toggle(self) -> bool:
        """Toggles the state."""
        self.state = not self.state
        return self.state

    @property
    def state(self) -> bool:
        """Returns the state."""
        return self.isChecked()

    @state.setter
    def state(self, state: bool) -> None:
        """Set the state.

        :param state: The state to be set.
        :type state: bool
        """
        self.setChecked(state)

    def updateIcon(self, state: bool) -> None:
        """Update icon and icon toolTip.

        :param state: The state for which icon and toolTip should be set.
        :type state: bool
        """
        if self.fullName:
            qSet.setValue(self.fullName, self.state)
        self.setIcon(self.iconTrue if state else self.iconFalse)
        self.setToolTip(self.toolTipTrue if state else self.toolTipFalse)

    def getIcon(self) -> Icon:
        """Get the icon. API consistent with other Action classes."""
        return self.iconTrue if self.state else self.iconFalse

    def getToolTip(self) -> str:
        """Get the tooltip. API consistent with other Action classes."""
        return self.toolTipTrue if self.state else self.toolTipFalse

    def setValue(self, value: bool) -> None:
        """Set action state. API consistent with other Action classes.

        :param value: New state.
        :type value: bool
        """
        self.state = value


@dataclass
class MultiState:
    """Represents a state of a MultiStateAction including label, toolTip and icon."""

    label: Enum
    toolTip: str
    icon: Icon


class MultiStateAction(Action):
    """Show different icons depending on multiple states.

    Values are restored using QSettings if name is provided.
    """

    _state: int

    def __init__(self, parentPlugin: 'Plugin', states: list[MultiState], event: 'Callable | None' = None, before: 'QAction | None' = None,  # noqa: PLR0913, PLR0917
                 attr: str = '', restore: bool = True, defaultState: int = 0) -> None:
        """Initialize a MultiStateAction.

        :param parentPlugin: Action parent.
        :type parentPlugin: Plugin
        :param states: Action states, defaults to None
        :type states: list[MultiState] | None, optional
        :param event: Action event, defaults to None
        :type event: Callable | None, optional
        :param before: Place left of existing action if provided, defaults to None
        :type before: QAction, optional
        :param attr: Used to save and restore state, defaults to ''
        :type attr: str, optional
        :param restore: Value will be restored from registry if True, defaults to True
        :type restore: bool, optional
        :param defaultState: Will be used unless changed by user, defaults to 0
        :type defaultState: int, optional
        """
        super().__init__(states[0].icon, states[0].toolTip, parentPlugin)
        self.parentPlugin = parentPlugin
        self.states = states
        self.setToolTip(states[0].toolTip)
        self.attr = attr
        self.fullName = ''
        if self.attr:
            self.fullName = f'{self.parentPlugin.name}/{self.attr}'
            self.setObjectName(self.fullName)
        else:
            self.setObjectName(f'{self.parentPlugin.name}/{states[0].toolTip}')
        if event:
            self.triggered.connect(lambda: (self.rollState(), event()))
        if restore and self.fullName:
            # use explicit int conversion instead of type=int in qSet.value for backward compatibility
            self._state = min(int(qSet.value(self.fullName, defaultState)), len(states) - 1)
        else:
            self._state = 0  # init
        self.updateIcon()
        if self.parentPlugin.titleBar:
            if before:
                self.parentPlugin.titleBar.insertAction(before, self)
            else:
                self.parentPlugin.titleBar.addAction(self)

    def stateFromEnum(self, state: Enum) -> int:
        """Return state corresponding to provided label.

        :param state: State Enum.
        :type state: Enum
        :return: Index of corresponding state, defaults to 0.
        :rtype: int
        """
        return next((i for i in range(len(self.states)) if self.states[i].label == state), 0)

    def rollState(self) -> None:
        """Roll to next state."""
        # state should be int but np.mod returns np.int64 which is causing issues with QSettings
        self._state = int(np.mod(self._state + 1, len(self.states), dtype=int))
        self.updateIcon()

    @property
    def state(self) -> Enum:  # use labels for api
        """Label representation of current state."""
        return self.states[self._state].label if self._state < len(self.states) else self.states[0].label

    @state.setter
    def state(self, state: Enum) -> None:
        self._state = self.stateFromEnum(state)

    def updateIcon(self) -> None:
        """Update icon and icon toolTip based on current state."""
        if self.fullName:
            qSet.setValue(self.fullName, self._state)  # store state as int
        self.setIcon(self.getIcon())
        self.setToolTip(self.getToolTip())

    def getIcon(self) -> Icon:
        """Get the icon. API consistent with other Action classes."""
        return self.states[self._state].icon

    def getToolTip(self) -> str:
        """Get the tooltip. API consistent with other Action classes."""
        return self.states[self._state].toolTip

    def setValue(self, value: Enum) -> None:
        """Set action state. API consistent with other Action classes.

        :param value: A valid label corresponding to one of the defined states.
        :type value: Enum
        """
        self._state = self.stateFromEnum(value)


class CompactComboBox(QComboBox, ParameterWidget):
    """Combobox that stays small while showing full content in dropdown menus."""

    MAX_WIDTH_LIMIT = 5000

    # from JonB at https://forum.qt.io/post/542594
    def showPopup(self) -> None:
        """Show popup after setting minimum width."""
        # we like the popup to always show the full contents
        # we only need to do work for this when the combo has had a maximum width specified
        maxWidth = self.maximumWidth()
        # see https://doc.qt.io/qt-5/qwidget.html#maximumWidth-prop for the 16777215 value
        if maxWidth and maxWidth < self.MAX_WIDTH_LIMIT:
            self.setPopupMinimumWidthForItems()

        # call the base method now to display the popup
        super().showPopup()

    def setPopupMinimumWidthForItems(self) -> None:
        """Calculate and sets the minimum combobox width."""
        # we like the popup to always show the full contents
        # under Linux/GNOME popups always do this
        # but under Windows they get truncated
        # here we calculate the maximum width among the items
        # and set QComboBox.view() to accommodate this
        # which makes items show full width under Windows
        view = self.view()
        if view and self.count() > 0:
            fm = self.fontMetrics()
            maxWidth = max(fm.size(Qt.TextFlag.TextSingleLine, self.itemText(i)).width() for i in range(self.count())) + 50  # account for scrollbar and margins
            if maxWidth:
                view.setMinimumWidth(maxWidth)

    def wheelEvent(self, e: 'QWheelEvent | None') -> None:  # noqa: ARG002 pylint: disable = missing-param-doc
        """Ignore wheel event."""
        return


class DockWidget(QDockWidget):
    """DockWidget with custom title bar allows to intercept the close and float events triggered by user."""

    # future desired features:
    # - floating docks should be able to be maximized/minimized and appear as separate windows of the same software in task bar
    # floating windows should not disappear when dragged below taskbar but jump back as normal windows
    # - some of these are possible with pyqtgraph but this introduces other limitations and bugs
    # Open bug: https://bugreports.qt.io/browse/QTBUG-118578 see also  https://stackoverflow.com/questions/77340981/how-to-prevent-crash-with-qdockwidget-and-custom-titlebar

    class SignalCommunicate(QObject):
        """Bundle pyqtSignals."""

        dockClosingSignal = pyqtSignal()

    def __init__(self, parentPlugin: 'Plugin') -> None:
        """Initialize a DockWidget.

        :param parentPlugin: Dock parent plugin.
        :type parentPlugin: Plugin
        """
        self.parentPlugin = parentPlugin
        self.title = self.parentPlugin.name
        if hasattr(self.parentPlugin, 'parentPlugin'):
            self.title = self.parentPlugin.parentPlugin.name
        if hasattr(self.parentPlugin, 'scan') and self.parentPlugin.scan:
            self.title = self.parentPlugin.scan.name
        super().__init__(self.title, self.parentPlugin.app.mainWindow)
        self.signalComm = self.SignalCommunicate()
        self.signalComm.dockClosingSignal.connect(self.parentPlugin.closeGUI)
        self.setObjectName(f'{self.parentPlugin.pluginType}_{self.parentPlugin.name}')  # essential to make restoreState work!
        self.setTitleBarWidget(parentPlugin.titleBar)
        self.topLevelChanged.connect(self.on_top_level_changed)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable)  # | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.setWidget(self.parentPlugin.mainDisplayWidget)
        self.resizeTimer = QTimer()
        self.resizeTimer.setSingleShot(True)
        self.resizeTimer.timeout.connect(self.resetResize)
        self.resizeTimer.setInterval(100)
        self._pending_resize_event = None

    def resizeEvent(self, a0: 'QResizeEvent | None') -> None:  # pylint: disable = missing-param-doc
        """Temporary set resizing flag."""
        if self.parentPlugin.pluginManager.loading or self.parentPlugin.pluginManager.finalizing:
            super().resizeEvent(a0)
            return
        if not self.resizeTimer.isActive():
            self.parentPlugin.resizing = True
            if isinstance(self.parentPlugin, self.parentPlugin.pluginManager.LiveDisplay) and self.parentPlugin.plotSplitter:
                self.parentPlugin.plotSplitter.setVisible(False)

        self._pending_resize_event = a0  # Store for later
        self.resizeTimer.start()  # start or restart to delay timeout

    def resetResize(self) -> None:
        """Reset resizing flag."""
        if self._pending_resize_event:
            super().resizeEvent(self._pending_resize_event)
            self._pending_resize_event = None
        if isinstance(self.parentPlugin, self.parentPlugin.pluginManager.LiveDisplay) and self.parentPlugin.plotSplitter:
            self.parentPlugin.plotSplitter.setVisible(True)
        self.parentPlugin.resizing = False

    def on_top_level_changed(self) -> None:
        """Update toolbars after dragging plugins to new location."""
        # there are no signals available to be emitted at the end of dragging or when tabifying.
        # for now I am just using a long delay and hope that the operation has been completed before toggleTitleBar is called
        if not self.parentPlugin.pluginManager.finalizing and not self.parentPlugin.pluginManager.loading:
            self.parentPlugin.pluginManager.toggleTitleBarDelayed(update=True, delay=3000)

    def toggleTitleBar(self) -> None:  # noqa: C901, PLR0912
        """Update titleBar as dock is changing from floating to docked states."""
        parent = self.parent()
        parent = cast('EsibdExplorer', parent) if isinstance(parent, EsibdExplorer) else cast('QWidget', parent)
        if self.parentPlugin.initializedDock:  # may have changed between toggleTitleBarDelayed and toggleTitleBar  # noqa: PLR1702
            if self.isFloating():  # dock is floating on its own
                if self.parentPlugin.titleBarLabel:
                    self.parentPlugin.titleBarLabel.setText(self.title)
                if hasattr(self.parentPlugin, 'floatAction'):
                    self.parentPlugin.floatAction.state = True
            else:  # dock is inside the mainWindow or an external window
                if hasattr(self.parentPlugin, 'floatAction'):
                    self.parentPlugin.floatAction.state = False
                    # do not allow to float from external windows as this causes GUI instabilities (empty external windows, crash without error, ...)
                    # need to allow float to leave external window -> need to make safe / dragging using standard titleBar works but not using custom titleBar
                if hasattr(self.parentPlugin, 'titleBarLabel') and self.parentPlugin.titleBarLabel:
                    self.parentPlugin.titleBarLabel.setText(self.title)  # need to apply for proper resizing, even if set to '' next
                    if isinstance(parent, EsibdExplorer) and len(parent.tabifiedDockWidgets(self)) > 0:
                        self.parentPlugin.titleBarLabel.setText('')
                    if self.parentPlugin.pluginManager.tabBars:  # might be null if there are no tabbed docks
                        for tabBar in self.parentPlugin.pluginManager.tabBars:
                            for i in range(tabBar.count()):
                                if tabBar.tabText(i) == self.title:
                                    tabBar.setTabIcon(i, QIcon() if getIconMode() == 'Labels' else self.parentPlugin.getIcon())
                                    # if getIconMode() == 'Icons':
                                    #     tabBar.tabIcon(i).setToolTip(self.title) cannot assign tooltip
                if not isinstance(parent, EsibdExplorer):
                    parent.setStyleSheet(self.parentPlugin.pluginManager.styleSheet)  # use same separators as in main window

    def closeEvent(self, event: 'QCloseEvent | None') -> None:  # pylint: disable = missing-param-doc, missing-type-doc
        """Close the GUI when the dock is closing."""
        self.signalComm.dockClosingSignal.emit()
        super().closeEvent(event)


class TreeWidget(QTreeWidget):
    """A TreeWidget with extended options to control its height."""

    def __init__(self, minimizeHeight: bool = False) -> None:
        """Initialize a TreeWidget.

        :param minimizeHeight: Will use minimal height Hint if True, defaults to False
        :type minimizeHeight: bool, optional
        """
        super().__init__()
        self.minimizeHeight = minimizeHeight

    def item_depth(self, item: QTreeWidgetItem) -> int:
        """Recursively count item depth from root (root = 0).

        :param item: The item for which to return the depth.
        :type item: QTreeWidgetItem
        :return: item depth
        :rtype: int
        """
        depth = 0
        while item.parent():
            item = cast('QTreeWidgetItem', item.parent())
            depth += 1
        return depth

    def expandAllItems(self, depth: int = 1) -> None:
        """Expand all Items up to a given level.

        :param depth: Limit expansion depth to prevent infinite loops (e.g. channel.device.channel.device...), defaults to 1
        :type depth: int, optional
        """
        # expand all categories
        it = QTreeWidgetItemIterator(self, QTreeWidgetItemIterator.IteratorFlag.HasChildren)
        while it.value():
            value = it.value()
            if value and self.item_depth(value) < depth:
                value.setExpanded(True)
            it += 1

    def totalItems(self) -> int:
        """Total number of items at top level and first child level."""
        total_items = 0
        for i in range(self.topLevelItemCount()):
            top_item = self.topLevelItem(i)
            if top_item:
                total_items += 1  # Count the top-level item
                total_items += top_item.childCount()  # Add the count of its children
        return total_items

    def totalHeight(self, limit: int = 0) -> int:
        """Total height of all items.

        :param limit: Limit the number of items considered, defaults to 0
        :type limit: int, optional
        :return: Total height of all considered items.
        :rtype: int
        """
        header = self.header()
        count = 0
        if header:
            total_height = header.height()
            for i in range(self.topLevelItemCount()):
                top_item = self.topLevelItem(i)
                if top_item:
                    total_height += self.visualItemRect(top_item).height()
                    count += 1
                    if limit > 0 and count == limit:
                        return total_height
                    for j in range(top_item.childCount()):
                        total_height += self.visualItemRect(top_item.child(j)).height()
                        count += 1
                        if limit > 0 and count == limit:
                            return total_height
            return total_height
        return 0

    def itemWidth(self) -> int:
        """Width of fist visible item."""
        if self.topLevelItemCount() > 0:
            for i in range(self.topLevelItemCount()):
                if self.visualItemRect(self.topLevelItem(i)).width() > 0:  # ignore hidden channels
                    return self.visualItemRect(self.topLevelItem(i)).width()
        return 300

    def tree_height_hint_complete(self) -> int:
        """Calculate the complete height, corresponding to all items."""
        item_height = self.visualItemRect(self.topLevelItem(0)).height() if self.topLevelItemCount() > 0 else 12
        header = self.header()
        return header.height() + self.totalItems() * item_height + 10 if header else 200

    def tree_height_hint_minimal(self) -> int:
        """Calculate the minimal height, corresponding to up to 6 items."""
        return self.totalHeight(limit=6)

    def count_child_items(self, item: QTreeWidgetItem) -> int:
        """Count the number of child items of a QTreeWidgetItem.

        :param item: The item of which the children should be counted.
        :type item: QTreeWidgetItem
        :return: The child count.
        :rtype: int
        """
        count = item.childCount()
        for i in range(item.childCount()):
            child = item.child(i)
            if child:
                count += self.count_child_items(child)
        return count

    def itemRect(self) -> QRect:
        """Return the QRect of all visible items."""
        return QRect(self.rect().left(), self.rect().top(), min(self.rect().width(), self.itemWidth()), min(self.rect().height(), self.totalHeight()))

    def grabItems(self) -> QPixmap:
        """Grab a QPixmap of the items."""
        return self.grab(self.itemRect())

    def sizeHint(self) -> QSize:
        """SizeHint with custom minimal or complete height."""
        return QSize(self.width(), self.tree_height_hint_minimal() if self.minimizeHeight else self.tree_height_hint_complete())


class LedIndicator(QAbstractButton, ParameterWidget):
    """Simple custom LED indicator."""

    # inspired by https://github.com/nlamprian/pyqt5-led-indicator-widget/blob/master/LedIndicatorWidget.py
    scaledSize = 1000.0

    def __init__(self) -> None:
        """Initialize a LedIndicator."""
        super().__init__()

        self.setMinimumSize(20, 20)
        self.setMaximumSize(20, 20)
        self.setCheckable(True)
        self.setEnabled(True)  # necessary to trigger context menu -> overwrite mousePressEvent instead of self.setEnabled(False)

        # Green
        self.on_color = QColor(0, 220, 0)
        self.off_color = QColor(0, 60, 0)

    def mousePressEvent(self, e: 'QMouseEvent | None') -> None:  # pylint: disable = unused-argument, missing-function-docstring, missing-param-doc
        """Allow to open context menu while preventing change of LedIndicator state by user."""
        if e and e.button() == Qt.MouseButton.RightButton:
            super().mousePressEvent(e)

    def resizeEvent(self, a0: 'QResizeEvent | None') -> None:  # pylint: disable = unused-argument, missing-function-docstring  # matching standard signature  # noqa: ARG002, D102
        self.update()

    def paintEvent(self, e: 'QPaintEvent | None') -> None:  # pylint: disable = unused-argument, missing-function-docstring  # matching standard signature  # noqa: ARG002, D102
        realSize = min(self.width(), self.height())

        painter = QPainter(self)
        pen = QPen(Qt.GlobalColor.black)
        pen.setWidth(4)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.scale(realSize / self.scaledSize, realSize / self.scaledSize)

        # paint outer ring
        gradient = QRadialGradient(QPointF(-500, -500), 1500, QPointF(-500, -500))
        gradient.setColorAt(0, QColor(224, 224, 224))
        gradient.setColorAt(1, QColor(28, 28, 28))
        painter.setPen(pen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(QPointF(0, 0), 500, 500)

        # paint inner ring
        gradient = QRadialGradient(QPointF(500, 500), 1500, QPointF(500, 500))
        gradient.setColorAt(0, QColor(224, 224, 224))
        gradient.setColorAt(1, QColor(28, 28, 28))
        painter.setPen(pen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(QPointF(0, 0), 450, 450)

        # paint center
        painter.setPen(pen)
        if self.isChecked():
            painter.setBrush(self.on_color)
        else:
            painter.setBrush(self.off_color)
        painter.drawEllipse(QPointF(0, 0), 400, 400)

    @property
    def onColor(self) -> QColor:
        """On color."""
        return self.on_color

    @onColor.setter
    def onColor(self, color: QColor) -> None:
        self.on_color = color

    @property
    def offColor(self) -> QColor:
        """Off color."""
        return self.off_color

    @offColor.setter
    def offColor(self, color: QColor) -> None:
        self.off_color = color


class LineEdit(QLineEdit, ParameterWidget):
    """LineEdit with input validation and custom signal onEditingFinished."""

    # based on https://stackoverflow.com/questions/79309361/prevent-editingfinished-signal-from-qlineedit-after-programmatic-text-update
    userEditingFinished = pyqtSignal()
    allowEmptyText: bool = True
    """If False, empty text is not allowed and will be replaced with last valid text"""
    last_valid_text: str

    def __init__(self, parentParameter: Parameter, tree: 'QTreeWidget | None' = None) -> None:
        """Initialize a LineEdit."""
        super().__init__()
        self._edited = False
        self.parentParameter = parentParameter
        # Regular expression to allow only letters (both upper and lower case), digits, and spaces + mathematical symbols and brackets for equations
        self.valid_chars = valid_chars
        # ! remove the forward slash from all parameters like name that may affect data structure in hdf5 files
        # NOTE: \\/ slashes may cause names to be interpreted as paths but are needed in equations -> not allowed, add / to valid_chars only for equations
        self.tree = tree
        self.editingFinished.connect(self.onEditingFinished)
        self.textEdited.connect(self.onTextEdited)
        self.textChanged.connect(self.onTextChanged)
        self.setMinimumWidth(50)  # Set a reasonable minimum width
        self.max_width = 300

    def onTextEdited(self) -> None:
        """Set flag to indicate text was edited by user but editing is not yet finished, emitted on every keystroke."""
        self._edited = True

    def onTextChanged(self, text: str) -> None:  # noqa: ARG002
        """Validate text and adjust width after text was changed by user or setText.

        :param text: The new text.
        :type text: str
        """
        self.updateGeometry()  # adjust width to text
        self.validateInput()
        if self.text().strip():
            self.last_valid_text = self.text()

    def validateInput(self) -> None:
        """Validate the text and remove invalid characters."""
        current_text = self.text()
        # Remove any character that doesn't match the valid_chars regex
        valid_text = validateText(printParent=self.parentParameter, valid_chars=self.valid_chars, text=current_text).strip()
        if valid_text != current_text:
            self.setText(valid_text)

    def onEditingFinished(self) -> None:
        """Process new value and update tree if applicable after editing was finished by Enter or loosing focus."""
        if not self.text().strip() and not self.allowEmptyText:
            if self.last_valid_text:
                self.parentParameter.print(f'Empty value not allowed, reverting to {self.last_valid_text}', flag=PRINT.WARNING)
                self.setText(self.last_valid_text)
            self._edited = False
        elif self._edited:
            self._edited = False
            if self.tree:
                self.tree.scheduleDelayedItemsLayout()
            self.userEditingFinished.emit()

    def sizeHint(self) -> QSize:
        """Return reasonable size hint based on content within minimum and maximum limits."""
        return QSize(min(max(self.minimumWidth(), QFontMetrics(self.font()).horizontalAdvance(self.text() or ' ') + 10), self.max_width), self.height())


class DebouncedCanvas(FigureCanvas):
    """Figure Canvas that defers drawing until manual resize is complete.

    This makes the GUI more responsive.
    """

    def __init__(self, parentPlugin: 'Plugin', figure: Figure) -> None:  # noqa: D107
        self.parentPlugin = parentPlugin
        super().__init__(figure)

        # Debounce timer
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(100)  # Delay in ms
        self._update_timer.timeout.connect(self._do_update)
        self._has_drawn = False

    def request_update(self) -> None:
        """Restart the timer every time this is called."""
        self._update_timer.start()

    def _do_update(self) -> None:
        """Execute draw_idle once timer has expired."""
        self._has_drawn = True
        self.draw_idle()
        if hasattr(self.parentPlugin, 'scan') and isinstance(self.parentPlugin.scan, self.parentPlugin.pluginManager.Scan):
            self.parentPlugin.scan.defaultLabelPlot()
        else:
            self.parentPlugin.defaultLabelPlot()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, D102
        # Resize still happens normally
        if self._in_resize_event:  # Prevent PyQt6 recursion
            return
        if self.figure is None:
            return
        self._in_resize_event = True
        try:
            w = event.size().width() * self.device_pixel_ratio
            h = event.size().height() * self.device_pixel_ratio
            dpival = self.figure.dpi
            winch = w / dpival
            hinch = h / dpival
            self.figure.set_size_inches(winch, hinch, forward=False)
            # pass back into Qt to let it finish
            QtWidgets.QWidget.resizeEvent(self, event)
            # emit our resize events
            ResizeEvent('resize_event', self)._process()  # type: ignore  # noqa: PGH003, SLF001
            # this will be executed once timer has expired self.draw_idle()
            self._has_drawn = False
            self.request_update()
        finally:
            self._in_resize_event = False

    def paintEvent(self, event) -> None:  # noqa: ANN001, D102
        if not self._has_drawn:
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(colors.bg))  # use global background color while waiting for resize to complete
            painter.end()
        else:
            super().paintEvent(event)


class TextEdit(QPlainTextEdit, ParameterWidget):
    """Editor that is compatible with :class:`~esibd.core.NumberBar`."""

    # based on https://gist.github.com/Axel-Erfurt/8c84b5e70a1faf894879cd2ab99118c2

    def __init__(self) -> None:
        """Initialize a TextEdit."""
        super().__init__()
        self.installEventFilter(self)
        self._completer = None

    def setCompleter(self, c) -> None:  # pylint: disable=missing-function-docstring  # noqa: ANN001, D102
        if self._completer:
            self._completer.activated.disconnect()

        self._completer = c
        c.popup().setStyleSheet('background-color:  #555753; color:  #eeeeec; font-size: 8pt; selection-background-color:  #4e9a06;')

        c.setWidget(self)
        c.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        c.activated.connect(self.insertCompletion)

    def completer(self):  # pylint: disable=missing-function-docstring  # noqa: ANN201, D102
        return self._completer

    def insertCompletion(self, completion):  # pylint: disable=missing-function-docstring  # noqa: ANN001, ANN201, D102
        if not self._completer or self._completer.widget() is not self:
            return

        tc = self.textCursor()
        extra = len(completion) - len(self._completer.completionPrefix())
        tc.movePosition(QTextCursor.MoveOperation.Left)
        tc.movePosition(QTextCursor.MoveOperation.EndOfWord)
        tc.insertText(completion[-extra:])
        self.setTextCursor(tc)

    def textUnderCursor(self):  # pylint: disable=missing-function-docstring  # noqa: ANN201, D102
        tc = self.textCursor()
        tc.select(QTextCursor.SelectionType.WordUnderCursor)
        return tc.selectedText()

    def focusInEvent(self, e):  # pylint: disable=missing-function-docstring  # noqa: ANN001, ANN201, D102
        if self._completer:
            self._completer.setWidget(self)
        super().focusInEvent(e)

    def keyPressEvent(self, e: 'QKeyEvent | None'):  # pylint: disable = missing-function-docstring  # noqa: ANN201, D102
        if e:
            if e.key() == Qt.Key.Key_Tab:
                self.textCursor().insertText('    ')
                return
            if self._completer and self._completer.popup().isVisible() and e.key() in {Qt.Key.Key_Enter, Qt.Key.Key_Return}:
                # The above keys are forwarded by the completer to the widget.
                e.ignore()
                # Let the completer do default behavior.
                return

            isShortcut = ((e.modifiers() & Qt.KeyboardModifier.ControlModifier) != 0 and e.key() == Qt.Key.Key_Escape)
            if not self._completer or not isShortcut:
                # Do not process the shortcut when we have a completer.
                super().keyPressEvent(e)

            ctrlOrShift = e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
            if not self._completer or (ctrlOrShift and len(e.text()) == 0):
                return

            eow = "~!@#$%^&*()_+{}|:\"<>?,./;'[]\\-="
            hasModifier = (e.modifiers() != Qt.KeyboardModifier.NoModifier) and not ctrlOrShift
            completionPrefix = self.textUnderCursor()

            if not isShortcut and (hasModifier or len(e.text()) == 0 or len(completionPrefix) < 2 or e.text()[-1] in eow):  # noqa: PLR2004
                self._completer.popup().hide()
                return

            if completionPrefix != self._completer.completionPrefix():
                self._completer.setCompletionPrefix(completionPrefix)
                self._completer.popup().setCurrentIndex(
                        self._completer.completionModel().index(0, 0))

            cr = self.cursorRect()
            cr.setWidth(self._completer.popup().sizeHintForColumn(0) + self._completer.popup().verticalScrollBar().sizeHint().width())
            self._completer.complete(cr)


class IconStatusBar(QStatusBar):
    """Statusbar that shows an icon in front of the message to indicate the type of message."""

    iconClicked = pyqtSignal(bool)

    def __init__(self) -> None:
        """Initialize an IconStatusBar."""
        super().__init__()
        self.setStyleSheet("""
            QStatusBar { color: transparent; }
            QToolButton#statusBarIconWidget { border: none; }
        """)

        self._iconWidget = QToolButton()
        self._iconWidget.setObjectName('statusBarIconWidget')
        self.addWidget(self._iconWidget)
        # add direct references to the icon functions
        self.icon = self._iconWidget.icon
        self.setIcon = self._iconWidget.setIcon
        # force the button to always show the icon, even if the
        # current style default is different
        self._iconWidget.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

        self.icon_warning = Icon(internalMediaPath / 'unicode_warning.png')
        self.icon_error = Icon(internalMediaPath / 'unicode_error.png')
        self.icon_info = Icon(internalMediaPath / 'unicode_info.png')
        self.icon_debug = Icon(internalMediaPath / 'unicode_bug.png')
        self.icon_verbose = Icon(internalMediaPath / 'icon_V.png')
        self.icon_trace = Icon(internalMediaPath / 'icon_T.png')
        self.icon_explorer = Icon(PROGRAM_ICON)
        self.setIcon(self.icon_explorer)

        self._statusLabel = QLabel()
        self._statusLabel.setMinimumWidth(1)  # allow ignoring the size hint
        self.addWidget(self._statusLabel)

    def showMessage(self, message, msecs=...) -> None:  # noqa: ANN001, ARG002
        """Redirecting message to custom statusbar.

        :param message: The message.
        :type message: str
        :param msecs: Defines message display time. Not used for custom statusbar.
        :type msecs: int, optional
        """
        self._statusLabel.setText(message)

    def setFlag(self, flag: PRINT = PRINT.MESSAGE) -> None:
        """Set the status icon depending on the message flag.

        :param flag: The message flag, defaults to PRINT.MESSAGE
        :type flag: esibd.const.PRINT, optional
        """
        match flag:
            case PRINT.WARNING:
                self.setIcon(self.icon_warning)
            case PRINT.ERROR:
                self.setIcon(self.icon_error)
            case PRINT.EXPLORER:
                self.setIcon(self.icon_explorer)
            case PRINT.DEBUG:
                self.setIcon(self.icon_debug)
            case PRINT.VERBOSE:
                self.setIcon(self.icon_verbose)
            case PRINT.TRACE:
                self.setIcon(self.icon_trace)
            case _:
                self.setIcon(self.icon_info)


class NumberBar(QWidget):
    """A bar that displays line numbers of an associated editor."""

    # based on https://gist.github.com/Axel-Erfurt/8c84b5e70a1faf894879cd2ab99118c2

    def __init__(self, parent: TextEdit) -> None:
        """Initialize NumberBar.

        :param parent: The corresponding editor, defaults to None
        :type parent: TextEdit, optional
        """
        super().__init__(parent)
        self.editor = parent
        self.editor.blockCountChanged.connect(self.update_width)
        self.editor.updateRequest.connect(self.update_on_scroll)
        self.update_width('1')
        self.lineBarColor = Qt.GlobalColor.black

    def updateTheme(self) -> None:
        """Change between dark and light themes."""
        self.lineBarColor = QColor(colors.bg)

    def update_on_scroll(self, rect, scroll) -> None:  # pylint: disable = unused-argument, missing-param-doc, missing-type-doc  # keeping consistent signature  # noqa: ANN001, ARG002
        """Update NumberBar when scrolling in editor."""
        if self.isVisible():
            if scroll:
                self.scroll(0, scroll)
            else:
                self.update()

    def update_width(self, string) -> None:  # pylint: disable = missing-param-doc, missing-type-doc  # noqa: ANN001
        """Adjust with to number of required digits."""
        width = self.fontMetrics().horizontalAdvance(str(string)) + 8  # changed from width to horizontalAdvance
        if self.width() != width:
            self.setFixedWidth(width)

    def paintEvent(self, a0) -> None:  # pylint: disable = missing-function-docstring  # noqa: ANN001, D102
        if self.isVisible() and a0:
            block = self.editor.firstVisibleBlock()
            height = self.fontMetrics().height()
            number = block.blockNumber()
            painter = QPainter(self)
            painter.fillRect(a0.rect(), self.lineBarColor)
            painter.drawRect(0, 0, a0.rect().width() - 1, a0.rect().height() - 1)
            font = painter.font()

            current_block = self.editor.textCursor().block().blockNumber() + 1

            condition = True
            while block.isValid() and condition:
                block_geometry = self.editor.blockBoundingGeometry(block)
                offset = self.editor.contentOffset()
                block_top = block_geometry.translated(offset).top()
                number += 1

                rect = QRect(0, int(block_top + 2), self.width() - 5, height)  # added conversion to int

                if number == current_block:
                    font.setBold(True)
                else:
                    font.setBold(False)

                painter.setFont(font)
                painter.drawText(rect, Qt.AlignmentFlag.AlignRight, f'{number:d}')  # added .AlignmentFlag

                if block_top > a0.rect().bottom():
                    condition = False

                block = block.next()

            painter.end()


class ReplWidget(pyqtgraph.console.repl_widget.ReplWidget):
    """Add logging to terminal and log files. See original class for Documentation."""

    def __init__(self, logger: 'Logger', globals, locals, parent=None) -> None:  # noqa: A002, ANN001
        """Add logger.

        :param logger: A Logger
        :type logger: Logger
        """
        self.logger = logger
        super().__init__(globals, locals, parent)
        self.stdoutInterceptor = StdoutInterceptor(self.logger, self.write)

    def runCmd(self, cmd: str) -> None:
        """Print command to terminal and log files.

        :param cmd: console command
        :type cmd: str
        """
        self.logger.print(f'>>> {cmd}', flag=PRINT.CONSOLE)
        super().runCmd(cmd)

    def displayException(self) -> None:
        """Display the current exception and stack.

        Also print to terminal and log files.
        """
        tb = traceback.format_exc()
        lines = []
        indent = 4
        prefix = ''
        for l in tb.split('\n'):  # noqa: E741
            lines.append(' ' * indent + prefix + l)  # noqa: PERF401
        self.write('\n'.join(lines))
        self.logger.print('\n'.join(lines), flag=PRINT.CONSOLE)


class StdoutInterceptor(pyqtgraph.console.repl_widget.StdoutInterceptor):
    """Add logging to terminal and log files. See original class for Documentation."""

    def __init__(self, logger: 'Logger', writeFn: Callable) -> None:
        """Add logger.

        :param logger: A Logger
        :type logger: Logger
        :param writeFn: A write function
        :type writeFn: Callable
        """
        self.logger = logger
        super().__init__(writeFn)

    def write(self, strn: str) -> None:
        """Print response to terminal and log files.

        :param strn: response to input command
        :type strn: str
        """
        if any(c in strn for c in 'ℹ️⚠️❌🪲🆅🆃'):  # do not intercept internal print calls  # noqa: RUF001
            # self.logger.write(f'{strn}\n')::
            strn = strn.rstrip('\n') + '\n'  # make sure to always add newline if not already there
            self.logger.write(strn, fromConsole=True)
        else:
            if strn != '\n':
                self.logger.print(strn, flag=PRINT.CONSOLE)
            super().write(strn)


class ThemedConsole(pyqtgraph.console.ConsoleWidget):
    """pyqtgraph.console.ConsoleWidget with colors adjusting to theme."""

    def __init__(self, parentPlugin: 'Console', **kwargs) -> None:
        """Initialize a ThemedConsole.

        :param parentPlugin: Console parent plugin.
        :type parentPlugin: Plugin
        """
        self.parentPlugin = parentPlugin
        super().__init__(**kwargs)
        font = QFont()
        font.setFamily('Courier New')
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self.outputWarnings = QTextEdit()
        self.outputWarnings.setFont(font)
        self.outputWarnings.setReadOnly(True)
        self.outputErrors = QTextEdit()
        self.outputErrors.setFont(font)
        self.outputErrors.setReadOnly(True)
        self.outputLayout = QStackedLayout()
        self.outputLayout.addWidget(self.output)
        self.outputLayout.addWidget(self.outputWarnings)
        self.outputLayout.addWidget(self.outputErrors)
        outputWidget = QWidget()
        outputWidget.setLayout(self.outputLayout)
        self.splitter.insertWidget(0, outputWidget)  # .repl.layout.addChildLayout(self.outputLayout)
        self.splitter.setStyleSheet('QSplitter::handle{width:0px; height:0px;}')

        self.updateTheme()

    def _setupUi(self) -> None:
        layout = QtWidgets.QGridLayout(self)
        self.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.splitter = QtWidgets.QSplitter(Qt.Orientation.Vertical, self)
        layout.addWidget(self.splitter, 0, 0)

        self.repl = ReplWidget(self.parentPlugin.pluginManager.logger, self.globals, self.locals, self)
        self.splitter.addWidget(self.repl)

        self.historyList = QtWidgets.QListWidget(self)
        self.historyList.hide()
        self.splitter.addWidget(self.historyList)

        self.historyBtn = QtWidgets.QPushButton('History', self)
        self.historyBtn.setCheckable(True)
        self.repl.inputLayout.addWidget(self.historyBtn)

        self.repl.sigCommandEntered.connect(self._commandEntered)
        self.repl.sigCommandRaisedException.connect(self._commandRaisedException)

        self.excHandler = pyqtgraph.console.exception_widget.ExceptionHandlerWidget(self)
        self.excHandler.hide()
        self.splitter.addWidget(self.excHandler)

        self.exceptionBtn = QtWidgets.QPushButton('Exceptions..', self)
        self.exceptionBtn.setCheckable(True)
        self.repl.inputLayout.addWidget(self.exceptionBtn)

        self.excHandler.sigStackItemDblClicked.connect(self._stackItemDblClicked)
        self.exceptionBtn.toggled.connect(self.excHandler.setVisible)
        self.historyBtn.toggled.connect(self.historyList.setVisible)
        self.historyList.itemClicked.connect(self.cmdSelected)
        self.historyList.itemDoubleClicked.connect(self.cmdDblClicked)

    def updateTheme(self) -> None:
        """Change between dark and light themes."""
        self.output.setStyleSheet(f'QPlainTextEdit{{background-color:{colors.bg};}}')

    def scrollToBottom(self) -> None:
        """Scrolls to bottom to show recently added messages."""
        sb = self.output.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def loadHistory(self):  # extend to catch error if file does not exist  # noqa: ANN201, D102
        history = None
        try:
            history = super().loadHistory()
        except EOFError as e:
            print(f'Could not load history: {e}')  # noqa: T201
        return history

    def _commandEntered(self, repl, cmd) -> None:  # noqa: ANN001
        """Make sure submitted code will be visible even if filters were active before."""
        super()._commandEntered(repl, cmd)
        self.outputLayout.setCurrentIndex(0)
        self.parentPlugin.warningFilterAction.state = False
        self.parentPlugin.errorFilterAction.state = False


class ThemedNavigationToolbar(NavigationToolbar2QT):
    """Provide controls to interact with the figure.

    Adds light and dark theme support to NavigationToolbar2QT.
    """

    def __init__(self, canvas: 'FigureCanvas', parentPlugin: 'Plugin', coordinates: bool = True) -> None:
        """Initialize a ThemedNavigationToolbar."""
        super().__init__(canvas, parentPlugin, coordinates)
        self.parentPlugin = parentPlugin
        self.updateNavToolbarTheme()

    def updateNavToolbarTheme(self) -> None:  # noqa: C901
        """Change color of icons in matplotlib navigation toolBar to match theme."""
        dark = getDarkMode()
        for a in self.actions()[:-1]:
            icon = None
            match a.text():
                case 'Home':
                    icon = self.parentPlugin.makeCoreIcon('home_large_dark.png' if dark else 'home_large.png')
                case 'Back':
                    icon = self.parentPlugin.makeCoreIcon('back_large_dark.png' if dark else 'back_large.png')
                case 'Forward':
                    icon = self.parentPlugin.makeCoreIcon('forward_large_dark.png' if dark else 'forward_large.png')
                case 'Pan':
                    icon = self.parentPlugin.makeCoreIcon('move_large_dark.png' if dark else 'move_large.png')
                case 'Zoom':
                    icon = self.parentPlugin.makeCoreIcon('zoom_to_rect_large_dark.png' if dark else 'zoom_to_rect_large.png')
                case 'Subplots':
                    icon = self.parentPlugin.makeCoreIcon('subplots_large_dark.png' if dark else 'subplots_large.png')
                case 'Customize':
                    icon = self.parentPlugin.makeCoreIcon('qt4_editor_options_large_dark.png' if dark else 'qt4_editor_options_large.png')
                case 'Save':
                    icon = self.parentPlugin.makeCoreIcon('filesave_large_dark.png' if dark else 'filesave_large.png')
                case _:
                    pass
            if icon:
                a.setIcon(icon)
                a.fileName = icon.fileName

    def save_figure(self, *args) -> None:  # pylint: disable = missing-param-doc
        """Adjust theme used for saved figure."""
        limits = []
        if getDarkMode() and not getClipboardTheme():
            # use default light theme for clipboard
            with matplotlib.style.context('default'):
                limits.extend((ax.get_xlim(), ax.get_ylim()) for ax in self.parentPlugin.axes)
                self.parentPlugin.initFig()  # canvas, figure, and, ThemedNavigationToolbar will be replaced after this
                # reconnect new canvas to old instance of ThemedNavigationToolbar to complete saving
                super().__init__(self.parentPlugin.canvas, self.parentPlugin, self.coordinates)
                self.parentPlugin.plot()
                for i, ax in enumerate(self.parentPlugin.axes):
                    ax.set_xlim(limits[i][0])
                    ax.set_ylim(limits[i][1])
                self.canvas.figure.set_facecolor(colors.bg_light)
                self.canvas.draw_idle()
                self.parentPlugin.processEvents()
                super().save_figure(*args)
        else:
            super().save_figure(*args)
        if getDarkMode() and not getClipboardTheme():
            # restore dark theme for use inside app
            self.parentPlugin.initFig()
            self.parentPlugin.plot()
            for i, ax in enumerate(self.parentPlugin.axes):
                ax.set_xlim(limits[i][0])
                ax.set_ylim(limits[i][1])
            self.canvas.draw_idle()
        self.parentPlugin.pluginManager.Explorer.populateTree()  # show saved file in Explorer


class CursorAxes(Axes):
    """An axes with comes with a controllable cursor."""

    cursor: ControlCursor


class MZCalculator:
    """Add to a class derived from Scan.

    Allows to mark mass to charge (m/z) locations within a charge-state distribution, calculates absolute mass, and displays it on the axis.
    Use Ctrl + left mouse click to mark and Ctrl + right mouse click to reset.
    """

    def __init__(self, parentPlugin: 'Plugin', ax: 'Axes | None' = None) -> None:
        """Initialize a MZCalculator.

        :param parentPlugin: Parent plugin.
        :type parentPlugin: Plugin
        :param ax: Axis used to show results, defaults to None
        :type ax: mpl.axes.Axes, optional
        """
        self.parentPlugin = parentPlugin
        if ax:  # maybe set later, e.g. after initFig created a new axis.
            self.ax = ax
            self.canvas = ax.figure.canvas
        self.mz = np.array([])  # array with selected m/z values
        self.cs = None
        self.charges = np.array([])  # for charge state
        self.maxChargeState = 200  # maximal value for lowest charge state
        self.STD = np.array([])  # array with standard deviations for each charge state
        self.c1 = 0  # charge state of lowest m/z value
        self.intensity = np.array([])  # y value for selected m/z values (for plotting only)
        # Note: Events should be implemented outside of this class to allow Scan to trigger multiple functions based on the event
        # self.canvas.mpl_connect('button_press_event', self.msOnClick) -> self.canvas.mpl_connect('button_press_event', self.mzCalc.msOnClick)

    def setAxis(self, ax: Axes) -> None:
        """Set the axes and canvas to be used.

        :param ax: A matplotlib axes.
        :type ax: matplotlib.axes
        """
        self.ax = ax
        self.canvas = ax.figure.canvas

    def msOnClick(self, event: 'Event') -> None:
        """Add a new m/z value for Ctrl+left mouse click.

        Clears all data and labels for Ctrl+right mouse click.

        :param event: The click event.
        :type event: Event
        """
        if event.button == MouseButton.RIGHT:  # type: ignore  # noqa: PGH003
            self.clear()
        elif event.button == MouseButton.LEFT and kb.is_pressed('ctrl') and event.xdata and event.ydata:  # type: ignore  # noqa: PGH003
            self.addMZ(event.xdata, event.ydata)  # type: ignore  # noqa: PGH003

    def addMZ(self, x: float, y: float) -> None:
        """Add a single m/z value and evaluates the likely mass.

        :param x: m/z value.
        :type x: float
        :param y: Intensity.
        :type y: float
        """
        if x is not None and y is not None:
            self.mz = np.append(self.mz, x)
            self.intensity = np.append(self.intensity, y)
            self.determine_mass_to_charge()

    def clear(self) -> None:
        """Clear all data and removes labels."""
        self.mz = np.array([])
        self.intensity = np.array([])
        self.update_mass_to_charge()

    def determine_mass_to_charge(self) -> None:
        """Estimate charge states based on m/z values provided by user by minimizing standard deviation of absolute mass within a charge state series.

        Provides standard deviation for neighboring series to allow for validation of the result.
        """
        if len(self.mz) > 1:  # not enough information for analysis
            sort_indices = self.mz.argsort()
            self.mz = self.mz[sort_indices]  # increasing m/z match decreasing charge states
            self.intensity = self.intensity[sort_indices]
            self.charges = np.arange(self.maxChargeState + len(self.mz))  # for charge state up to self.maxChargeState
            self.STD = np.zeros(self.maxChargeState)  # initialize standard deviation
            for i in np.arange(self.maxChargeState):
                self.STD[i] = np.std(self.mz * np.flip(self.charges[i:i + len(self.mz)]))
            self.c1 = self.STD.argmin()
            self.cs = np.flip(self.charges[self.c1:self.c1 + len(self.mz)])  # charge states
            self.update_mass_to_charge()

    def mass_string(self, offset: int, label: str) -> str:
        """Indicate mass and standard deviation.

        :param offset: Charge offset relative to likely charge.
        :type offset: int
        :param label: Descriptor for type of estimation.
        :type label: str
        :return: Complete string.
        :rtype: str
        """
        return f'{label} mass (Da): {np.average(self.mz * np.flip(self.charges[self.c1 + offset:self.c1 + offset + len(self.mz)])):.2f}, std: {self.STD[self.c1 + offset]:.2f}'

    def update_mass_to_charge(self) -> None:
        """Update labels indicating mass and mass-to-charge values."""
        for ann in [child for child in self.ax.get_children() if isinstance(child, Annotation)]:  # [self.seAnnArrow, self.seAnnFile, self.seAnnFWHM]:
            ann.remove()
        if len(self.mz) > 1 and self.cs is not None:
            for x, y, charge in zip(self.mz, self.intensity, self.cs, strict=True):
                self.ax.annotate(text=f'{charge}', xy=(x, y), xycoords='data', ha='center')
            self.ax.annotate(text=f"{self.mass_string(-1, 'lower  ')}\n{self.mass_string(0, 'likely  ')}\n{self.mass_string(+1, 'higher')}\n"
                                    + '\n'.join([f'mz:{mass:10.2f} z:{charge:4}' for mass, charge in zip(self.mz, self.cs, strict=True)]),
                                xy=(0.02, 0.98), xycoords='axes fraction', fontsize=8, ha='left', va='top')
        self.parentPlugin.defaultLabelPlot()


class ViewBox(pg.ViewBox):
    """ViewBox providing mouseEnabledChangedUser event."""

    userMouseEnabledChanged = pyqtSignal(bool, bool)
    axis_leftright: pg.AxisItem
    dummyAx: pg.AxisItem
    plotItem: 'PlotItem'

    def __init__(self, *args, **kwargs) -> None:  # noqa: D107
        super().__init__(*args, **kwargs)
        self.dragging = False
        self.draggingTimer = QTimer()
        self.draggingTimer.timeout.connect(self.resetDragging)
        self.draggingTimer.setInterval(1000)  # 1 s

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, D102  # pylint: disable = missing-function-docstring
        if event:
            if event.button() == Qt.MouseButton.LeftButton:
                self.dragging = True
                self.draggingTimer.start()  # as mouseReleaseEvent is not called, this workaround makes sure the flag is reset
            super().mousePressEvent(event)

    def resetDragging(self) -> None:  # noqa: D102  # pylint: disable = missing-function-docstring
        self.dragging = False
        self.plotItem.parentPlot()

    def setMouseEnabled(self, x: 'bool | None' = None, y: 'bool | None' = None) -> None:
        """Call user event if values have changed.

        :param x: x enabled state, defaults to None
        :type x: bool | None, optional
        :param y: y enabled state, defaults to None
        :type y: bool | None, optional
        """
        x_old, y_old = self.mouseEnabled()
        super().setMouseEnabled(x, y)
        if x_old is not x or y_old is not y:
            self.userMouseEnabledChanged.emit(x_old if x is None else x, y_old if y is None else y)


class PlotCurveItem(pyqtgraph.PlotCurveItem):
    """PlotCurveItem that stores a reference to its parent and legend to allow for clean removal of references before deletion."""

    curveParent: 'PlotWidget | PlotItem'
    curveLegend: pg.LegendItem


class PlotDataItem(pyqtgraph.PlotDataItem):
    """PlotCurveItem that stores a reference to its parent and legend to allow for clean removal of references before deletion."""

    curveParent: 'PlotWidget | PlotItem | ViewBox'
    curveLegend: pg.LegendItem


class PlotItem(pg.PlotItem):
    """PlotItem providing xyLabel."""

    axis_leftright: pg.AxisItem
    dummyAx: pg.AxisItem
    setMouseEnabled: Callable  # dynamically created -> needs explicit type hint
    enableAutoRange: Callable  # dynamically created -> needs explicit type hint
    disableAutoRange: Callable  # dynamically created -> needs explicit type hint
    xyLabel: 'LabelItem'
    testModeLabel: 'LabelItem'
    setRange: Callable
    viewRange: Callable
    setXRange: Callable
    setYRange: Callable

    def __init__(self, parentPlugin: 'StaticDisplay | LiveDisplay | None' = None, groupLabel: str = '', tickWidth: int = 50, showXY: bool = True,
                  viewBox: 'ViewBox | None' = None, **kwargs) -> None:
        """Initialize a PlotItem.

        :param parentPlugin: Parent plugin, defaults to None
        :type parentPlugin: StaticDisplay | LiveDisplay, optional
        :param groupLabel: Label indicating unit, device, or group number, defaults to ''
        :type groupLabel: str, optional
        :param tickWidth: With of ticks, defaults to 50
        :type tickWidth: int, optional
        :param showXY: Determine if the xy label should be shown, defaults to True
        :type showXY: bool, optional
        :param viewBox: Custom ViewBox, defaults to None
        :type viewBox: ViewBox, optional
        """
        if not viewBox:
            viewBox = ViewBox()
        viewBox.plotItem = self
        super().__init__(viewBox=viewBox, **kwargs)
        self.parentPlugin = parentPlugin
        self.tickWidth = tickWidth
        self.showXY = showXY
        self.plotWidgetFont = QFont()
        self.plotWidgetFont.setPixelSize(13)
        if groupLabel:
            self.groupLabel = LabelItem(anchor=(1, 1))
            self.groupLabel.setParentItem(self.getViewBox())
            self.groupLabel.setText(groupLabel)
            self.groupLabel.setPos(10, 0)
            self.groupLabel.setColor(colors.fg)
        if showXY:
            self.xyLabel = LabelItem(anchor=(1, 1))
            self.xyLabel.setParentItem(self.getViewBox())
            self.xyLabel.setColor(colors.fg)
        if getTestMode():
            self.testModeLabel = LabelItem(justify='center', size='24pt')
            self.testModeLabel.setParentItem(self.getViewBox())
            self.testModeLabel.anchor((0.5, 0.5), (0.5, 0.5))
            self.testModeLabel.setPos(0, 0)
            color = QColor(colors.fg)
            self.testModeLabel.setText(
                f"<span style='font-size: 18pt; font-weight: bold; color: rgba({color.red},{color.green},{color.blue},120);'>Test Mode Active! Simulating Data!</span>")

    def init(self) -> None:
        """Init plotItem formatting and events."""
        self.showGrid(x=True, y=True, alpha=0.1)
        self.showAxis('top')
        self.getAxis('top').setStyle(showValues=False)
        self.showLabel('top', show=False)
        self.setMouseEnabled(x=False, y=True)  # keep auto pan in x running, use settings to zoom in x
        self.disableAutoRange(ViewBox.XAxis)
        self.setAxisItems({'left': SciAxisItem('left')})
        self.setAxisItems({'right': SciAxisItem('right')})
        self.updateGrid()
        self.setAxisItems({'bottom': pg.DateAxisItem()})  # , size=5
        self.setLabel('bottom', '<font size="4">Time</font>', color=colors.fg)  # has to be after setAxisItems
        self.getAxis('bottom').setTickFont(self.plotWidgetFont)
        self.connectMouse()

    def finalizeInit(self) -> None:
        """Apply final formatting."""
        for pos in ['left', 'top', 'right', 'bottom']:
            self.getAxis(pos).setPen(pg.mkPen(color=colors.fg, width=2))  # avoid artifacts with too thin lines
            self.getAxis(pos).setTextPen(pg.mkPen(color=colors.fg))
        for pos in ['left', 'right']:
            self.getAxis(pos).setTickFont(self.plotWidgetFont)
            self.getAxis(pos).setWidth(self.tickWidth)  # fixed space independent on tick formatting. labels may be hidden if too small!
        self.hideButtons()  # remove autorange button

    def connectMouse(self) -> None:
        """Connect the mouse to the scene to update xyLabel. Plot when user moves or rescales x range."""
        # call after adding to GraphicsLayout as scene is not available before
        scene = cast('pg.GraphicsScene', self.scene())
        if scene:
            scene.sigMouseMoved.connect(self.mouseMoveEvent)
        self.sigXRangeChanged.connect(self.parentPlot)

    def mouseMoveEvent(self, event: 'QGraphicsSceneMouseEvent | None') -> None:
        """Update the xyLabel with the current position.

        :param event: The mouseMoveEvent
        :type event: QGraphicsSceneMouseEvent
        """
        if not event:
            return
        if self.showXY:
            pos = cast('QPointF', event)  # called with QPointF instead of event?
            viewBox = self.getViewBox()
            if viewBox and viewBox.geometry().contains(pos):  # add offset
                pos = viewBox.mapSceneToView(pos)
                try:
                    if self.ctrl.logYCheck.isChecked():
                        self.xyLabel.setText(f"t = {datetime.fromtimestamp(pos.x()).strftime('%Y-%m-%d %H:%M:%S')}, y = {10**pos.y():.2e}")
                    else:
                        self.xyLabel.setText(f"t = {datetime.fromtimestamp(pos.x()).strftime('%Y-%m-%d %H:%M:%S')}, y = {pos.y():.2f}")
                    self.xyLabel.setPos(viewBox.geometry().width() - self.xyLabel.boundingRect().width() - 4, 2)
                except (OSError, ValueError, OverflowError):
                    pass  # ignore errors that occur before time axis is initialized
            else:
                self.xyLabel.setText('')
        if isinstance(event, QMouseEvent):
            super().mouseMoveEvent(event)

    def parentPlot(self) -> None:
        """Plot if Xrange changed by user.

        When looking at larger x ranges sections might not be shown due to data thinning.
        Make sure the number of displayed data points is appropriate for your data.
        """
        viewBox = self.getViewBox()
        if self.parentPlugin and viewBox and viewBox.mouseEnabled()[0]:
            self.parentPlugin.parentPlugin.signalComm.plotSignal.emit(True)  # noqa: FBT003

    @property
    def dragging(self) -> bool:  # pylint: disable = missing-function-docstring
        """Indicate if dragging event has been called recently."""
        return cast('ViewBox', self.getViewBox()).dragging


class PlotWidget(pg.PlotWidget):
    """PlotWidget providing xyLabel."""

    setRange: Callable  # defined at runtime

    def __init__(self, parentPlugin: 'StaticDisplay | LiveDisplay', **kwargs) -> None:
        """Initialize a PlotWidget."""
        super().__init__(**kwargs, plotItem=PlotItem(parentPlugin=parentPlugin, **kwargs), viewBox=ViewBox())
        plotItem = cast('PlotItem', self.getPlotItem())
        if plotItem:
            self.init = plotItem.init
            self.finalizeInit = plotItem.finalizeInit
        self.setMinimumHeight(15)  # can fit more plots on top of each other
        self.setBackground(colors.bg)

    @property
    def legend(self) -> 'pg.LegendItem | None':
        """The plot legend."""
        if self.plotItem:
            return self.plotItem.legend
        return None

    @property
    def dragging(self) -> bool:  # pylint: disable = missing-function-docstring  # noqa: D102
        return cast('PlotItem', self.getPlotItem()).dragging


class LabelItem(pg.LabelItem):
    """LabelItem that passes color changes on to the label text."""

    def setColor(self, color: 'ColorType') -> None:
        """Set the color.

        :param color: The color to be applied.
        :type color: ColorType
        """
        self.setText(self.text, color=color)


class SciAxisItem(pg.AxisItem):  # pylint: disable = abstract-method
    """Version of original logTickStrings that provide improved formatting of tick labels.

    Only difference to source code is 0.1g -> .0e and consistent use of 1 = 10⁰.
    """

    # based on https://pyqtgraph.readthedocs.io/en/latest/_modules/pyqtgraph/graphicsItems/AxisItem.html

    # no ticks when zooming in too much: https://github.com/pyqtgraph/pyqtgraph/issues/1505

    def __init__(self, *args, **kwargs) -> None:
        """Initialize a SciAxisItem."""
        super().__init__(*args, **kwargs)
        self.enableAutoSIPrefix(enable=False)  # always show complete numbers in ticks. especially for currents and pressures dividing by a random factor is very confusing

    def logTickStrings(self, values, scale, spacing):  # noqa: ANN001, ANN201, ARG002, D102
        estrings = [f'{x:.0e}' for x in 10 ** np.array(values) * scale]
        convdict = {'0': '⁰',
                    '1': '¹',
                    '2': '²',
                    '3': '³',
                    '4': '⁴',
                    '5': '⁵',
                    '6': '⁶',
                    '7': '⁷',
                    '8': '⁸',
                    '9': '⁹',
                    }
        dstrings = []
        for e in estrings:
            if e.count('e'):
                v, p = e.split('e')
                sign = '⁻' if p[0] == '-' else ''
                pot = ''.join([convdict[pp] for pp in p[1:].lstrip('0')])
                if not pot:  # added to account for 1=10⁰
                    pot = '⁰'
                dstrings.append(v + '·' + '10' + sign + pot)
            else:
                dstrings.append(e)
        return dstrings


class TimeoutLock:
    """Specify a timeout inside a with statement.

    Can be used as normal Lock or optionally using 'with self.lock.acquire_timeout(1) as lock_acquired:'.
    """

    # based on https://stackoverflow.com/questions/16740104/python-lock-with-statement-and-timeout
    def __init__(self, lockParent: 'Plugin | DeviceController | Logger') -> None:
        """Initialize a TimeoutLock.

        :param lockParent: A parent that allows to print and count errors.
        :type lockParent: Plugin | DeviceController | Logger
        """
        self._lock = threading.Lock()
        self.lockParent = lockParent
        self.print = lockParent.print

    def acquire(self, blocking: bool = True, timeout: int = -1) -> bool:
        """Acquires the lock.

        :param blocking: If it is set to True, the calling thread will be blocked if some other thread is holding the flag and once that lock is released,
            then the calling thread will acquire the lock and return True.
            If it is set to False, it will not block the thread if the lock is already acquired by some other thread, and will return False. Defaults to True
        :type blocking: bool, optional
        :param timeout: Timeout for lock acquisition, defaults to -1
        :type timeout: int, optional
        :return: True if lock has been acquired.
        :rtype: bool
        """
        return self._lock.acquire(blocking, timeout)

    @contextmanager
    def acquire_timeout(self, timeout: int, timeoutMessage: str = '', already_acquired: bool = False):  # noqa: ANN201
        """Acquire lock.

        Shows timeoutMessage if lock could not be acquired before timeout expires.

        :param timeout: timeout in seconds
        :type timeout: float, optional
        :param timeoutMessage: Message shown in case of a timeout, defaults to ''
        :type timeoutMessage: str, optional
        :param already_acquired: True if lock has already been acquired in callstack. Use to prevent deadlocks
        :type already_acquired: bool, optional
        :yield: True if lock is acquired
        :rtype: bool
        """
        result = already_acquired or self._lock.acquire(timeout=timeout)
        logLevel = cast('int', getLogLevel())
        if logLevel > 1:  # VERBOSE/TRACE
            # get more information on errors (file and line number not available when using except)
            yield result
            if result and not already_acquired:
                self._lock.release()
        else:
            try:
                yield result
            except Exception as e:  # noqa: BLE001
                if logLevel == 0:  # BASIC
                    self.print(f'Error while using lock: {e}', flag=PRINT.ERROR)
                else:  # DEBUG
                    self.print(f'Error while using lock: {e}\nStack:{"".join(traceback.format_stack()[:-1])}', flag=PRINT.ERROR)
                self.lockParent.errorCount += 1
            finally:
                if result and not already_acquired:
                    self._lock.release()
                if ((self.lockParent.errorCount == self.lockParent.maxErrorCount or self.lockParent.errorCount > 2 * self.lockParent.maxErrorCount) and
                    isinstance(self.lockParent, DeviceController)):
                    # only call closeCommunication when equal to maxErrorCount, Otherwise errors during closeCommunication could cause recursion.
                    self.print(f'Closing communication of {self.lockParent.name} after more than {self.lockParent.errorCount} consecutive errors.', flag=PRINT.ERROR)  # {e}
                    if not self.lockParent.closing:  # avoid recursion if lock cannot be acquired while closing
                        self.lockParent.closeCommunication()
        if not result and timeoutMessage:
            self.print(timeoutMessage, flag=PRINT.ERROR)

    def release(self) -> None:
        """Releases the lock."""
        self._lock.release()

    def __enter__(self) -> None:  # noqa: D105
        self._lock.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001, D105
        self._lock.__exit__(exc_type, exc_val, exc_tb)


class DeviceController(QObject):  # noqa: PLR0904
    """Each :class:`~esibd.plugins.Device` or :class:`~esibd.core.Channel` comes with a :class:`~esibd.core.DeviceController`.

    The :class:`~esibd.core.DeviceController` is not itself a :class:`~esibd.plugins.Plugin`. It only abstracts the direct
    hardware communication from :class:`plugins<esibd.plugins.Plugin>` allowing them to use minimal and
    consistent code that can be adjusted and reused independently of the
    hardware. It should do all resource or time intensive communication work
    in parallel threads to keep the GUI responsive. Following the
    producer-consumer pattern, the :class:`~esibd.core.DeviceController` reads values from a physical device and assigns
    them to the corresponding :class:`~esibd.core.Channel`. The :class:`devices<esibd.plugins.Device>` will collect data from
    the :class:`~esibd.core.Channel` independently. In case you work with time sensitive
    experiments this concept will need to be adapted. Feel free to use the
    basic functionality provided by :class:`~esibd.core.DeviceController` or implement
    your own from scratch. As the :class:`~esibd.core.DeviceController` only interacts with your
    custom :class:`~esibd.core.Channel` or :class:`~esibd.plugins.Device`, there are no general requirements for
    its implementation.
    """

    class SignalCommunicate(QObject):  # signals called from external thread and run in main thread
        """Bundle pyqtSignals."""

        initCompleteSignal = pyqtSignal()
        """Signal that is emitted after successful initialization of device communication."""
        closeCommunicationSignal = pyqtSignal()
        """Signal that triggers the acquisition to stop after communication errors."""
        updateValuesSignal = pyqtSignal()
        """Signal that transfers new data from the :attr:`~esibd.core.DeviceController.acquisitionThread` to the corresponding channels."""

    controllerParent: 'Device | Channel'
    """Reference to the associated class."""
    port: 'serial.Serial | None' = None
    """Port for serial communication."""
    initThread: 'Thread | None' = None
    """A parallel thread used to initialize communication."""
    acquisitionThread: 'Thread | None' = None
    """A parallel thread that regularly reads values from the device."""
    lock: TimeoutLock  # Lock
    """Lock used to avoid race conditions when communicating with the hardware."""
    acquiring: bool = False
    """True, while *acquisitionThread* is running. *AcquisitionThread* terminates if set to False."""
    initialized: bool = False
    """Indicates if communications has been initialized successfully and not yet terminated."""
    initializing: bool = False
    """Indicates if communications is being initialized."""
    closing: bool = False
    """Indicates if communications is being closed."""
    rng = np.random.default_rng()
    """Random number generator."""
    maxErrorCount: int
    """Close communication if exceeded."""

    def __init__(self, controllerParent: 'Device | Channel') -> None:
        """Initialize a DeviceController.

        :param controllerParent: A parent channel or device.
        :type controllerParent: Device | Channel
        """
        super().__init__()
        # Values are stored here by the deviceController and read out by the device later.
        # Change definition by overwriting initComplete if necessary.
        self.values: np.ndarray = None  # type: ignore # ignore on purpose  | None  # noqa: PGH003
        self.controllerParent = controllerParent
        self.pluginManager = controllerParent.pluginManager
        self.maxErrorCount = self.getDevice().maxErrorCount
        self.lock = TimeoutLock(lockParent=self)  # init here so each instance gets its own lock
        self.port = None
        self.signalComm = self.SignalCommunicate()
        self.signalComm.initCompleteSignal.connect(self.initComplete)
        self.signalComm.updateValuesSignal.connect(self.updateValues)
        self.signalComm.closeCommunicationSignal.connect(self.closeCommunication)
        self._errorCount = 0
        self.errorCountTimer = QTimer()
        self.errorCountTimer.timeout.connect(self.resetErrorCount)
        self.errorCountTimer.setSingleShot(True)

    @property
    def name(self) -> str:
        """Convenience method to access the name of the device."""
        return self.controllerParent.name

    @property
    def errorCount(self) -> int:
        """Convenience method to access the errorCount of the device."""
        return self._errorCount

    @errorCount.setter
    def errorCount(self, count: int) -> None:
        if count > self._errorCount and count % 5 == 0:
            self.print(f'Error count is {count}. Communication will be closed after {self.maxErrorCount} consecutive errors.', flag=PRINT.WARNING)
        self._errorCount = count
        if self.errorCount != 0:
            self.errorCountTimer.setInterval(self.controllerParent.pluginManager.Settings.errorResetTime * 60000)  # e.g. 10 min = 600000 msec
            QTimer.singleShot(0, self.errorCountTimer.start)  # will reset after interval unless another error happens before and restarts the timer
        if (self.errorCount == self.maxErrorCount or self.errorCount > 2 * self.maxErrorCount) and self.initialized:
            self.print(f'Closing communication after {self.errorCount} consecutive errors.', flag=PRINT.ERROR)  # {e}
            self.closeCommunication()
        QTimer.singleShot(0, self.getDevice().errorCountChanged)

    def resetErrorCount(self) -> None:
        """Reset error count to 0."""
        self.errorCount = 0
        if isinstance(self.controllerParent, self.pluginManager.Device):
            self.controllerParent.errorCount = 0

    def print(self, message: str, flag: PRINT = PRINT.MESSAGE) -> None:
        """Send a message to stdout, the statusbar, the Console, and if enabled to the logfile.

        It will automatically add a timestamp and the name of the sending plugin.

        :param message: A short informative message.
        :type message: str
        :param flag: Flag used to adjust message display, defaults to :attr:`~esibd.const.PRINT.MESSAGE`
        :type flag: :meth:`~esibd.const.PRINT`, optional
        """
        controller_name = f'{shorten_text(self.controllerParent.name + " controller", max_length=25):25s}' if isinstance(self.controllerParent, Channel) else 'Controller'
        self.controllerParent.print(f'{controller_name}: {message}', flag=flag)

    def getDevice(self) -> 'Device':
        """Return the corresponding device, even if controllerParent is a Channel."""
        if isinstance(self.controllerParent, self.pluginManager.Device):
            return self.controllerParent
        if isinstance(self.controllerParent, Channel) and isinstance(self.controllerParent.channelParent, self.pluginManager.Device):
            return self.controllerParent.channelParent
        return None  # type: ignore  # noqa: PGH003

    def initializeCommunication(self) -> None:
        """Start the :meth:`~esibd.core.DeviceController.initThread`."""
        self.print('initializeCommunication', flag=PRINT.DEBUG)
        if self.initializing:
            return
        if self.acquisitionThread and self.acquisitionThread.is_alive():
            self.print('Closing communication for reinitialization.', flag=PRINT.DEBUG)
            self.closeCommunication()  # terminate old thread before starting new one
        self.initializing = True
        self.errorCount = 0
        self.values = None  # type: ignore  # noqa: PGH003
        self.initThread = Thread(target=self.fakeInitialization if getTestMode() else self.runInitialization, name=f'{self.controllerParent.name} initThread')
        self.initThread.daemon = True
        self.initThread.start()  # initialize in separate thread

    def runInitialization(self) -> None:
        """Hardware specific initialization of communication. Executed in initThread (no access to GUI!)."""

    def fakeInitialization(self) -> None:
        """Faking successful initialization in test mode. Called instead of runInitialization."""
        # initialization time cannot be predicted, do not slow down tests time.sleep(2)
        self.signalComm.initCompleteSignal.emit()
        self.print('Faking values for testing!', flag=PRINT.DEBUG)
        self.initializing = False

    def initializeValues(self, reset: bool = False) -> None:
        """Initialize array used to store current values or readbacks.

        :param reset: If True, will reset values to np.nan, defaults to False
        :type reset: bool, optional
        """
        if self.values is None or reset:  # unless already defined by child class
            if isinstance(self.controllerParent, Channel):
                self.values = np.array([np.nan])
            elif getTestMode() and not reset and self.controllerParent.inout is INOUT.IN and self.controllerParent.useMonitors:
                self.values = np.array([channel.value for channel in self.controllerParent.getChannels()])
            else:
                self.values = np.array([np.nan] * len(self.controllerParent.getChannels()))  # initializing values, overwrite if needed

    def startAcquisition(self) -> None:
        """Start data acquisition from physical device."""
        self.print('startAcquisition', flag=PRINT.DEBUG)
        if not self.initialized:
            self.print('Cannot start acquisition. Not initialized', flag=PRINT.DEBUG)
            return
        if self.acquisitionThread and self.acquisitionThread.is_alive():
            self.controllerParent.print('Wait for data reading thread to complete before restarting acquisition.', flag=PRINT.DEBUG)
            self.acquiring = False
            self.acquisitionThread.join(timeout=5)
            if self.acquisitionThread.is_alive():
                self.print('Data reading thread did not complete. Reset connection manually.', flag=PRINT.ERROR)
                return
            self.controllerParent.print('Data reading thread did complete.', flag=PRINT.DEBUG)
        self.acquisitionThread = Thread(target=self.runAcquisition, name=f'{self.controllerParent.name} acquisitionThread')
        self.acquisitionThread.daemon = True
        self.acquiring = True  # terminate old thread before starting new one
        self.acquisitionThread.start()

    def initComplete(self) -> None:
        """Start acquisition from main thread (access to GUI!). Called after successful initialization."""
        self.initializeValues()
        self.initialized = True
        self.startAcquisition()
        if self.getDevice().isOn():
            self.getDevice().updateValues(apply=True)  # apply values before turning on or off
            self.toggleOnFromThread()

    def readNumbers(self) -> None:
        """Write channel values in an array that will be used in updateValue."""
        # overwrite to implement function if applicable

    def fakeNumbers(self) -> None:
        """Write fake channel values in an array that will be used in updateValue."""
        # overwrite to implement function
        if self.values is None:
            return
        if isinstance(self.controllerParent, self.pluginManager.Device):
            for i, channel in enumerate(self.controllerParent.getChannels()):
                if self.controllerParent.inout == INOUT.IN:
                    self.values[i] = channel.value + self.rng.random() - .5
                else:
                    self.values[i] = float(self.rng.integers(1, 100)) if np.isnan(self.values[i]) else self.values[i] * self.rng.uniform(.99, 1.01)
        elif isinstance(self.controllerParent, Channel):
            if self.controllerParent.inout == INOUT.IN:
                self.values[0] = self.controllerParent.value + self.rng.random() - .5
            else:
                self.values[0] = float(self.rng.integers(1, 100)) if np.isnan(self.values[0]) else self.values[0] * self.rng.uniform(.99, 1.01)

    def applyValueFromThread(self, channel: Channel) -> None:
        """Apply value to device (thread safe).

        :param channel: Channel for which the value should be applied.
        :type channel: esibd.core.Channel
        """
        if not getTestMode() and self.initialized:
            Thread(target=self.applyValue, args=(channel,), name=f'{self.controllerParent.name} applyValueThread').start()

    def applyValue(self, channel: Channel) -> None:
        """Apply value to device.

        Should only be called for real channels!

        :param channel: Channel for which the value should be applied.
        :type channel: esibd.core.Channel
        """
        # Extend to add functionality

    def updateValues(self) -> None:
        """Update the value or monitor of the channel(s) in the main thread. Called from acquisitionThread."""
        # Overwrite with specific update code if applicable.
        if self.values is not None and self.controllerParent:
            if isinstance(self.controllerParent, self.pluginManager.Device):
                for channel, value in zip(self.controllerParent.getChannels(), self.values, strict=True):
                    if channel.useMonitors and channel.enabled and channel.real:
                        # Monitors of input devices should be updated even if the channel is not active (value determined by equation).
                        channel.monitor = np.nan if channel.waitToStabilize else value
                    elif channel.enabled and channel.active and channel.real:
                        # Should only be called for output devices
                        channel.value = np.nan if channel.waitToStabilize else value
            elif isinstance(self.controllerParent, Channel):
                if self.controllerParent.useMonitors:
                    self.controllerParent.monitor = np.nan if self.controllerParent.waitToStabilize else self.values[0]
                else:
                    self.controllerParent.value = np.nan if self.controllerParent.waitToStabilize else self.values[0]

    def runAcquisition(self) -> None:
        """Run acquisition loop. Executed in acquisitionThread.

        Overwrite with hardware specific acquisition code.
        """
        while self.acquiring:
            with self.lock.acquire_timeout(1, timeoutMessage='Could not acquire lock to acquire data') as lock_acquired:
                if lock_acquired:
                    self.fakeNumbers() if getTestMode() else self.readNumbers()
                    self.signalComm.updateValuesSignal.emit()
            # release lock before waiting!
            time.sleep(self.getDevice().interval / 1000)

    def toggleOnFromThread(self, parallel: bool = True) -> None:
        """Toggles device on or off (tread safe).

        :param parallel: Use parallel thread. Run in main thread if you want the application to wait for this to complete! Defaults to True
        :type parallel: bool, optional
        """
        if self.initialized:
            if getTestMode():
                self.fakeNumbers()
                self.updateValues()
            elif parallel:
                Thread(target=self.toggleOn, name=f'{self.controllerParent.name} toggleOnThread').start()
            else:
                self.toggleOn()

    def toggleOn(self) -> None:
        """Toggles device on or off."""
        self.print('toggleOn', flag=PRINT.DEBUG)
        # Implement device specific

    def stopAcquisition(self) -> bool:
        """Terminate acquisition but leaves communication initialized."""
        self.print('stopAcquisition', flag=PRINT.DEBUG)
        if isinstance(self.controllerParent, self.pluginManager.Device) and self.getDevice().recording:
            # stop recording if controller is stopping acquisition
            # continue if only a channel controller is stopping acquisition
            self.getDevice().recording = False
        elif (isinstance(self.controllerParent, Channel) and self.getDevice().recording and
              # test if controllerParent is the last initialized channel -> it will be stopped, so recording should end.
                not any(channel.controller.initialized or not channel.active for channel in self.getDevice().channels
                         if channel.controller and channel is not self.controllerParent)):
            # only stop recording if none of the channel controllers is initialized
            self.getDevice().print('Stopping recording as last initialized channel is closing communication.', flag=PRINT.DEBUG)
            self.getDevice().recording = False
        if self.acquisitionThread:
            with self.lock.acquire_timeout(1, timeoutMessage='Could not acquire lock to stop acquisition.'):
                # use lock in runAcquisition to make sure acquiring flag is not changed before last call completed
                # set acquiring flag anyways if timeout expired. Possible errors have to be handled
                self.acquiring = False
            self.initializeValues(reset=True)  # set values and monitors to None to indicate that acquisition has stopped and current value is unknown
            self.updateValues()  # update new values in GUI
            self.getDevice().updateValues()
            return True
        return False

    def closeCommunication(self) -> None:
        """Close all open ports. Ths method has to be extended as described below.

        This should free up all resources and allow for clean reinitialization.
        Extend to add hardware specific code.
        Keep following order to make sure acquisition is stopped before communication is closed:
        1. super().closeCommunication()
        2. closing your custom hardware communication
        3. self.initialized = False
        """
        self.closing = True
        self.print('closeCommunication controller', flag=PRINT.DEBUG)
        if self.acquiring:
            self.stopAcquisition()  # only call if not already called by device
        # self.initialized = False # ! Make sure to call this at the end of extended function  # noqa: ERA001
        # self.closing = False # ! Make sure to call this at the end of extended function  # noqa: ERA001

    def serialWrite(self, port: serial.Serial, message: str, encoding: str = 'utf-8') -> None:
        """Write a string to a serial port. Takes care of decoding messages to bytes and catches common exceptions.

        :param port: Serial port.
        :type port: serial.Serial
        :param message: Message.
        :type message: str
        :param encoding: Encoding used for sending and receiving messages, defaults to 'utf-8'
        :type encoding: str, optional
        """
        self.print('serialWrite message: ' + message.replace('\r', '').replace('\n', ''), flag=PRINT.TRACE)
        try:
            self.clearBuffer(port)  # make sure communication does not break if for any reason the port is not empty. E.g. old return value has not been read.
            port.write(bytes(message, encoding))
        except serial.SerialTimeoutException as e:
            self.errorCount += 1
            self.print(f'Timeout while writing message, try to reinitialize communication: {e}. Message: {message}.', flag=PRINT.ERROR)
        except serial.PortNotOpenError as e:
            self.errorCount += 1
            self.print(f'Port not open, try to reinitialize communication: {e}. Message: {message}.', flag=PRINT.ERROR)
            self.signalComm.closeCommunicationSignal.emit()
        except serial.SerialException as e:
            self.errorCount += 1
            self.print(f'Serial error, try to reinitialize communication: {e}. Message: {message}.', flag=PRINT.ERROR)
            self.signalComm.closeCommunicationSignal.emit()
        except AttributeError as e:
            self.errorCount += 1
            self.print(f'Attribute error, try to reinitialize communication: {e}. Message: {message}.', flag=PRINT.ERROR)
            if port:
                self.signalComm.closeCommunicationSignal.emit()

    def serialRead(self, port: serial.Serial, encoding: str = 'utf-8', EOL: str = '\n', strip: str = '') -> str:
        """Read a string from a serial port.

        Takes care of decoding messages from bytes and catches common exceptions.

        :param port: Serial port.
        :type port: serial.Serial
        :param encoding: Encoding used for sending and receiving messages, defaults to 'utf-8'
        :type encoding: str, optional
        :param EOL: End of line character.
        :type EOL: str, optional
        :param strip: String to be stripped from message, defaults to ''
        :type strip: str, optional
        :return: message
        :rtype: str
        """
        response = ''
        try:
            if EOL == '\n':
                response = (port.readline().decode(encoding).strip(strip).rstrip() if strip
                            else port.readline().decode(encoding).rstrip())
            else:
                response = (port.read_until(EOL.encode(encoding)).decode(encoding).strip(strip).rstrip() if strip
                            else port.read_until(EOL.encode(encoding)).decode(encoding).rstrip())
        except UnicodeDecodeError as e:
            self.errorCount += 1
            self.print(f'Error while decoding message: {e}', flag=PRINT.ERROR)
        except serial.SerialTimeoutException as e:
            self.errorCount += 1
            self.print(f'Timeout while reading message, try to reinitialize communication: {e}', flag=PRINT.ERROR)
            self.signalComm.closeCommunicationSignal.emit()
        except serial.SerialException as e:
            self.errorCount += 1
            self.print(f'Serial error, try to reinitialize communication: {e}', flag=PRINT.ERROR)
            self.signalComm.closeCommunicationSignal.emit()
        except AttributeError as e:
            self.errorCount += 1
            self.print(f'Attribute error, try to reinitialize communication: {e}', flag=PRINT.ERROR)
            if port:
                self.signalComm.closeCommunicationSignal.emit()
        self.print('serialRead response: ' + response.replace('\r', '').replace('\n', ''), flag=PRINT.TRACE)
        return response

    def clearBuffer(self, port: serial.Serial) -> None:
        """Clear the buffer at the serial port.

        :param port: The port at which to clear the buffer, defaults to None
        :type port: serial.Serial, optional
        """
        port = port or self.port
        if not port:
            return
        x = port.in_waiting
        if x > 0:
            port.read(x)


class SplashScreen(QSplashScreen):
    """Program splash screen that indicates loading."""

    def __init__(self, app: 'Application') -> None:
        """Initialize a SplashScreen."""
        super().__init__()
        self.app = app
        self.lay = QVBoxLayout(self)
        self.labels = []
        self.index = 3
        self.label = QLabel()
        self.label.setMaximumSize(200, 200)
        self.label.setScaledContents(True)
        self.lay.addWidget(self.label)
        self.timer = QTimer()
        self.timer.timeout.connect(self.animate)
        self.timer.setInterval(1000)
        self.closed = False

    def animate(self) -> None:
        """Advances to the next frame."""
        self.index = np.mod(self.index + 1, len(SPLASHIMAGE))
        self.label.setPixmap(QPixmap(SPLASHIMAGE[self.index].as_posix()))

    def show(self) -> None:
        """Show the splash screen."""
        self.setParent(self.app.mainWindow, Qt.WindowType.Window)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if self.app:
            currentDesktopsCenter = self.app.mainWindow.geometry().center()
            self.move(currentDesktopsCenter.x() - 100, currentDesktopsCenter.y() - 100)  # move to center
        super().show()
        self.timer.start()
        self.raise_()
        QApplication.processEvents()

    def close(self) -> bool:
        """Close the splash screen."""
        self.closed = True
        self.timer.stop()
        return super().close()


class VideoRecorder:
    """Allows to record videos of a plugin."""

    # ! capture real contextual cursor instead of drawing fixed cursor requires recording with external library FFmpeg -> not supported

    def __init__(self, parentPlugin: 'Plugin') -> None:
        """Initialize a VideoRecorder.

        :param parentPlugin: The Plugin that will be recorded.
        :type parentPlugin: Plugin
        """
        self.parentPlugin = parentPlugin
        self.recordWidget: 'DockWidget | EsibdExplorer | None' = parentPlugin.dock
        self.timer = QTimer()
        self.timer.timeout.connect(self.capture_frame)
        self.fps = 10  # Frames per second
        self.frameCount = 0
        self.is_recording = False
        self.cursor_pixmap = self.parentPlugin.makeCoreIcon('cursor.png').pixmap(32)

    def startRecording(self) -> None:
        """Initialize video recorder and starts recording."""
        if not self.recordWidget or (self.parentPlugin.pluginManager.testing and not self.parentPlugin.pluginManager.Settings.showVideoRecorders):
            return
        self.frameCount = 0
        self.video_writer: cv2.VideoWriter
        self.screen = QGuiApplication.screenAt(self.recordWidget.mapToGlobal(QPoint(0, 0)))
        if self.screen:
            self.is_recording = True
            self.parentPlugin.pluginManager.Settings.incrementMeasurementNumber()
            self.file = self.parentPlugin.pluginManager.Settings.getMeasurementFileName(f'_{self.parentPlugin.name}.mp4')
            self.parentPlugin.videoRecorderAction.state = True
            self.parentPlugin.videoRecorderAction.setVisible(True)
            self.screen_geometry = self.screen.geometry()
            self.dpr = self.screen.devicePixelRatio()
            self.timer.start(int(1000 / self.fps))
            self.parentPlugin.print(f'Start recording {self.file.name}')
        else:
            self.parentPlugin.print('Cannot start recording. Screen not found.', flag=PRINT.ERROR)

    def capture_frame(self) -> None:
        """Capture a single video frame."""
        if not self.is_recording or not self.screen or not self.recordWidget:
            return
        full_screenshot = self.screen.grabWindow(sip.voidptr(0))  # should be called from main thread

        # Capture the current mouse position
        cursor_pos_global = QCursor().pos()
        # Overlay the cursor on the full-screen image
        painter = QPainter(full_screenshot)
        painter.drawPixmap(int(cursor_pos_global.x() - self.screen_geometry.x()), int(cursor_pos_global.y() - self.screen_geometry.y()), self.cursor_pixmap)
        painter.end()
        global_pos = self.recordWidget.mapToGlobal(QPoint(0, 0))  # Widget's global position
        screen_x = global_pos.x() - self.screen_geometry.x()
        screen_y = global_pos.y() - self.screen_geometry.y()
        # Define cropping rectangle in local screen coordinates
        rect = QRect(int(screen_x * self.dpr), int(screen_y * self.dpr), int(self.recordWidget.width() * self.dpr), int(self.recordWidget.height() * self.dpr))
        cropped_screenshot = full_screenshot.copy(rect)
        image = cropped_screenshot.toImage().convertToFormat(QImage.Format.Format_RGBA8888)  # Ensure correct format
        if self.frameCount == 0:
            self.width, self.height = image.width(), image.height()
            # Note cv2.VideoWriter_fourcc(*'H264') H.264 codec (MPEG-4 AVC) would achieve smaller file sizes,
            # but requires independent codec installation and would not work out of the box
            self.video_writer = cv2.VideoWriter(self.file.as_posix(), cv2.VideoWriter.fourcc(*'mp4v'), self.fps, (self.width, self.height))
        elif (image.width(), image.height()) != (self.width, self.height):
            self.parentPlugin.print('Resizing during video recording not supported. Stopping recording.', flag=PRINT.WARNING)
            self.stopRecording()
            return
        elif self.frameCount > 600 * self.fps:  # limit recording to 10 minutes
            self.parentPlugin.print('Stopping video recording after reaching 5 minute limit.', flag=PRINT.WARNING)
            self.stopRecording()
            return
        buffer = image.bits()  # Get image data as a bytes object
        if buffer:
            buffer.setsize(image.sizeInBytes())
            frame = np.asarray(buffer, dtype=np.uint8).reshape((self.height, self.width, 4))  # Convert to NumPy array
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)  # Convert RGBA to BGR for OpenCV
            self.video_writer.write(frame_bgr)
            self.frameCount += 1

    def stopRecording(self) -> None:
        """Stop recording and finalizes the video file."""
        if self.is_recording:
            self.timer.stop()
            self.parentPlugin.videoRecorderAction.state = False
            if self.frameCount == 0:
                self.parentPlugin.print('No frames have been recorded')
                return
            self.is_recording = False
            self.video_writer.release()
            self.parentPlugin.print(f'Saved {self.file.name}')
            self.parentPlugin.pluginManager.Explorer.populateTree()


class RippleEffect(QWidget):
    """Creates a fading ripple effect at the clicked QAction."""

    def __init__(self, parentWindow: QMainWindow, x: int, y: int, color: 'QColor | None' = None) -> None:
        """Initialize a RippleEffect.

        :param parentWindow: Main application window.
        :type parentWindow: QMainWindow
        :param x: X position.
        :type x: int
        :param y: Y position.
        :type y: int
        :param color: Ripple color, defaults to None
        :type color: QColor, optional
        """
        super().__init__(parentWindow)
        self._x, self._y = x, y
        self.color = color or QColor(138, 180, 247)
        self.radius = 20  # Initial ripple size
        self.opacity = 1.0  # Full opacity
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.expand)
        self.timer.start(80)  # ms steps
        self.setGeometry(parentWindow.rect())
        self.show()

    def expand(self) -> None:
        """Expand and fades the ripple effect."""
        self.radius -= 4  # Increase size
        self.opacity -= 0.1  # Reduce opacity
        if self.opacity <= 0 or self.radius <= 0:
            self.timer.stop()
            self.deleteLater()  # Remove effect
        self.update()  # Trigger repaint

    def paintEvent(self, a0: 'QPaintEvent | None') -> None:  # noqa: ARG002
        """Draws the ripple effect.

        :param a0: The paint event.
        :type a0: QPaintEvent
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.color.red(), self.color.green(), self.color.blue(), int(255 * self.opacity))
        pen = QPen(color, 6)
        painter.setPen(pen)
        painter.drawEllipse(self._x - self.radius, self._y - self.radius, self.radius * 2, self.radius * 2)


class MouseInterceptor(QObject):
    """Intercepts mouse clicks and applies ripple effect."""

    rippleEffectSignal = pyqtSignal(int, int, QColor)

    def __init__(self, window: EsibdExplorer) -> None:
        """Initialize a MouseInterceptor.

        :param window: The application window.
        :type window: QMainWindow
        """
        super().__init__()
        self.window = window
        self.rippleEffectSignal.connect(self.ripple)

    def ripple(self, x: int, y: int, color: QColor) -> None:
        """Initialize an instance of the RippleEffect at the given location.

        :param x: X position.
        :type x: int
        :param y: Y position.
        :type y: int
        :param color: Ripple color.
        :type color: QColor
        """
        RippleEffect(self.window, x, y, color)

    def eventFilter(self, a0: 'QObject | None', a1: 'QEvent | None') -> bool:  # noqa: ARG002
        """Intercept mouse clicks and applies ripple effect.

        :param a0: Sender of the event.
        :type a0: QObject
        :param a1: The Event.
        :type a1: QEvent
        :return: Indicates if the event has been handled. Always False as we want to add the ripple effect without altering anything else.
        :rtype: bool
        """
        if not hasattr(self.window, 'pluginManager') or self.window.pluginManager.closing:
            return False

        if (isinstance(a1, QMouseEvent) and a1.type() == QMouseEvent.Type.MouseButtonPress and hasattr(self.window.pluginManager, 'Settings')
            and self.window.pluginManager.Settings.showMouseClicks):
            local_pos = self.window.mapFromGlobal(a1.globalPosition().toPoint())
            if a1.button() == Qt.MouseButton.LeftButton:
                QTimer.singleShot(200, lambda: self.rippleEffectSignal.emit(local_pos.x(), local_pos.y(), QColor(colors.highlight)))
            elif a1.button() == Qt.MouseButton.RightButton:
                QTimer.singleShot(200, lambda: self.rippleEffectSignal.emit(local_pos.x(), local_pos.y(), QColor(255, 50, 50)))
        return False


class TransparentTextDelegate(QtWidgets.QStyledItemDelegate):
    """Delegate that ensures transparent text stays transparent even if QWidgetItem gets selected."""

    def paint(self, painter, option, index):  # noqa: ANN001, ANN201, D102 # pylint: disable = missing-param-doc, missing-function-docstring
        # Get the color of the text
        color = index.data(Qt.ItemDataRole.ForegroundRole)
        if isinstance(color, QBrush):
            qcolor = color.color()
        elif isinstance(color, QColor):
            qcolor = color
        else:
            qcolor = None

        # If text is fully transparent, skip drawing text
        if qcolor is not None and qcolor.alpha() == 0:
            # ignore paint event so transparent text used for sorting does not become visible
            pass
        else:
            super().paint(painter, option, index)
