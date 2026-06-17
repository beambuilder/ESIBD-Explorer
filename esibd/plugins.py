"""Contains only :class:`plugins<esibd.plugins.Plugin>` and plugin templates.

The user controls generally have a large amount of logic integrated and can act as an intelligent database.
This avoids complex and error prone synchronization between redundant data in the UI and a separate database.
Every Parameter should only exist in one unique location at run time.
"""
# Separating the logic from the PyQt specific UI elements may be required in the future,
# but only if there are practical and relevant advantages that outweigh the drawbacks of managing synchronization."""

import ast
import configparser
import contextlib
import gc
import inspect
import io
import itertools
import os
import sys
import time
import timeit
from collections.abc import Callable
from datetime import datetime
from itertools import islice
from pathlib import Path
from threading import Thread, Timer, current_thread, main_thread
from typing import TYPE_CHECKING, Any, cast

import h5py
import keyboard as kb
import matplotlib.pyplot as plt
import numpy as np
import pyautogui
import pyperclip
import pyqtgraph as pg
import requests
import simple_pid
from asteval import Interpreter
from matplotlib.axes import Axes
from matplotlib.backend_bases import Event, MouseButton
from matplotlib.figure import Figure
from matplotlib.text import Annotation
from packaging.version import InvalidVersion
from PyQt6 import QtCore
from PyQt6.QtCore import QLoggingCategory, QObject, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal  # , QRect
from PyQt6.QtGui import QAction, QFont, QIcon, QImage, QTextCursor  # , QPixmap, QScreen, QColor, QKeySequence, QShortcut, QTreeWidget
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollBar,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)
from send2trash import send2trash

import esibd
from esibd.const import *  # pylint: disable = wildcard-import, unused-wildcard-import  # noqa: F403
from esibd.core import *  # pylint: disable = wildcard-import, unused-wildcard-import  # noqa: F403

if TYPE_CHECKING:
    from matplotlib.lines import Line2D

if sys.platform == 'win32':
    import win32com.client
aeval = Interpreter()

pyautogui.FAILSAFE = False


class Plugin(QWidget):  # noqa: PLR0904
    """:class:`Plugins<esibd.plugins.Plugin>` abstract basic GUI code for devices, scans, and other high level UI elements.

    All plugins are ultimately derived from the :class:`~esibd.plugins.Plugin` class.
    The doc string of the plugin class will be shown in the corresponding help window
    unless documentation is implemented explicitly.
    """

    LOAD = 'Load'
    SAVE = 'Save'
    IMPORT = 'Import'
    EXPORT = 'Export'
    TIME = 'Time'
    UTF8 = 'utf-8'
    maxErrorCount = 10
    FILTER_INI_H5 = 'INI or H5 File (*.ini *.h5)'
    FILTER_LOG = 'LOG File (*.log)'
    pluginType: PLUGINTYPE = PLUGINTYPE.INTERNAL  # overwrite in child class mandatory
    """The type defines the location of the plugin in the user interface and allows to run
       operations on a group of plugins with the same type using :meth:`~esibd.core.PluginManager.getPluginsByType`."""
    name: str = ''  # specify in child class mandatory
    """A unique name that will be used in the graphic user interface.
       Plugins can be accessed directly from the :ref:`sec:console` using their name."""
    documentation: str = ''  # specify in child class
    """The plugin documentation used in the internal about dialog in the :ref:`sec:browser`.
    If None, the doc string *__doc__* will be used instead.
    The latter may contain rst commands for sphinx documentation which should not be exposed to the user.
    """
    version: str = ''  # specify in child class mandatory
    """The version of the plugin. Plugins are independent programs that
       require independent versioning and documentation."""
    optional: bool = True  # specify in child to prevent user from disabling this plugin
    """Defines if the user can deactivate the plugin in the :class:`~esibd.core.PluginManager` user interface."""
    supportedVersion: str = f'{PROGRAM_VERSION.major}.{PROGRAM_VERSION.minor}'
    """By default the current program version is used. You can
       define a fixed plugin version and future program versions will
       state that they are incompatible with this plugin. This can be used to
       prompt developers to update and test their plugins before
       distributing them for a more recent program version."""
    titleBar: 'QToolBar | None'
    """Actions can be added to the titleBar using :meth:`~esibd.plugins.Plugin.addAction` or :meth:`~esibd.plugins.Plugin.addStateAction`."""
    titleBarLabel: 'QLabel | None'
    """The label used in the titleBar."""
    dependencyPath: Path = Path()  # will be set when plugin is loaded.
    """Path to the plugin dependency files. Typically this is the same or relative to the plugin source code path.
       Can be used to locate dependencies like external scripts or media which are
       stored next to the plugin file or in sub folders relative to its location."""
    sourceCodePath: Path = Path()  # will be set when plugin is loaded.
    """Path to the plugin source code file."""
    pluginManager: PluginManager
    """A reference to the central :class:`~esibd.core.PluginManager`."""
    parentPlugin: 'Plugin'
    """A Plugin that acts as a parent by providing references for and using this plugin"""
    dock: 'DockWidget | None'
    """The dockWidget that allows to float and rearrange the plugin user interface."""
    scan = None
    """A :meth:`~esibd.plugins.Scan` that provides content to display."""
    fig: 'Figure | None'
    """A figure, initialized e.g. using `plt.figure(constrained_layout=True, dpi=getDPI())`
       and followed by `self.makeFigureCanvasWithToolbar(self.fig)`."""
    axes: list[Axes]
    """The axes of :attr:`~esibd.plugins.Plugin.fig`."""
    initializedGUI: bool
    """A flag signaling if the plugin graphical user interface has been initialized.
       You may want to ignore certain events before initialization is complete."""
    initializedDock: bool
    """A flag signaling if the plugin :attr:`~esibd.plugins.Plugin.dock` has been initialized.
       You may want to ignore certain events before initialization is complete."""
    lock: TimeoutLock
    """Locks are used to make sure methods decorated with @synchronized() cannot run in parallel,
       but one call has to be completed before the next."""
    iconFile: str = ''
    """Default icon file. Expected to be in dependencyPath."""
    iconFileDark: str = ''
    """Default icon file for dark mode. Expected to be in dependencyPath. Will fallback to iconFile."""
    useAdvancedOptions: bool = False
    """Adds toolbox icon to show advanced plugin options."""
    line: 'Line2D'
    """A line to store matplotlib lines for simple displays."""
    labelAxis: Axes = None  # type: ignore  # noqa: PGH003
    """Axis used for plot labels."""
    resizing: bool = False
    """Indicate if the plugin is resizing. May be used to increase performance by suppressing updates while resizing."""
    testTimeout: int = 60
    """"""

    class SignalCommunicate(QObject):  # signals that can be emitted by external threads
        """Bundle pyqtSignals."""

        testCompleteSignal = pyqtSignal()

    def __init__(self, pluginManager: PluginManager, dependencyPath: Path, sourceCodePath: Path) -> None:
        """Initialize a Plugin.

        :param pluginManager: The central PluginManager, defaults to None
        :type pluginManager: PluginManager, optional
        :param dependencyPath: Path to dependencies.
        :type dependencyPath: Path
        :param sourceCodePath: Path to the plugin source code.
        :type sourceCodePath: Path
        """
        super().__init__()
        self.app = cast('Application', QApplication.instance())
        self.pluginManager = pluginManager  # provide access to other plugins through pluginManager
        self.display: 'Plugin | None' = None  # may be added by child class
        self._loading = 0

        self.previewFileTypes: list[str] = []  # specify in child class if applicable # NOTE: defined here to prevent mutable default in class body
        """File extensions that are supported by this plugin. If a corresponding
        file is selected in the :meth:`~esibd.plugins.Explorer`, the plugins :meth:`~esibd.plugins.Plugin.loadData` function will be called."""

        self.errorCount = 0  # will be overwritten by setting for devices, but needs to be defined for other plugins
        self.labelAnnotation = None
        self.dock = None
        self.lock = TimeoutLock(lockParent=self)
        self.file: 'Path | None' = None
        self.canvas: 'DebouncedCanvas | None' = None  # type: ignore  # noqa: PGH003
        self.navToolBar: 'ThemedNavigationToolbar | None' = None  # type: ignore  # noqa: PGH003
        self.fig = None
        self.axes = []
        self.plotting = False
        self.copyAction = None
        self.initializedGUI = False
        self.initializedDock = False  # visible in GUI, some plugins will only appear when needed to display specific content
        self.dependencyPath = dependencyPath
        self.sourceCodePath = sourceCodePath
        self.dataClipboardIcon = self.makeCoreIcon('clipboard-paste-document-text.png')
        self.imageClipboardIcon = self.makeCoreIcon('clipboard-paste-image.png')
        self.testing_state: bool = False  # indicates if tests are running
        self.mainTester = False  # indicate if this plugin initiated the tests
        self.signalComm = self.SignalCommunicate()
        self.signalComm.testCompleteSignal.connect(self.testComplete)

    def print(self, message: str, flag: PRINT = PRINT.MESSAGE) -> None:
        """Send a message to stdout, the statusbar, the Console, and to the logfile.

        It will automatically add a
        timestamp and the name of the sending plugin.

        :param message: A short informative message.
        :type message: str
        :param flag: Flag used to adjust message display, defaults to :attr:`~esibd.const.PRINT.MESSAGE`
        :type flag: :meth:`~esibd.const.PRINT`, optional
        """
        self.pluginManager.logger.print(message=message, sender=self.name, flag=flag)

    @property
    def loading(self) -> bool:
        """A flag that can be used to suppress certain events while loading data or initializing the user interface.

        Make sure the flag is reset after every use. Internal logic allows nested use.
        """
        return self._loading != 0

    @loading.setter
    def loading(self, loading: bool) -> None:
        if loading:
            self._loading += 1
        else:
            self._loading -= 1

    def test(self) -> None:
        """Runs :meth:`~esibd.plugins.Plugin.runTestParallel` in parallel thread."""
        self.provideDock()
        self.raiseDock(showPlugin=True)
        self.testing = True
        self.pluginManager.Settings.updateSessionPath()  # avoid interference with undefined files from previous test run
        self.mainTester = True
        self.pluginManager.logger.openTestLogFile(tester=self.name)
        self.print(f'Starting testing for {self.name} {self.version}.')
        if isinstance(self, StaticDisplay):
            self.pluginManager.Console.mainConsole.input.setText(f'{self.parentPlugin.name}.staticDisplay.stopTest()')  # prepare to stop
        elif isinstance(self, LiveDisplay):
            self.pluginManager.Console.mainConsole.input.setText(f'{self.parentPlugin.name}.liveDisplay.stopTest()')  # prepare to stop
        else:
            self.pluginManager.Console.mainConsole.input.setText(f'{self.name}.stopTest()')  # prepare to stop
        Timer(0, self.runTestParallel).start()

    def stopTest(self) -> None:
        """Stop testing."""
        self.signalComm.testCompleteSignal.emit()

    LOG_LINE_LENGTH = 86

    def testControl(self, control: ParameterWidgetType | QAction | Action | StateAction | MultiStateAction | RestoreFloatComboBox | None,  # noqa: C901, PLR0912, PLR0915
                     value: Any, delay: float = 0, label: str = '') -> None:  # noqa: ANN401
        """Changes control states and triggers corresponding events.

        :param control: The control to be tested.
        :type control: ParameterWidgetType
        :param value: The value that should be simulated.
        :type value: Any
        :param delay: Wait this long for event to be processed before proceeding to next test. Defaults to 0
        :type delay: float, optional
        :param label: Custom test message used for logging, defaults to ''
        :type label: str, optional
        """
        if not self.testing:
            self.print('Cannot use testControl outside of testing.', flag=PRINT.WARNING)
            return
        if not control:
            return
        widget = control
        if label:
            message = label
        elif hasattr(control, 'toolTip') and not isinstance(control, (QAction)):
            # Actions already have tooltip in their objectName
            message = f'Testing {control.objectName()} with value {value} {control.toolTip() if callable(control.toolTip) else control.toolTip}  '
        else:
            message = f'Testing {control.objectName()} with value {value}'
        message = message.replace('\n', '')
        message = shorten_text(message, max_length=self.LOG_LINE_LENGTH)
        with self.lock.acquire_timeout(5, timeoutMessage=f'Could not acquire lock to test {message}') as lock_acquired:
            # allow any critical function to finish before testing next control
            if lock_acquired:
                self.print(message)
                if isinstance(control, (CheckBox | ToolButton)):
                    control.signalComm.setValueFromThreadSignal.emit(value)
                if isinstance(control, QAction):
                    if type(control) is Action:
                        control.signalComm.setValueFromThreadSignal.emit(value)
                    elif type(control) is StateAction:
                        control.state = value
                    # MultiStateAction: ignore value, triggered will rollState
                    control.triggered.emit(value)  # c.isChecked()
                    widget = cast('QToolButton', control.associatedObjects()[1])  # second object is associated QToolButton
                elif isinstance(control, QComboBox):
                    index = control.findText(str(value))
                    control.setCurrentIndex(index)
                    control.currentIndexChanged.emit(index)  # c.currentIndex()
                elif isinstance(control, (QLineEdit)):
                    control.editingFinished.emit()
                elif isinstance(control, (QSpinBox, LabviewSpinBox, LabviewDoubleSpinBox, LabviewSciSpinBox)):
                    control.valueChanged.emit(value)
                    control.editingFinished.emit()
                elif isinstance(control, (QCheckBox)):
                    control.setChecked(value)
                    control.stateChanged.emit(value)  # c.isChecked()
                elif isinstance(control, (QToolButton, QPushButton)):
                    control.clicked.emit()
                elif isinstance(control, (ColorButton)):
                    control.sigColorChanged.emit(control)
                elif isinstance(control, (QLabel)):
                    pass  # ignore labels as they are always indicators and not connected to events
                else:
                    self.print(f'No test implemented for class {type(control)}')
                if current_thread() is not main_thread():
                    if isinstance(widget, QAction):
                        widget = cast('QToolButton', widget)
                    main_center = widget.mapTo(self.pluginManager.mainWindow, widget.rect().center())
                    self.app.mouseInterceptor.rippleEffectSignal.emit(
                    main_center.x(), main_center.y(), QColor(colors.highlight))
                    time.sleep(.1)
        # Sleep after releasing lock!
        # Use minimal required delays to make sure event can be processed before triggering next one.
        # Ideally acquire lock to process event and make sure next one is triggered once lock is released, instead of using delay.
        time.sleep(max(delay, 0.5) if self.pluginManager.Settings.showVideoRecorders else delay)

    def testPythonPlotCode(self, closePopup: bool = False) -> None:
        """Generate and run python plot code for given file.

        Will save figure to file.

        :param closePopup: Determine if popup should be closed automatically, defaults to False
        :type closePopup: bool, optional
        """
        if self.file and self.file.name:
            with self.file.with_suffix('.py').open('w', encoding=UTF8) as plotFile:
                plotFile.write(self.generatePythonPlotCode())
            if current_thread() is main_thread():
                Module = dynamicImport('ModuleName', self.file.with_suffix('.py').as_posix())
                if Module:
                    Module.fig.savefig(self.file.with_suffix('.png'), format='png', bbox_inches='tight', dpi=getDPI())
                    QTimer.singleShot(0, self.pluginManager.Explorer.populateTree)
                    if closePopup:
                        plt.close(Module.fig)
            else:
                # NOTE: use console to execute in main thread.
                self.pluginManager.Console.executeSilent(command=f"Module = dynamicImport('ModuleName', '{self.file.with_suffix('.py').as_posix()}')")
                self.pluginManager.Console.executeSilent(command=f"Module.fig.savefig('{self.file.with_suffix('.png').as_posix()}',"
                                                         f" format='png', bbox_inches='tight', dpi={getDPI()})")
                if closePopup:
                    self.pluginManager.Console.executeSilent(command='plt.close(Module.fig)')
        else:
            self.print('Cannot testPythonPlotCode: no file available', flag=PRINT.WARNING)

    def runTestParallel(self) -> None:
        """Run a series of tests by changing values of selected controls and triggering the corresponding events.

        Extend and add call to super().runTestParallel() to the end to make sure testing flag is set to False after all test completed.
        """
        # ... add sequence of spaced events to trigger and test all functionality
        if self.initializedDock:
            self.testControl(self.aboutAction, value=True)
        self.signalComm.testCompleteSignal.emit()

    @synchronized()
    def testComplete(self) -> None:
        """Resets testing flag after last test completed."""
        # queue this behind any other synchronized function that is still being tested
        if self.mainTester:
            QTimer.singleShot(0, self.pluginManager.logger.closeTestLogFile)  # prevent conflict between synchronized testComplete and populateTree
        self.testing = False

    def bufferLagging(self, wait: 'int | None' = None) -> bool:
        """Wait for excess events to be processed.

        Only call from parallel thread, e.g. during testing or scanning!

        :param wait: Indicates how long the thread should wait to allow lag to recover, defaults to 5
        :type wait: int
        """
        if wait is None:
            wait = self.pluginManager.Settings.lagWaitTime
        if current_thread() is main_thread():
            self.print('Never call bufferLagging from main thread!', flag=PRINT.ERROR)
        max_lagging_seconds = 0
        max_lagging_device = None
        for device in self.pluginManager.DeviceManager.getDevices():
            if device.lagging_seconds > max_lagging_seconds:
                max_lagging_seconds = device.lagging_seconds
                max_lagging_device = device
        if max_lagging_seconds > self.pluginManager.Settings.maxLagTolerance:
            if max_lagging_device:
                self.print(f'Maximum lag of {max_lagging_seconds} s ({max_lagging_device.name}) is larger than'
                           f' tolerated lag of {self.pluginManager.Settings.maxLagTolerance} s. Pausing for {wait} s.',
                        flag=PRINT.DEBUG)
            else:
                self.print(f'Maximum lag of {max_lagging_seconds} s is larger than tolerated lag'
                           f' of {self.pluginManager.Settings.maxLagTolerance} s. Pausing for {wait} s.', flag=PRINT.DEBUG)
            time.sleep(wait)
            return True
        return False

    @property
    def testing(self) -> bool:
        """Indicates if the plugin, or any other plugin, is currently testing."""
        return self.pluginManager.testing

    @testing.setter
    def testing(self, state: bool) -> None:
        self.testing_state = state
        if not state:
            self.mainTester = False

    def processEvents(self) -> None:
        """Process all open events in the event loop before continuing. Is ignored in testmode where this could lead to a dead lock."""
        if not self.testing:
            self.app.processEvents()

    def waitForCondition(self, condition: Callable, interval: float = 0.5, timeout: int = 5, timeoutMessage: str = '') -> bool:
        """Wait until condition returns True or timeout expires.

        This can be safer and easier to understand than using signals and locks.
        The flag not just blocks other functions but informs them and allows them to react instantly.

        :param condition: will wait for condition to return True
        :type condition: Callable
        :param interval: wait interval seconds before checking condition
        :type interval: float
        :param timeout: timeout in seconds
        :type timeout: float
        :param timeoutMessage: message displayed if timeout is reached
        :type timeoutMessage: str
        :return: True if condition was met before timeout
        :rtype: bool
        """
        if current_thread() is main_thread():
            self.print(f'waitForCondition called from main thread! Timeout message: {timeoutMessage}', flag=PRINT.VERBOSE)
        if condition():  # no need to message as we are not waiting
            return True
        start = time.time()
        self.print(f'Waiting for {timeoutMessage}', flag=PRINT.DEBUG)
        while not condition():
            if time.time() - start < timeout:
                time.sleep(interval)
                if current_thread() is main_thread():  # do not block other events in main thread
                    self.processEvents()
            else:
                self.print(f'Wait condition: {condition()}, elapsed time: {time.time() - start}', flag=PRINT.VERBOSE)
                self.print(f'Timeout reached while waiting for {timeoutMessage}', flag=PRINT.ERROR)
                return False
        return True

    def addToolbarStretch(self) -> None:
        """Add a dummy action that can be used to stretch the gap between actions on the left and right of a toolbar."""
        if self.titleBar:
            self.stretchAction = QAction()  # allows adding actions in front of stretch later on
            self.stretchAction.setVisible(False)
            self.titleBar.addAction(self.stretchAction)
            self.stretch = QWidget()  # acts as spacer
            self.stretch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self.titleBar.addWidget(self.stretch)

    def setFloat(self) -> None:
        """Turn the plugin into a popup. It can be combined with others or docked in another place in the main window."""
        if self.dock and self.initializedDock:
            self.dock.setFloating(self.floatAction.state)
            if not self.floatAction.state:
                self.raiseDock()

    def initGUI(self) -> None:
        """Initialize the graphic user interface (GUI), independent of all other plugins."""
        # hierarchy: self -> mainDisplayLayout -> mainDisplayWidget -> mainLayout
        # mainDisplayLayout and mainDisplayWidget only exist to enable conversion into a dockArea
        # mainLayout contains the actual content
        self.print('initGUI', flag=PRINT.DEBUG)
        if not self.initializedGUI:
            if not self.layout():  # layout will be retained even if dock is closed.
                self.mainDisplayLayout = QVBoxLayout()
                self.setLayout(self.mainDisplayLayout)
                self.mainDisplayLayout.setContentsMargins(0, 0, 0, 0)
            self.mainDisplayWidget = QWidget()
            self.mainDisplayLayout.addWidget(self.mainDisplayWidget)
            self.mainLayout = QVBoxLayout()
            self.mainLayout.setContentsMargins(0, 0, 0, 0)
            self.mainDisplayWidget.setLayout(self.mainLayout)
            self.vertLayout = QVBoxLayout()  # contains row(s) with buttons on top and content below
            self.vertLayout.setSpacing(0)
            self.vertLayout.setContentsMargins(0, 0, 0, 0)
            self.mainLayout.addLayout(self.vertLayout)
            self.titleBar = QToolBar()
            self.titleBar.setIconSize(QSize(16, 16))
            self.titleBarLabel = QLabel('')
            self.titleBar.addWidget(self.titleBarLabel)
            if self.useAdvancedOptions:
                self.advancedAction = self.addStateAction(event=lambda: self.toggleAdvanced(advanced=None), attr='advanced',
                                                            toolTipFalse=f'Show advanced options for {self.name}.', iconFalse=self.makeCoreIcon('toolbox.png'),
                                                            toolTipTrue=f'Hide advanced options for {self.name}.', iconTrue=self.makeCoreIcon('toolbox--pencil.png'))
                self.advancedAction.state = False  # always off on start
            self.initializedGUI = True

    def finalizeInit(self) -> None:
        """Execute final initialization code after all other Plugins are initialized.

        Use this for code
        that modifies other :class:`Plugins<esibd.plugins.Plugin>`, e.g. adding an :class:`~esibd.core.Action` to the :class:`~esibd.plugins.DeviceManager`.
        """
        # dock should be present if this is called
        self.loading = True
        self.print('finalizeInit', flag=PRINT.DEBUG)
        self.addToolbarStretch()
        self.aboutAction = self.addAction(event=self.about, toolTip=f'About {self.name}.',
                                           icon=self.makeCoreIcon('help_large_dark.png' if getDarkMode() else 'help_large.png'))
        self.floatAction = self.addStateAction(event=self.setFloat, toolTipFalse=f'Float {self.name}.', iconFalse=self.makeCoreIcon('application.png'),
                                               toolTipTrue=f'Dock {self.name}.', iconTrue=self.makeCoreIcon('applications.png'),
                            # , attr='floating' cannot use same attribute for multiple instances of same class  # https://stackoverflow.com/questions/1325673/how-to-add-property-to-a-class-dynamically
                            )
        if self.pluginType in {PLUGINTYPE.DISPLAY, PLUGINTYPE.LIVEDISPLAY} and self != self.pluginManager.Browser:
            self.closeAction = self.addAction(event=self.closeUserGUI, toolTip=f'Close {self.name}.',
                                               icon=self.makeCoreIcon('close_dark.png' if getDarkMode() else 'close_light.png'))
        self.updateTheme()
        self.loading = False
        # extend or overwrite to add code that should be executed after all other plugins have been initialized, e.g. modifications of other plugins

    def afterFinalizeInit(self) -> None:
        """Execute after all other plugins are finalized."""
        self.videoRecorderAction = self.addStateAction(event=self.toggleVideoRecorder, toolTipFalse=f'Record video of {self.name}.',
            iconFalse=self.makeCoreIcon('record_start.png'), toolTipTrue=f'Stop and save video of {self.name}.',
            iconTrue=self.makeCoreIcon('record_stop.png'), before=self.aboutAction)
        self.videoRecorderAction.setVisible(self.pluginManager.Settings.showVideoRecorders)

    def initDock(self) -> None:
        """Initialize the :class:`~esibd.core.DockWidget`."""
        if not self.initializedDock:
            self.dock = DockWidget(self)

    def provideDock(self) -> bool:  # noqa: C901, PLR0912
        """Add existing :attr:`~esibd.plugins.Plugin.dock` to UI at position defined by :attr:`esibd.plugins.Plugin.pluginType`.

        Always call from the main_thread.
        """
        mw = self.pluginManager.mainWindow
        if not self.initializedDock:
            self.print('provideDock', flag=PRINT.DEBUG)
            self.loading = True
            self.initGUI()
            self.initDock()
            if self.pluginType in {PLUGINTYPE.CHANNELMANAGER, PLUGINTYPE.INPUTDEVICE, PLUGINTYPE.OUTPUTDEVICE, PLUGINTYPE.CONTROL, PLUGINTYPE.SCAN}:
                if self.pluginManager.firstControl:
                    mw.tabifyDockWidget(self.pluginManager.firstControl.dock, self.dock)
                else:
                    self.pluginManager.firstControl = self
                    mw.splitDockWidget(self.pluginManager.DeviceManager.dock, self.dock, Qt.Orientation.Vertical)  # below DeviceManager
            elif self.pluginType == PLUGINTYPE.DEVICEMGR:  # should be loaded before any other plugin
                mw.splitDockWidget(self.pluginManager.topDock, self.dock, Qt.Orientation.Vertical)  # below topDock
            elif self.pluginType == PLUGINTYPE.LIVEDISPLAY:
                liveDisplays = self.pluginManager.DeviceManager.getActiveLiveDisplays()
                if len(liveDisplays) == 0:
                    mw.splitDockWidget(self.pluginManager.topDock, self.dock, Qt.Orientation.Vertical)  # below topDock
                else:
                    mw.tabifyDockWidget(liveDisplays[-1].dock, self.dock)  # add to other live displays
            elif self.pluginType == PLUGINTYPE.CONSOLE and self.pluginManager.firstControl:
                mw.splitDockWidget(self.pluginManager.firstControl.dock, self.dock, Qt.Orientation.Vertical)
            elif self.pluginType == PLUGINTYPE.DISPLAY and self.pluginManager.firstControl:
                if self.pluginManager.firstDisplay:
                    mw.tabifyDockWidget(self.pluginManager.firstDisplay.dock, self.dock)
                else:
                    self.pluginManager.firstDisplay = self
                    mw.splitDockWidget(self.pluginManager.firstControl.dock, self.dock, Qt.Orientation.Horizontal)
            self.initializedDock = True  # only True after initializing and adding dock to GUI
            self.loading = False
            if not self.pluginManager.finalizing and not self.pluginManager.loading:
                self.toggleTitleBarDelayed()
            self.videoRecorder = VideoRecorder(parentPlugin=self)
            return True  # dock has been created
        return False  # dock already exists

    def raiseDock(self, showPlugin: bool = True) -> None:
        """Raise :attr:`dock<esibd.plugins.Plugin.dock>` if showPlugin is True.

        :param showPlugin: Only show one plugin, even though multiple plugins may load a file, defaults to True
        :type showPlugin: bool, optional
        """
        if showPlugin and self.dock and self.initializedDock:
            QTimer.singleShot(0, self.dock.raise_)  # give time for UI to draw before raising the dock

    def toggleAdvanced(self, advanced: 'bool | None' = False) -> None:  # noqa: ARG002
        """Overwrite to show advanced options.

        :param advanced: Indicates if advanced options should be visible, defaults to None
        :type advanced: bool, optional
        """
        self.print('toggleAdvanced not implemented')

    def requiredPlugin(self, name: str) -> None:
        """Display error message if required plugin is not available.

        :param name: Name of required Plugin.
        :type name: str
        """
        if not hasattr(self.pluginManager, name):
            self.print(f'Plugin {name} required for {self.name} {self.version}', flag=PRINT.ERROR)

    def addAction(self, event: 'Callable | None' = None, toolTip: str = '', *, icon: Icon | str, before: 'QAction | None' = None) -> Action:
        """Add a simple Action to the toolBar.

        :param event: The function triggered by the action, defaults to None
        :type event: method, optional
        :param toolTip: The toolTip of the action, defaults to ''
        :type toolTip: str, optional
        :param icon: The icon of the action
        :type icon: :class:`~esibd.core.Icon`, optional
        :param before: The existing action before which the new action will be placed, defaults to None. If None, the new action will be added to the end.
        :type before: :class:`~esibd.core.Action`, optional
        :return: The new Action
        :rtype: :class:`~esibd.core.Action`
        """
        # first arguments of func have to be "self" and "checked".
        # If you do not need "checked" use "lambda: func()" instead of func as argument to this function to prevent your parameters from being overwritten
        if isinstance(icon, str):
            icon = self.makeIcon(icon)
        a = Action(icon, toolTip, self)  # icon, toolTip, parent
        if event:
            a.triggered.connect(event)
        a.setObjectName(f"{self.name}/toolTip: {toolTip.strip('.')}.")
        if self.titleBar:
            if before:
                self.titleBar.insertAction(before, a)
            else:
                self.titleBar.addAction(a)
        return a

    def addStateAction(self, event: 'Callable | None' = None, toolTipFalse: str = '', *, iconFalse: Icon,  # noqa: PLR0913
                       toolTipTrue: str = '', iconTrue: 'Icon | None' = None, before: 'Action | None' = None,
                       attr: str = '', restore: bool = True, defaultState: bool = False) -> StateAction:
        """Add an action with can be toggled between two states, each having a dedicated tooltip and icon.

        :param event: The function triggered by the stateAction, defaults to None
        :type event: method, optional
        :param toolTipFalse: The toolTip of the stateAction if state is False, defaults to ''
        :type toolTipFalse: str, optional
        :param iconFalse: The icon of the stateAction if state is False.
        :type iconFalse: :class:`~esibd.core.Icon`, optional
        :param toolTipTrue: The toolTip of the stateAction if state is True, defaults to ''
        :type toolTipTrue: str, optional
        :param iconTrue: The icon of the stateAction if state is True, defaults to None
        :type iconTrue: :class:`~esibd.core.Icon`, optional
        :param before: An existing action or stateAction before which the new action will be placed, defaults to None.
            If None, the new stateAction will be added to the end.
        :type before: :class:`~esibd.core.Action`, optional
        :param attr: used to save and restore state, defaults to ''
        :type attr: str, optional
        :param restore: If True state will be restored when the program is restarted, defaults to True
        :type restore: bool, optional
        :param defaultState: Default state as saved by qSettings, defaults to False
        :type defaultState: bool, optional
        :return: The new StateAction
        :rtype: :class:`~esibd.core.StateAction`

        """
        # Using wrapper allows to pass parentPlugin implicitly and keep signature consistent.
        return StateAction(parentPlugin=self, toolTipFalse=toolTipFalse, iconFalse=iconFalse, toolTipTrue=toolTipTrue,
                                     iconTrue=iconTrue, event=event, before=before, attr=attr, restore=restore, defaultState=defaultState)

    def addMultiStateAction(self, states: list[MultiState], event: 'Callable | None' = None, before: 'QAction | None' = None,  # noqa: PLR0913, PLR0917
                             attr: str = '', restore: bool = True, defaultState: int = 0) -> MultiStateAction:
        """Add an action with can be toggled between two states, each having a dedicated tooltip and icon.

        :param event: The function triggered by the stateAction, defaults to None
        :type event: method, optional
        :param states: The list of states the control can represent, defaults to a list of empty states
        :type states: list[:class:`~esibd.core.MultiState`], optional
        :param before: An existing action or stateAction before which the new action will be placed, defaults to None.
            If None, the new stateAction will be added to the end.
        :type before: :class:`~esibd.core.Action`, optional
        :param attr: Used to save and restore state, defaults to ''
        :type attr: str, optional
        :param restore: If True state will be restored when the program is restarted, defaults to True
        :type restore: bool, optional
        :param defaultState: Index of default state, defaults to 0
        :type defaultState: int, optional
        :return: The new StateAction
        :rtype: :class:`~esibd.core.StateAction`

        """
        # Using wrapper allows to pass parentPlugin implicitly and keep signature consistent.
        return MultiStateAction(parentPlugin=self, states=states, event=event, before=before, attr=attr, restore=restore, defaultState=defaultState)

    def toggleTitleBar(self) -> None:
        """Adjust the title bar layout and :attr:`~esibd.plugins.Plugin.titleBarLabel` depending on the state of the :attr:`~esibd.plugins.Plugin.dock` (tabbed, floating, ...).

        Extend to make sure toggleTitleBar is called for dependent plugins if applicable.
        """
        if self.dock:
            self.dock.toggleTitleBar()

    def toggleTitleBarDelayed(self, delay: int = 500) -> None:
        """Delay toggleTitleBar until GUI updates have been completed.

        :param delay: Delay in ms, defaults to 500
        :type delay: int, optional
        """
        QTimer.singleShot(delay, self.toggleTitleBar)

    def addContentWidget(self, contentWidget: QWidget) -> None:
        """Use this to add your main content widget to the user interface.

        :param contentWidget: Content widget
        :type contentWidget: QWidget
        """
        self.vertLayout.addWidget(contentWidget)

    def addContentLayout(self, layout: QLayout) -> None:
        """Use this to add a content layout instead of a content widget to the user interface.

        :param layout: Content layout
        :type layout: QLayout
        """
        self.vertLayout.addLayout(layout)

    def supportsFile(self, file: Path) -> bool:
        """Test if a file is supported by the plugin, based on file name or content.

        :param file: File that has been selected by the user.
        :type file: pathlib.Path
        :return: Returns True if the file is supported by the plugin. Test if supported based on file extension or content.
        :rtype: bool
        """
        return any(file.name.lower().endswith(fileType.lower()) for fileType in self.previewFileTypes)

    def loadData(self, file: Path, showPlugin: bool = True) -> None:  # noqa: ARG002
        """Load and displays data from file.

        This should only be called for files where :meth:`~esibd.plugins.Plugin.supportsFile` returns True.
        Overwrite depending on data supported by the plugin.

        :param file: File from which to load data.
        :type file: pathlib.Path
        :param showPlugin: Show plugin after loading data, defaults to True. Some files are handled by multiple plugins and only one of them should be shown by default.
        :type showPlugin: bool, optional
        """
        self.print(f'Loading data from {file} not implemented.', flag=PRINT.ERROR)

    def getSupportedFiles(self) -> list[str]:
        """Return supported file types."""
        # extend to include previewFileTypes of associated displays if applicable
        return self.previewFileTypes

    def hdfUpdateVersion(self, file: h5py.File) -> None:
        """Update version in the Info group.

        :param file: The file in which to update the version.
        :type file: h5py.File
        """
        info_group = self.requireGroup(file, INFO)
        for key, value in infoDict(self.name).items():
            info_group.attrs[key] = value

    def requireGroup(self, group: h5py.Group, name: str) -> h5py.Group:
        """Replace require_group from h5py, and adds support for track_order.

        :param group: Valid group.
        :type group: h5py.Group
        :param name: Name of new group.
        :type name: str
        :return: New group.
        :rtype: h5py.Group
        """
        if name in group:
            return cast('h5py.Group', group[name])
        return group.create_group(name=name, track_order=True)

    def toggleVideoRecorder(self) -> None:
        """Toggles visibility of videoRecorderActions."""
        if self.videoRecorderAction.state:
            self.videoRecorder.startRecording()
        else:
            self.videoRecorder.stopRecording()

    def about(self) -> None:
        """Display the about dialog of the plugin using the :ref:`sec:browser`."""
        name = self.name
        if isinstance(self, LiveDisplay):
            name = f'{self.parentPlugin.name}.liveDisplay'
        elif isinstance(self, StaticDisplay):
            name = f'{self.parentPlugin.name}.staticDisplay'
        elif 'Channel Plot' in self.name:
            name = f'{self.parentPlugin.name}.channelPlot'
        elif self.pluginType == PLUGINTYPE.DISPLAY and hasattr(self, 'parentPlugin'):
            name = f'{self.parentPlugin.name}.display'
        elif isinstance(self, Scan.Display):
            name = f'{self.scan.name}.display'
        self.print(f'Run Tree.inspect({name}) to get more info.')
        documentation = ((self.documentation or self.__doc__) or '').replace('\n', '<br>')
        self.pluginManager.Browser.setAbout(self, f'About {self.name}', f"""
            <p>{documentation}</p>
            <p>Supported files: {', '.join(self.getSupportedFiles())}<br>
            Supported version: {self.supportedVersion}<br></p>"""
            # add programmer info in testmode, otherwise only show user info
            f"""<p>Plugin type: {self.pluginType.value}<br>
            Optional: {self.optional}<br>
            Dependency path: {self.dependencyPath.resolve()}<br>
            Source code file: {self.sourceCodePath.resolve()}<br></p>"""
            + self.getToolBarActionsHTML(),
            )

    def getToolBarActionsHTML(self) -> str:
        """Return HTML code that lists all toolbar actions including the corresponding icons.

        :return: HTML code documenting toolbar actions.
        :rtype: str
        """
        if not hasattr(self, 'titleBar') or not self.titleBar:
            return ''
        actionsHTML = '<p>Icon Legend:<br>'
        for action in self.titleBar.actions():
            if action.iconText():
                if isinstance(action, (Action, StateAction, MultiStateAction)) and hasattr(action.getIcon(), 'fileName'):
                    actionsHTML += (f"<span><img src='{Path(action.getIcon().fileName).resolve()}' style='vertical-align: middle;'"
                    f" width='16'/><span style='vertical-align: middle;'> {action.getToolTip()}</span></span><br>\n")
                elif hasattr(action, 'fileName'):
                    actionsHTML += (f"<span><img src='{Path(action.fileName).resolve()}' style='vertical-align: middle;'"  # type: ignore  # noqa: PGH003
                    f" width='16'/><span style='vertical-align: middle;'> {action.toolTip()}</span></span><br>\n")
                else:
                    self.print(f'QAction with iconText {action.iconText()} has no attribute fileName', flag=PRINT.WARNING)  # assign fileName if missing
        actionsHTML += '</p>'
        return actionsHTML

    def makeFigureCanvasWithToolbar(self, figure: Figure) -> None:  # noqa: C901, PLR0912
        """Create canvas, which can be added to the user interface, and adds the corresponding navToolBar to the plugin titleBar.

        :param figure: A matplotlib figure.
        :type figure: matplotlib.figure.Figure
        """
        if self.canvas:
            self.canvas.setVisible(False)  # need to get out of the way quickly when changing themes, deletion may take longer
            self.canvas.deleteLater()
        if self.navToolBar and self.titleBar:
            layout = self.titleBar.layout()
            if layout:
                for widget in self.titleBar.findChildren(QWidget):
                    # remove actions along with corresponding widgets
                    action = None
                    if hasattr(widget, 'defaultAction'):
                        action = cast('QToolButton', widget).defaultAction()
                    elif isinstance(widget, QWidgetAction):
                        action = widget
                    if action and action.property('nav_action') is True:
                        layout.removeWidget(widget)
                        self.titleBar.removeAction(action)
                        action.setParent(None)
                        action.deleteLater()
                for action in self.titleBar.actions():
                    # also remove separators
                    if action.property('nav_action') is True:
                        self.titleBar.removeAction(action)
                self.navToolBar.update()
                self.navToolBar.repaint()
                self.navToolBar.deleteLater()
                self.navToolBar = None
        self.canvas = DebouncedCanvas(parentPlugin=self, figure=figure)
        self.navToolBar = ThemedNavigationToolbar(self.canvas, parentPlugin=self)  # keep reference in order to reset navigation
        if self.titleBar:
            for action in self.navToolBar.actions()[:-1]:  # last action is empty and undocumented
                action.setProperty('nav_action', True)  # noqa: FBT003
                if hasattr(self, 'stretchAction'):
                    self.titleBar.insertAction(self.stretchAction, action)
                else:
                    self.titleBar.addAction(action)

    def labelPlot(self, label: str) -> None:
        """Add file name labels to plot to trace back which file it is based on.

        :param ax: A matplotlib axes.
        :type ax: matplotlib.axes.Axes
        :param label: The plot label.
        :type label: str
        """
        ax = self.labelAxis or self.axes[0]
        fontsize = 10
        # call after all other plotting operations are completed for scaling to work properly
        if self.labelAnnotation:
            with contextlib.suppress(ValueError, NotImplementedError):  # might have been deleted already
                self.labelAnnotation.remove()
        if label:
            self.labelAnnotation = ax.annotate(text=label, xy=(.98, .98), fontsize=fontsize, xycoords='axes fraction', textcoords='axes fraction',
                                        ha='right', va='top', bbox={'boxstyle': 'square, pad=.2', 'fc': plt.rcParams['axes.facecolor'], 'ec': 'none'}, clip_on=True)
            self.processEvents()  # trigger paint to get width
        else:
            self.labelAnnotation = None
        fig = ax.get_figure()
        if fig:
            if self.labelAnnotation:
                labelWidth = self.labelAnnotation.get_window_extent(renderer=fig.canvas.get_renderer()).width  # type: ignore  # noqa: PGH003
                axisWidth = ax.get_window_extent().transformed(fig.dpi_scale_trans.inverted()).width * fig.dpi * .9
                if labelWidth > 0:
                    self.labelAnnotation.set_size(min(max(fontsize / labelWidth * axisWidth, 1), 10))  # type: ignore  # noqa: PGH003
            if hasattr(ax, 'cursor'):  # cursor position changes after adding label... -> restore
                cast('CursorAxes', ax).cursor.updatePosition()
            ax.figure.canvas.draw_idle()

    def defaultLabelPlot(self) -> None:
        """Add file name labels to plot to trace back which file it is based on."""
        if isinstance(self, Scan) and len(self.outputChannels) > 0:
            if self.file:
                self.labelPlot(f'{self.outputChannels[self.getOutputIndex()].name} from {self.file.name}')
            else:
                self.labelPlot(self.outputChannels[self.getOutputIndex()].name)
        elif self.file:
            self.labelPlot(self.file.name)

    def removeAnnotations(self, ax: Axes) -> None:
        """Remove all annotations from an axes.

        :param ax: A matplotlib axes.
        :type ax: matplotlib.axes.Axes
        """
        for ann in [child for child in ax.get_children() if isinstance(child, Annotation)]:  # [self.seAnnArrow, self.seAnnFile, self.seAnnFWHM]:
            ann.remove()

    def getIcon(self, desaturate: bool = False) -> Icon:
        """Get the plugin icon. Consider using a themed icon that works in dark and light modes.

        Overwrite only if definition of iconFile and iconFileDark is not sufficient.

        :param desaturate: Indicates if color should be removed from icon, defaults to False
        :type desaturate: bool, optional
        :return: Icon
        :rtype: :class:`~esibd.core.Icon`
        """
        # e.g. return self.darkIcon if getDarkMode() else self.lightIcon
        if self.iconFile:
            return self.makeIcon(self.iconFileDark if getDarkMode() and self.iconFileDark else self.iconFile, desaturate=desaturate)
        return self.makeCoreIcon('help_large_dark.png' if getDarkMode() else 'help_large.png', desaturate=desaturate)

    def makeCoreIcon(self, file: str, desaturate: bool = False) -> Icon:
        """Return an icon based on a filename. Looks for files in the :meth:`~esibd.plugins.Plugin.dependencyPath`.

        :param file: Icon file name.
        :type file: str
        :param desaturate: Indicates if color should be removed from icon, defaults to False
        :type desaturate: bool, optional
        :return: Icon
        :rtype: :class:`~esibd.core.Icon`
        """
        return self.makeIcon(file=file, path=internalMediaPath, desaturate=desaturate)

    def makeIcon(self, file: str, path: 'Path | None' = None, desaturate: bool = False) -> Icon:
        """Return an icon based on a filename. Looks for files in the :meth:`~esibd.plugins.Plugin.dependencyPath` unless otherwise specified.

        :param file: Icon file name.
        :type file: str
        :param path: The file path to the icon.
        :type path: pathlib.Path, optional
        :param desaturate: Indicates if color should be removed from icon, defaults to False
        :type desaturate: bool, optional
        :return: Icon
        :rtype: :class:`~esibd.core.Icon`
        """
        iconPath = Path(str((path or self.dependencyPath) / file))
        if not iconPath.exists():
            self.print(f'Could not find icon {iconPath.as_posix()}', flag=PRINT.ERROR)
            return Icon(internalMediaPath / 'unicode_error.png')
        return Icon(iconPath, desaturate=desaturate)

    def updateTheme(self) -> None:
        """Change between dark and light themes.

        Most controls should update automatically as the color pallet is changed.
        Only update the remaining controls using style sheets.
        Extend to adjust colors to app theme.
        """
        if self.fig and not self.loading and (not self.scan or self.scan.file.name):
            self.initFig()
            self.plot()
        if hasattr(self, 'navToolBar') and self.navToolBar:
            self.navToolBar.updateNavToolbarTheme()
        if hasattr(self, 'closeAction'):
            self.closeAction.setIcon(self.makeCoreIcon('close_dark.png' if getDarkMode() else 'close_light.png'))
        if hasattr(self, 'aboutAction'):
            self.aboutAction.setIcon(self.makeCoreIcon('help_large_dark.png' if getDarkMode() else 'help_large.png'))

    def initFig(self) -> None:
        """Initialize a figure.

        Will be called when a :ref:`display<sec:displays>` is closed and reopened or the theme is changed.
        Overwrite your figure initialization here to make sure all references are updated correctly.
        """
        self.fig = None
        self.canvas = None  # type: ignore  # noqa: PGH003

    def provideFig(self) -> None:
        """Create or reinitialize a matplotlib figure."""
        if self.fig and rgb_to_hex(cast('tuple[float, float, float, float]', self.fig.get_facecolor())) != colors.bg:
            # need to create new fig to change matplotlib style
            plt.close(self.fig)
            self.fig = None
        if self.fig:
            self.fig.clf()  # reuse if possible
            self.fig.set_constrained_layout(True)  # type: ignore  # noqa: PGH003
            self.fig.set_dpi(getDPI())
        else:
            self.fig = plt.figure(constrained_layout=True, dpi=getDPI(), label=f'{self.name} figure')
            self.makeFigureCanvasWithToolbar(self.fig)
            self.addContentWidget(self.canvas)
        self.axes = []

    @plotting
    def plot(self) -> None:
        """If applicable, overwrite with a plugin specific plot method."""

    def generatePythonPlotCode(self) -> str:
        """Provide plugin specific code to plot data exported by the plugin independently.

        This is a starting point to reproduce and adjust figures e.g. for publications or independent data analysis.

        :return: Python plot code
        :rtype: str
        """
        # Extend to add functionality
        return ''

    @synchronized()
    def copyClipboard(self) -> None:
        """Copy matplotlib figure to clipboard."""
        if self.fig:
            limits = []
            buffer = io.BytesIO()
            if getDarkMode() and not getClipboardTheme():
                # use default light theme for clipboard
                with matplotlib.style.context('default'):
                    limits = [(ax.get_xlim(), ax.get_ylim()) for ax in self.axes]
                    size = self.fig.get_size_inches()
                    self.initFig()
                    self.plot()
                    for i, ax in enumerate(self.axes):
                        ax.set_xlim(limits[i][0])
                        ax.set_ylim(limits[i][1])
                    self.fig.set_size_inches(tuple(size))
                    if self.canvas:
                        self.canvas.draw_idle()
                    self.fig.savefig(buffer, format='png', bbox_inches='tight', dpi=getDPI(), facecolor='w')  # safeFig in default context
            else:
                self.fig.savefig(buffer, format='png', bbox_inches='tight', dpi=getDPI())
            self.imageToClipboard(QImage.fromData(buffer.getvalue()))
            buffer.close()
            if getDarkMode() and not getClipboardTheme():
                # restore dark theme for use inside app
                self.initFig()
                self.plot()
                for i, ax in enumerate(self.axes):
                    ax.set_xlim(limits[i][0])
                    ax.set_ylim(limits[i][1])
                if self.canvas:
                    self.canvas.draw_idle()

    def imageToClipboard(self, image: 'QPixmap | QImage') -> None:
        """Set an image to the clipboard.

        While testing, images are written to files instead of clipboard for later inspection and to not spam the clipboard.

        :param image: The image to be set to the clipboard.
        :type image: QPixmap | QImage
        """
        clipboard = QApplication.clipboard()
        if clipboard:
            if self.testing:
                if isinstance(image, QImage):
                    image = QPixmap.fromImage(image)
                self.pluginManager.Settings.incrementMeasurementNumber()
                image.save(self.pluginManager.Settings.getMeasurementFileName(f'_{self.name.replace(" ", "_")}_clipboard.png').as_posix())
            elif isinstance(image, QImage):
                clipboard.setImage(image)
            else:
                clipboard.setPixmap(image)

    @synchronized()
    def copyLineDataClipboard(self, line: 'Line2D') -> None:
        """Copy data as text to clipboard.

        :param line: The line with the data do be copied.
        :type line: Line2D
        """
        if line:
            text = ''
            for x, y in zip(cast('np.ndarray', line.get_xdata()), cast('np.ndarray', line.get_ydata()), strict=True):
                text += f'{x:12.2e}\t{y:12.2e}\n'
            if not self.testing:
                clipboard = QApplication.clipboard()
                if clipboard:
                    clipboard.setText(text)

    def setLabelMargin(self, ax: Axes, margin: float) -> None:
        """Set top margin only, to reserve space for file name label.

        :param ax: The axis to which to add the top margin
        :type ax: matplotlib.axis.Axes
        :param margin: The margin to add. 0.15 -> add 15 % margin
        :type margin: float

        """
        # not yet implemented https://stackoverflow.com/questions/49382105/set-different-margins-for-left-and-right-side
        ax.autoscale(enable=True)
        lim = ax.get_ylim()
        delta = cast('float', np.diff(lim))
        ax.set_ylim(lim[0], lim[1] + delta * margin)

    def addRightAxis(self, ax: Axes) -> None:
        """Add additional y labels on the right.

        :param ax: A matplotlib axes.
        :type ax: matplotlib.axes
        """
        # .tick_params(labelright=True) does only add labels
        # .tick_right() removes ticks on left
        # -> link second axis as a workaround
        axr = ax.twinx()
        axr.tick_params(direction='out', right=True)
        axr.sharey(ax)
        if ax.get_yscale() == 'log':
            axr.set_yscale('log')

    def tilt_xlabels(self, ax: Axes, rotation: int = 30) -> None:
        """Replace autofmt_xdate which is currently not compatible with constrained_layout.

        :param ax: The axis for which to tilt the labels.
        :type ax: matplotlib.axes.Axes
        :param rotation: The tilt angle in degrees, defaults to 30
        :type rotation: int, optional
        """
        # https://currents.soest.hawaii.edu/ocn_data_analysis/_static/Dates_Times.html
        for label in ax.get_xticklabels(which='major'):
            label.set_ha('right')  # type: ignore  # noqa: PGH003
            label.set_rotation(rotation)

    def getDefaultSettings(self) -> dict[str, dict[str, ParameterType | QTreeWidget | PARAMETERTYPE | QWidget | Callable | None]]:
        """Define a dictionary of :meth:`~esibd.core.parameterDict` which specifies default settings for this plugin.

        Overwrite or extend as needed to define specific settings that will be added to :ref:`sec:settings` section.
        NOTE: While settings with attributes will be automatically accessible, it is good practice to define the type of the attribute for type checking.

        :return: Settings dictionary
        :rtype: {:meth:`~esibd.core.parameterDict`}
        """
        ds = {}
        # ds[f'{self.name}/SettingName'] = parameterDict(...)  # noqa: ERA001
        return ds  # noqa: RET504

    def displayActive(self) -> bool:
        """Indicate if the display is active."""
        return self.display is not None and self.display.initializedDock

    def close(self) -> bool:
        """Close plugin cleanly without leaving any data or communication running.

        Extend to make sure your custom data and custom communication is closed.
        """
        return super().close()

    def closeUserGUI(self) -> None:
        """Perform additional closing actions when user is closing the plugin GUI."""
        self.closeGUI()

    def closeGUI(self) -> None:
        """Close the user interface but might keep data available in case the user interface is restored later.

        Closes all open references. Extend to save data and make hardware save if needed.
        """
        self.close()
        self.disconnect_all_signals()
        if self.dock and self.initializedDock:
            self.pluginManager.mainWindow.removeDockWidget(self.dock)
            self.dock.setParent(None)
            self.dock.deleteLater()
            self.dock = None

        if hasattr(self, 'fig'):
            plt.close(self.fig)
        self.fig = None
        self.canvas = None
        self.titleBar = None
        self.initializedGUI = False
        self.initializedDock = False

    def disconnect_all_signals(self) -> None:
        """Disconnect all signals."""
        for attr_name in dir(self.signalComm):
            attr = getattr(self.signalComm, attr_name)
            # Check if the attribute is a pyqtBoundSignal (instance of a pyqtSignal bound to this object)
            if hasattr(attr, 'disconnect'):
                with contextlib.suppress(Exception):
                    attr.disconnect()
                    # Some signals might already be disconnected or have no slots connected

    def getQtPen(self, channel: Channel | MetaChannel | Parameter) -> QPen:
        """Create a pen used for QT plots. Use custom definition for dashed lines that allows to see dashes in the legend.

        :param channel: Channel or associated parameter.
        :type channel: Channel
        :return: returns a QtPen with the corresponding color, linewidth and linestyle
        :rtype: QPen
        """
        if channel.getQtLineStyle() == Qt.PenStyle.DashLine:
            pen = pg.mkPen((channel.color), width=channel.linewidth, style=Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([2, 2])
            return pen
        return pg.mkPen((channel.color), width=channel.linewidth, style=channel.getQtLineStyle())


class StaticDisplay(Plugin):
    """Display :class:`~esibd.plugins.Device` data from file."""

    pluginType = PLUGINTYPE.DISPLAY
    parentPlugin: 'ChannelManager'

    def __init__(self, parentPlugin: 'ChannelManager', **kwargs) -> None:  # noqa: D107
        super().__init__(**kwargs)
        self.parentPlugin = parentPlugin  # another Plugin
        self.name = f'{parentPlugin.name} Static Display'
        self.file = Path()
        self.previewFileTypes = []  # extend in derived classes, define here to avoid cross talk between instances

    def initGUI(self) -> None:  # noqa: D102
        super().initGUI()
        self.legend = None
        self.outputLayout = QVBoxLayout()
        self.plotWidgetFont = QFont()
        self.plotWidgetFont.setPixelSize(15)
        self.staticPlotWidget = PlotWidget(parentPlugin=self)
        self.staticPlotWidget.showGrid(x=True, y=True, alpha=0.1)
        self.staticPlotWidget.showAxis('top')
        self.staticPlotWidget.getAxis('top').setStyle(showValues=False)
        self.staticPlotWidget.showLabel('top', show=False)
        self.staticPlotWidget.setAxisItems({'right': SciAxisItem('right')})
        self.staticPlotWidget.setAxisItems({'left': SciAxisItem('left')})
        self.staticPlotWidget.getAxis('left').setTickFont(self.plotWidgetFont)
        self.staticPlotWidget.getAxis('right').setTickFont(self.plotWidgetFont)
        self.staticPlotWidget.getAxis('bottom').setTickFont(self.plotWidgetFont)
        self.staticPlotWidget.setAxisItems({'bottom': pg.DateAxisItem()})
        self.staticPlotWidget.setLabel('bottom', '<font size="5">Time</font>')  # has to be after setAxisItems
        self.staticPlotWidget.enableAutoRange(x=True)
        self.outputLayout.addWidget(self.staticPlotWidget)
        self.staticPlotWidget.setLogMode(x=False, y=self.parentPlugin.logY)
        self.initFig()
        self.addContentLayout(self.outputLayout)
        self.initData()

    def initFig(self) -> None:  # noqa: D102
        if self.fig and rgb_to_hex(cast('tuple[float, float, float, float]', self.fig.get_facecolor())) != colors.bg:
            # need to create new fig to change matplotlib style
            plt.close(self.fig)
            self.fig = None
        if self.fig:
            self.fig.clf()  # reuse if possible
            self.fig.set_constrained_layout(True)  # type: ignore  # noqa: PGH003
            self.fig.set_dpi(getDPI())
        else:
            self.fig = plt.figure(constrained_layout=True, dpi=getDPI(), label=f'{self.name} staticDisplay figure')
            self.makeFigureCanvasWithToolbar(self.fig)
            self.outputLayout.addWidget(self.canvas)
        self.axes = []
        self.axes.append(self.fig.add_subplot(111))

    def finalizeInit(self) -> None:  # noqa: D102
        super().finalizeInit()
        self.copyAction = self.addAction(event=self.copyClipboard, toolTip=f'{self.name} to clipboard.', icon=self.imageClipboardIcon, before=self.aboutAction)
        self.plotEfficientAction = self.addStateAction(event=self.togglePlotType, toolTipFalse='Use matplotlib plot.', iconFalse=self.makeCoreIcon('mpl.png'),
                                                       toolTipTrue='Use pyqtgraph plot.', iconTrue=self.makeCoreIcon('pyqt.png'), attr='plotEfficient', before=self.copyAction)
        self.togglePlotType()
        self.staticPlotWidget.updateGrid()

    def getIcon(self, desaturate: bool = False) -> Icon:  # noqa: D102
        return self.parentPlugin.getIcon(desaturate=desaturate)

    def runTestParallel(self) -> None:  # noqa: D102
        if self.initializedDock:
            self.testControl(self.copyAction, value=True, delay=1)
            self.testControl(self.plotEfficientAction, not self.plotEfficientAction.state, 1)
        super().runTestParallel()

    # @synchronized() do not use same lock for extended version of already decorated super().copyClipboard()
    def copyClipboard(self) -> None:
        """Extend matplotlib based version to add support for pyqtgraph."""
        if self.plotEfficientAction.state:  # matplotlib
            super().copyClipboard()
        elif getDarkMode() and not getClipboardTheme():  # pyqt
            viewRange = self.staticPlotWidget.viewRange()
            try:
                setDarkMode(False)  # temporary switch to light mode
                self.updateTheme()  # use default light theme for clipboard
                self.staticPlotWidget.setRange(xRange=viewRange[0], yRange=viewRange[1], padding=0)
                self.processEvents()  # update GUI before grabbing
                self.imageToClipboard(self.staticPlotWidget.grab())
            except Exception as e:  # noqa: BLE001
                self.print(f'Error while plotting in light theme: {e}')
            finally:  # make sure darkmode is restored even after errors
                setDarkMode(True)  # restore dark theme
                self.updateTheme()  # restore dark theme
                self.staticPlotWidget.setRange(xRange=viewRange[0], yRange=viewRange[1], padding=0)
        else:
            self.imageToClipboard(self.staticPlotWidget.grab())

    def provideDock(self) -> bool:  # noqa: D102
        if super().provideDock():
            self.finalizeInit()
            self.afterFinalizeInit()
            return True
        return False

    def supportsFile(self, file: Path) -> bool:  # noqa: D102
        if super().supportsFile(file):
            return True
        if self.pluginManager.DeviceManager.supportsFile(file):
            with h5py.File(file, 'r') as h5File:
                return self.parentPlugin.name in h5File
        else:
            return False

    def loadData(self, file: Path, showPlugin: bool = True) -> None:  # noqa: D102
        # using linewidget to display
        self.file = file
        self.initData()
        self.provideDock()
        if self.loadDataInternal(file):
            self.outputChannels.reverse()  # reverse to plot first outputChannels on top of later outputChannels
            self.plot(update=True)
            self.raiseDock(showPlugin)
        else:
            self.closeGUI()
            self.print(f'Could not load file {file.name}.', flag=PRINT.VERBOSE)

    def togglePlotType(self) -> None:
        """Toggles between using matplotlib and pyqtgraph."""
        self.staticPlotWidget.setVisible(not self.plotEfficientAction.state)
        if self.canvas:
            self.canvas.setHidden(not self.plotEfficientAction.state)
        if self.navToolBar:
            for a in self.navToolBar.actions()[:-1]:  # last action is empty and undocumented
                a.setVisible(self.plotEfficientAction.state)
            if self.file and self.file.name and len(self.outputChannels) > 0:
                self.plot(update=True)

    def plot(self, update: bool = False) -> None:  # noqa: C901, PLR0912
        """Plot channels from file, using real channel information (color, linewidth, linestyle, ...) if available.

        :param update: Indicates if plot needs to be updated, defaults to False
        :type update: bool, optional
        """
        # as this is only done once we can plot all data without thinning
        if not self.initializedDock or self.loading or len(self.outputChannels) == 0:
            return
        if self.plotEfficientAction.state:
            self.axes[0].clear()
            self.axes[0].set_xlabel(self.TIME)
            if self.parentPlugin.logY:
                self.axes[0].set_yscale('log')
            self.tilt_xlabels(self.axes[0])
        else:
            self.staticPlotWidget.clear()
            self.legend = self.staticPlotWidget.addLegend(labelTextColor=colors.fg)  # before adding plots
        for outputChannel in self.outputChannels:
            inputRecordingData0 = self.inputChannels[0].getRecordingData()
            outputRecordingData = outputChannel.getRecordingData()
            if inputRecordingData0 is None or outputRecordingData is None:
                return
            length = min(inputRecordingData0.shape[0], outputRecordingData.shape[0])
            time_axis = inputRecordingData0[-length:]
            time_stamp_axis = [datetime.fromtimestamp(float(t)) for t in time_axis]
            y = self.parentPlugin.convertDataDisplay((outputRecordingData - outputChannel.recordingBackground)[:length]
                                           if isinstance(self.parentPlugin, Device) and self.parentPlugin.useBackgrounds and self.parentPlugin.subtractBackgroundActive()
                                           else outputRecordingData[:length])
            if not outputChannel.sourceChannel:
                if self.plotEfficientAction.state:
                    self.axes[0].plot(time_stamp_axis, y, label=f'{outputChannel.name} ({outputChannel.unit})')  # type: ignore  # noqa: PGH003
                else:
                    self.staticPlotWidget.plot(time_axis, y, name=f'{outputChannel.name} ({outputChannel.unit})')  # initialize empty plots
            elif outputChannel.sourceChannel.display:
                if outputChannel.smooth != 0:
                    # y = uniform_filter1d(y, outputChannel.smooth)  # revert to this if nan_policy becomes available https://github.com/scipy/scipy/pull/17393  # noqa: ERA001
                    y = smooth(y, outputChannel.smooth)
                if self.plotEfficientAction.state:
                    self.axes[0].plot(time_stamp_axis, y, label=f'{outputChannel.name} ({outputChannel.unit})',  # type: ignore  # noqa: PGH003
                                      color=outputChannel.color, linewidth=outputChannel.linewidth / 2, linestyle=outputChannel.linestyle)
                else:
                    self.staticPlotWidget.plot(time_axis, y, pen=self.getQtPen(outputChannel), name=f'{outputChannel.name} ({outputChannel.unit})')
        if self.plotEfficientAction.state:
            self.setLabelMargin(self.axes[0], 0.15)
            if self.navToolBar:
                self.navToolBar.update()  # reset history for zooming and home view
            if self.canvas:
                self.canvas.get_default_filename = lambda: self.file.with_suffix('.pdf').as_posix() if self.file else self.name  # set up save file dialog
            if self.file:
                self.labelPlot(self.file.name)
            legend = self.axes[0].legend(loc='best', prop={'size': 7}, frameon=False)
            legend.set_in_layout(False)
        elif update:
            self.staticPlotWidget.autoRange()  # required to trigger update

    def initData(self) -> None:
        """Clear all channels before (re-)initialization."""
        self.inputChannels: list[MetaChannel] = []
        self.outputChannels: list[MetaChannel] = []

    def loadDataInternal(self, file: Path) -> bool:
        """Load data in standard format. Overwrite in derived classes to add support for old file formats.

        :param file: File to load data from.
        :type file: pathlib.Path
        :return: Return True if loading was successful.
        :rtype: bool
        """
        with h5py.File(file, 'r') as h5file:
            if self.parentPlugin.name not in h5file:
                return False
            group = cast('h5py.Group', h5file[self.parentPlugin.name])
            if not (INPUTCHANNELS in group and OUTPUTCHANNELS in group):
                return False
            self.inputChannels.append(MetaChannel(parentPlugin=self, name=self.TIME, recordingData=cast('h5py.Dataset', group[INPUTCHANNELS])[self.TIME][:]))
            output_group = cast('h5py.Group', group[OUTPUTCHANNELS])
            for name, item in output_group.items():
                if '.' in name:
                    continue  # ignore extra recorded parameters
                if name.endswith('_BG'):
                    self.outputChannels[-1].recordingBackground = item[:]
                else:
                    self.outputChannels.append(MetaChannel(parentPlugin=self, name=name, recordingData=item[:], unit=item.attrs.get(UNIT, '')))
        return True

    def connectAllSources(self) -> None:
        """Connect all available source channels."""
        # NOTE: inputChannels are already connected on creation
        for channel in self.outputChannels:
            channel.connectSource()

    def reconnectSource(self, name: str) -> None:
        """Reconnect a specific source channel.

        :param name: name of channel to reconnect
        :type name: str
        """
        for channel in self.outputChannels:
            if channel.name == name:
                self.print(f'Source channel {channel.name} may have been lost. Attempt reconnecting.', flag=PRINT.DEBUG)
                channel.connectSource(giveFeedback=True)

    def generatePythonPlotCode(self) -> str:  # noqa: D102
        return f"""import h5py
import matplotlib.pyplot as plt
from datetime import datetime

inputChannels, outputChannels = [], []
class MetaChannel():
    def __init__(self, name, recordingData, initialValue=None, recordingBackground=None, unit=''):
        self.name = name
        self.recordingData = recordingData
        self.initialValue = initialValue
        self.recordingBackground = recordingBackground
        self.unit = unit

    @property
    def logY(self):
        if self.unit in ['mbar', 'Pa']:
            return True
        else:
            return False

with h5py.File('{self.file.as_posix() if self.file else ''}','r') as h5file:
    group = h5file['{self.parentPlugin.name}']

    inputChannels.append(MetaChannel(name='Time', recordingData=group['Input Channels']['Time'][:]))

    output_group = group['Output Channels']
    for name, data in output_group.items():
        if '.' in name:
            continue  # ignore extra recorded parameters
        if name.endswith('_BG'):
            outputChannels[-1].recordingBackground = data[:]
        else:
            outputChannels.append(MetaChannel(name=name, recordingData=data[:], unit=data.attrs['Unit']))

# replace following with your custom code
subtract_backgrounds = False  # switch to True to subtract background signals if available

fig=plt.figure(num='{self.parentPlugin.name} plot', constrained_layout=True, )
ax = fig.add_subplot(111)
ax.set_xlabel('Time')
{"ax.set_yscale('log')" if self.parentPlugin.logY else ''}

for i, output in enumerate(outputChannels):
    length = min(inputChannels[0].recordingData.shape[0], output.recordingData.shape[0])
    x = inputChannels[0].recordingData[-length:]
    y = (output.recordingData-output.recordingBackground)[:length] if output.recordingBackground is not None and subtract_backgrounds else output.recordingData[:length]
    ax.plot([datetime.fromtimestamp(float(_time)) for _time in x], y, label=f'{{output.name}} ({{output.unit}})')

ax.legend(loc = 'best', prop={{'size': 7}}, frameon=False)
fig.show()
        """

    def updateTheme(self) -> None:  # noqa: D102
        super().updateTheme()
        self.staticPlotWidget.setBackground(colors.bg)
        fg = colors.fg
        self.staticPlotWidget.getAxis('left').setTextPen(fg)
        self.staticPlotWidget.getAxis('top').setTextPen(fg)
        self.staticPlotWidget.getAxis('right').setTextPen(fg)
        self.staticPlotWidget.getAxis('bottom').setTextPen(fg)
        self.staticPlotWidget.setLabel('bottom', '<font size="5">Time</font>', color=fg)  # do not overwrite text!
        plotItem = cast('PlotItem', self.staticPlotWidget.getPlotItem())
        if plotItem:
            plotItem.xyLabel.setColor(fg)
        if self.legend:
            self.legend.setLabelTextColor(fg)
        self.plot()  # triggers update of legend
        if not self.loading:
            self.togglePlotType()


class LiveDisplay(Plugin):  # noqa: PLR0904
    """Live displays show the history of measured data over time.

    Use the start/pause icon to control data recording. The toolbar
    provides icons to initialize and stop acquisition, optionally
    subtract backgrounds, or export displayed data to the current session.
    Data is only collected if the corresponding live display is visible.
    The length of the displayed history is determined by the display time
    control in the tool bar.

    Frequently updating those plots is typically the computationally most
    expensive action. Thus you might want to reduce
    the number of displayed data points in the :ref:`acquisition settings<sec:acquisition_settings>`. This will make sure that
    the graphs are updated less frequently and select a smaller but
    consistent subset of data points for a smooth visualization. While
    PyQtGraph provides its own algorithms for down sampling data (accessible
    via the context menu), they tend to cause a flicker when updating data.
    """

    documentation = """Live displays show the history of measured data over time.
    Use the start/pause icon to control data recording. The toolbar
    provides icons to initialize and stop acquisition, optionally
    subtract backgrounds, or export displayed data to the current session.
    Data is only collected if the corresponding live display is visible.
    The length of the displayed history is determined by the display time
    control in the tool bar.

    Frequently updating those plots is typically the computationally most
    expensive action. Thus you might want to reduce
    the number of displayed data points in the Settings. This will make sure that
    the graphs are updated less frequently and select a smaller but
    consistent subset of data points for a smooth visualization. While
    PyQtGraph provides its own algorithms for down sampling data (accessible
    via the context menu), they tend to cause a flicker when updating data.
    """

    pluginType = PLUGINTYPE.LIVEDISPLAY
    useAdvancedOptions = True
    DISPLAYTIME = 'displayTime'
    parentPlugin: 'ChannelManager'

    class StackActionState(Enum):
        """States for the Stack Action."""

        VERTICAL = 'VERTICAL'
        HORIZONTAL = 'HORIZONTAL'
        STACKED = 'STACKED'

    class GroupActionState(Enum):
        """States for the Group Action."""

        ALL = 'ALL'
        DEVICE = 'DEVICE'
        UNIT = 'UNIT'
        GROUP = 'GROUP'

    def __init__(self, parentPlugin: 'ChannelManager', **kwargs) -> None:  # noqa: D107
        super().__init__(**kwargs)
        self.parentPlugin = parentPlugin  # should be a device that will define which channel to plot
        self.name = f'{parentPlugin.name} Live Display'
        self.stackedGraphicsLayoutWidget = None
        self.livePlotWidgets: list[PlotItem | PlotWidget | ViewBox] = []
        self.channelGroups = {}
        self.updateLegend = True
        self.dataFileType = f'_{self.parentPlugin.name.lower()}.dat.h5'
        self.previewFileTypes = [self.dataFileType]

    def initGUI(self) -> None:  # noqa: D102
        super().initGUI()
        self.plotSplitter = None
        if self.parentPlugin.pluginType in {PLUGINTYPE.INPUTDEVICE, PLUGINTYPE.OUTPUTDEVICE}:
            self.exportAction = self.addAction(event=lambda: cast('Device', self.parentPlugin).exportOutputData(useDefaultFile=False),
                                                toolTip=f'Save visible {self.parentPlugin.name} data to current session.',  # pylint: disable=unnecessary-lambda
                                                    icon=self.makeCoreIcon('database-export.png'))
            self.closeCommunicationAction = self.addAction(event=self.parentPlugin.closeCommunication, toolTip=f'Close {self.parentPlugin.name} communication.',
                                                           icon=self.makeCoreIcon('stop.png'))
            self.initAction = self.addAction(event=self.parentPlugin.initializeCommunication, toolTip=f'Initialize {self.parentPlugin.name} communication.',
                            icon=self.makeCoreIcon('rocket-fly.png'))
        if hasattr(self.parentPlugin, 'recordingAction'):
            self.recordingAction = self.addStateAction(event=lambda: self.parentPlugin.toggleRecording(manual=True),
                                                   toolTipFalse=f'Start {self.parentPlugin.name} data acquisition.', iconFalse=self.makeCoreIcon('play.png'),
                                                   toolTipTrue=f'Pause {self.parentPlugin.name} data acquisition.', iconTrue=self.makeCoreIcon('pause.png'))
            if self.parentPlugin.recordingAction is not None:
                self.recordingAction.state = self.parentPlugin.recordingAction.state
        if self.parentPlugin.pluginType in {PLUGINTYPE.INPUTDEVICE, PLUGINTYPE.OUTPUTDEVICE}:
            parentPlugin = cast('Device', self.parentPlugin)
            self.clearHistoryAction = self.addAction(event=parentPlugin.clearHistory, toolTip=f'Clear {parentPlugin.name} history.',
                                                      icon=self.makeCoreIcon('clipboard-empty.png'))
            self.clearHistoryAction.setVisible(False)  # usually not required as number of data points is already limited. only show in advanced mode
            if parentPlugin.useBackgrounds and parentPlugin.subtractBackgroundAction:
                self.subtractBackgroundAction = self.addStateAction(toolTipFalse=f'Subtract background for {parentPlugin.name}.', iconFalse=self.makeCoreIcon('eraser.png'),
                                                        toolTipTrue=f'Ignore background for {parentPlugin.name}.', iconTrue=self.makeCoreIcon('eraser.png'),
                                                        event=self.subtractBackgroundChanged)
                self.subtractBackgroundAction.state = parentPlugin.subtractBackgroundAction.state
                self.addAction(event=parentPlugin.setBackground, toolTip=f'Set current value as background for {self.parentPlugin.name}.',
                                icon=self.makeCoreIcon('eraser--pencil.png'))
        self.stackAction = self.addMultiStateAction(states=[MultiState(self.StackActionState.VERTICAL, 'Stack axes horizontally.', self.makeCoreIcon('stack_horizontal.png')),
                                                            MultiState(self.StackActionState.HORIZONTAL, 'Stack axes on top of each other.', self.makeCoreIcon('stack_top.png')),
                                                            MultiState(self.StackActionState.STACKED, 'Stack axes vertically.', self.makeCoreIcon('stack_vertical.png'))],
                                                        event=lambda: (self.initFig(), self.plot(apply=True)), attr='stackMode')
        self.groupAction = self.addMultiStateAction(states=[MultiState(self.GroupActionState.ALL, 'Group channels by device.', self.makeCoreIcon('group_device.png')),
                                                            MultiState(self.GroupActionState.DEVICE, 'Group channels by unit.', self.makeCoreIcon('group_unit.png')),
                                                            MultiState(self.GroupActionState.UNIT, 'Group channels by group parameter.', self.makeCoreIcon('group_group.png')),
                                                            MultiState(self.GroupActionState.GROUP, 'Show all channels together.', self.makeCoreIcon('group_all.png'))],
                                                        event=lambda: (self.initFig(), self.plot(apply=True)), attr='groupMode')
        self.displayTimeComboBox = RestoreFloatComboBox(parentPlugin=self, default='2', items='-1, 0.2, 1, 2, 3, 5, 10, 60, 600, 1440', attr=self.DISPLAYTIME,
                                                        event=self.displayTimeChanged, minimum=.2, maximum=3600,
                                                        toolTip=f'Length of displayed {self.parentPlugin.name} history in min.\nWhen -1, all history is shown.')
        self.autoScaleAction = self.addStateAction(event=self.autoScaleChanged,
                                                    toolTipFalse='Scale x manually.', iconFalse=self.makeCoreIcon('scaleX_manual.png'),
                                                   toolTipTrue='Scale x automatically.', iconTrue=self.makeCoreIcon('scaleX_auto.png'), restore=False, defaultState=False)

    def finalizeInit(self) -> None:  # noqa: D102
        super().finalizeInit()
        self.copyAction = self.addAction(event=self.copyClipboard, toolTip=f'{self.name} to clipboard.', icon=self.imageClipboardIcon, before=self.aboutAction)
        if self.titleBar:
            self.titleBar.insertWidget(self.copyAction, self.displayTimeComboBox)
            self.titleBar.insertAction(self.copyAction, self.autoScaleAction)
        self.plot(apply=True)

    def toggleAdvanced(self, advanced: 'bool | None' = False) -> None:  # noqa: ARG002, D102
        if hasattr(self, 'clearHistoryAction'):
            self.clearHistoryAction.setVisible(self.advancedAction.state)

    def displayTimeChanged(self) -> None:
        """Adjust displayed section to display time and activates autorange."""
        self.autoScaleAction.state = False
        self.updateMouseEnabled(x=False, y=True)
        self.autoRangeY()
        self.plot(apply=True)

    def subtractBackgroundChanged(self) -> None:
        """Relays change of background subtraction to corresponding action in parentPlugin."""
        if (isinstance(self.parentPlugin, Device)) and self.parentPlugin.subtractBackgroundAction:
            self.parentPlugin.subtractBackgroundAction.state = self.subtractBackgroundAction.state
            self.parentPlugin.subtractBackgroundAction.triggered.emit(self.subtractBackgroundAction.state)

    def getDisplayTime(self) -> float:
        """Get displaytime independent of displayTimeComboBox."""
        # displayTimeComboBox does not exist if display is hidden
        return qSet.value(f'{self.name}/{self.DISPLAYTIME}', 2, type=float)

    def clearPlot(self) -> None:
        """Clear all references to plotCurves, plotItems, and legends.

        To be recreated if needed.
        """
        for channel in self.parentPlugin.getChannels():
            channel.clearPlotCurve()
        for livePlotWidget in self.livePlotWidgets:
            livePlotWidget.hide()
            livePlotWidget.deleteLater()
        if self.stackedGraphicsLayoutWidget:
            try:
                self.stackedGraphicsLayoutWidget.hide()
                self.stackedGraphicsLayoutWidget.deleteLater()
            except RuntimeError:
                pass  # Ignore if already been deleted
            finally:
                self.stackedGraphicsLayoutWidget = None
        if self.plotSplitter:
            self.plotSplitter.hide()
            self.plotSplitter.deleteLater()
            self.plotSplitter = None
        self.livePlotWidgets = []
        self.updateLegend = True

    def getGroups(self) -> dict[str, list[Channel | Parameter]]:
        """Return Channel groups, sorted by device, unit, group, or unsorted.

        :return: Channel groups.
        :rtype: dict[str, list[esibd.core.Channel]]
        """
        self.channelGroups: dict[str, list[Channel | Parameter]] = {}
        activeChannels = self.parentPlugin.getActiveChannelsAndParameters()  # getActiveChannels()
        match self.groupAction.state:
            case self.GroupActionState.DEVICE:
                groupLabels = list({channel.getDevice().name for channel in activeChannels if channel.display})
                groups = [[] for _ in range(len(groupLabels))]
                [groups[groupLabels.index(channel.getDevice().name)].append(channel) for channel in activeChannels if channel.display]
            case self.GroupActionState.UNIT:
                groupLabels = list({channel.getDisplayUnit() for channel in activeChannels if channel.display})
                groups = [[] for _ in range(len(groupLabels))]
                [groups[groupLabels.index(channel.getDisplayUnit())].append(channel) for channel in activeChannels if channel.display]
            case self.GroupActionState.GROUP:
                groupLabels = list({channel.displayGroup for channel in activeChannels if channel.display})
                groups = [[] for _ in range(len(groupLabels))]
                [groups[groupLabels.index(channel.displayGroup)].append(channel) for channel in activeChannels if channel.display]
            case _:  # ALL
                groupLabels = [self.parentPlugin.name]
                groups = [[channel for channel in activeChannels if channel.display]]
        for label, group in zip(groupLabels, groups, strict=True):
            self.channelGroups[label] = group
        self.channelGroups = dict(sorted(self.channelGroups.items()))  # sort by groupLabel
        return self.channelGroups

    # @synchronized() called by updateTheme, copy clipboard, ... cannot decorate without causing deadlock
    def initFig(self) -> None:  # noqa: C901, D102, PLR0912, PLR0915
        if not self.waitForCondition(condition=lambda: not self.parentPlugin.plotting, timeoutMessage='init figure.', timeout=1):
            return  # NOTE: using the self.parentPlugin.plotting flag instead of a lock, is more resilient as it works across multiple functions and nested calls
        self.print('initFig', flag=PRINT.DEBUG)
        self.parentPlugin.plotting = True
        self.clearPlot()
        self.plotWidgetFont = QFont()
        self.plotWidgetFont.setPixelSize(13)
        self.plotSplitter = QSplitter()  # create new plotSplitter as old widgets are not garbage collected fast enough for copyClipboard
        self.plotSplitter.setStyleSheet('QSplitter::handle{width:0px; height:0px;}')
        self.noPlotLabel = QLabel('Nothing to plot. Display a channel with data in the selected display time.')
        self.noPlotLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.plotSplitter.addWidget(self.noPlotLabel)
        self.addContentWidget(self.plotSplitter)
        self.noPlotLabel.setVisible(len(self.getGroups()) == 0)
        for i, (groupLabel, group) in enumerate(self.getGroups().items()):
            logY = all(channel.logY for channel in group)
            if self.stackAction.state in {self.StackActionState.HORIZONTAL, self.StackActionState.VERTICAL}:
                livePlotWidget = PlotWidget(parentPlugin=self, groupLabel=groupLabel)
                self.plotSplitter.addWidget(livePlotWidget)
                livePlotWidget.init()
                livePlotWidget.setLogMode(x=False, y=logY)
                self.livePlotWidgets.append(livePlotWidget)
                livePlotWidget.getViewBox().userMouseEnabledChanged.connect(self.updateMouseEnabled)
                if self.stackAction.state == self.StackActionState.VERTICAL:
                    livePlotWidget.addLegend(labelTextColor=colors.fg, colCount=3, offset=0.15, labelTextSize='8pt')  # before adding plots
                    self.plotSplitter.setOrientation(Qt.Orientation.Vertical)
                    if i < len(self.channelGroups) - 1:  # only label bottom x axis
                        livePlotWidget.hideAxis('bottom')
                else:  # self.stackAction.state == self.stackAction.labels.horizontal:
                    livePlotWidget.addLegend(labelTextColor=colors.fg, colCount=1, offset=0.15, labelTextSize='8pt')  # before adding plots
                    self.plotSplitter.setOrientation(Qt.Orientation.Horizontal)
                if i > 0:  # link to previous
                    livePlotWidget.setXLink(self.livePlotWidgets[0])
                livePlotWidget.finalizeInit()
            else:  # self.stackAction.state == self.stackAction.labels.stacked:
                # Based on https://github.com/pyqtgraph/pyqtgraph/blob/master/pyqtgraph/examples/MultiplePlotAxes.py
                # had to use older version to allow for multiple left axes https://stackoverflow.com/questions/42931474/how-can-i-have-multiple-left-axisitems-with-the-same-alignment-position-using-py
                # should soon become standard functionality and then this can be replaced https://github.com/pyqtgraph/pyqtgraph/pull/1359
                # self.livePlotWidgets[0] will be a plotItem the following elements will be linked viewBoxes
                plotColumn = (len(self.channelGroups) + 1) // 2 - 1
                if i == 0:
                    self.stackedGraphicsLayoutWidget = pg.GraphicsLayoutWidget()
                    self.stackedGraphicsLayoutWidget.setBackground(colors.bg)
                    self.plotSplitter.addWidget(self.stackedGraphicsLayoutWidget)
                    livePlotWidget = PlotItem(showXY=False)
                    self.stackedGraphicsLayoutWidget.addItem(livePlotWidget, 0, plotColumn, rowspan=2)  # type: ignore  # noqa: PGH003 rowspan required to align plots
                    livePlotWidget.init()
                    livePlotWidget.showGrid(x=False, y=False)
                    if len(self.channelGroups) == 1:
                        livePlotWidget.showAxis('right')
                        livePlotWidget.showLabel('right', show=False)
                        livePlotWidget.getAxis('right').setStyle(showValues=False)
                    livePlotWidget.axis_leftright = livePlotWidget.getAxis('left')
                    if len(self.channelGroups) > 2:  # noqa: PLR2004 need extra axis after using both left and right
                        livePlotWidget.dummyAx = pg.AxisItem(orientation='bottom')  # Blank axis used for aligning extra y axes
                        livePlotWidget.dummyAx.setPen(colors.bg)
                        livePlotWidget.dummyAx.setStyle(showValues=False)
                        livePlotWidget.dummyAx.setHeight(38)  # empirical constant
                        self.stackedGraphicsLayoutWidget.addItem(livePlotWidget.dummyAx, 1, 0)
                    if livePlotWidget.vb:
                        livePlotWidget.vb.sigResized.connect(self.updateStackedViews)
                    livePlotWidget.addLegend(labelTextColor=colors.fg, colCount=3, offset=0.15, labelTextSize='8pt')
                    livePlotWidget.setLogMode(y=logY)  # set for PlotItem
                    livePlotWidget.finalizeInit()
                else:
                    livePlotWidget = ViewBox()
                    livePlotWidget0 = cast('PlotItem | PlotWidget', self.livePlotWidgets[0])
                    scene = livePlotWidget0.scene()
                    if scene:
                        scene.addItem(livePlotWidget)
                    if i == 1:  # use original right axis
                        livePlotWidget0.showAxis('right')
                        livePlotWidget.axis_leftright = livePlotWidget0.getAxis('right')
                    else:
                        livePlotWidget.axis_leftright = SciAxisItem('left' if np.mod(i, 2) == 0 else 'right')
                    livePlotWidget.axis_leftright.linkToView(livePlotWidget)
                    livePlotWidget.setXLink(self.livePlotWidgets[0])
                    livePlotWidget.axis_leftright.setLogMode(y=logY)  # set for AxisItem instead of ViewBox
                    livePlotWidget.userMouseEnabledChanged.connect(self.updateMouseEnabled)
                if i > 1 and self.stackedGraphicsLayoutWidget:
                    columnOffset = (i) // 2
                    self.stackedGraphicsLayoutWidget.addItem(livePlotWidget.axis_leftright, 0, plotColumn - columnOffset if np.mod(i, 2) == 0 else plotColumn + columnOffset)
                livePlotWidget.axis_leftright.setLabel(groupLabel)
                livePlotWidget.axis_leftright.setTickFont(self.plotWidgetFont)
                livePlotWidget.axis_leftright.setPen(pg.mkPen(color=colors.fg, width=2))
                livePlotWidget.axis_leftright.setTextPen(pg.mkPen(color=colors.fg))
                livePlotWidget.axis_leftright.setWidth(60 if logY else 40)
                livePlotWidget.setMouseEnabled(x=False, y=True)
                self.livePlotWidgets.append(livePlotWidget)
            if self.stackAction.state == self.StackActionState.STACKED:
                self.updateStackedViews()  # call after all initialized
        self.parentPlugin.plotting = False

    def updateStackedViews(self) -> None:
        """Ensure stacked views use same x axis."""
        if self.stackAction.state == self.StackActionState.STACKED and len(self.livePlotWidgets) > 0:
            livePlotWidget0 = cast('PlotWidget | PlotItem', self.livePlotWidgets[0])
            if livePlotWidget0.vb:
                for livePlotWidget in self.livePlotWidgets[1:]:
                    livePlotWidget = cast('ViewBox', livePlotWidget)
                    livePlotWidget.setGeometry(QRectF(livePlotWidget0.vb.sceneBoundingRect()))
                    livePlotWidget.linkedViewChanged(livePlotWidget0.vb, livePlotWidget.XAxis)

    def updateMouseEnabled(self, x: bool, y: 'bool | None' = None) -> None:
        """Set same x mouse enabled state for all livePlotWidgets.

        :param x: x mouse enabled
        :type x: bool
        :param y: y mouse enabled
        :type y: bool
        """
        if self.autoScaleAction.state != x:
            self.autoScaleAction.state = x
        if len(self.livePlotWidgets) > 0:
            for livePlotWidget in self.livePlotWidgets:
                viewBox = livePlotWidget.getViewBox()
                if viewBox and isinstance(livePlotWidget, (PlotItem, PlotWidget)) and (viewBox.mouseEnabled()[0] is not x):
                    livePlotWidget.setMouseEnabled(x=x, y=y if y is not None else viewBox.mouseEnabled()[1])

    def autoRangeY(self, autorange: bool = True) -> None:
        """Auto range y axis of all livePlotWidgets.

        :param autorange: Indicates if autorange should be applied, defaults to True
        :type autorange: bool, optional
        """
        if autorange:
            for livePlotWidget in self.livePlotWidgets:
                if isinstance(livePlotWidget, (PlotItem, PlotWidget)):
                    livePlotWidget.enableAutoRange(x=False, y=True)

    def autoScaleChanged(self) -> None:
        """Toggle between manual and automatic x scaling and scales y if needed."""
        self.updateMouseEnabled(x=self.autoScaleAction.state, y=None)
        self.autoRangeY(not self.autoScaleAction.state)
        self.plot(apply=True)

    def getIcon(self, desaturate: bool = False) -> Icon:  # noqa: D102
        return self.parentPlugin.getIcon(desaturate=desaturate)

    def runTestParallel(self) -> None:  # noqa: D102
        if self.initializedDock:
            # init, start, pause, stop acquisition will be tested by DeviceManager
            self.testControl(self.copyAction, value=True)
            # self.testControl(self.clearHistoryAction, True)  # keep history, test manually if applicable  # noqa: ERA001
            for _ in range(3):
                self.testControl(self.stackAction, value=0)  # value does not matter, just rolling
                self.app.processEvents()
                time.sleep(1)
            if hasattr(self, 'exportAction') and self.parentPlugin.file:
                self.testControl(self.exportAction, value=True)
                if self.waitForCondition(condition=lambda parentPlugin=self.parentPlugin: parentPlugin.file.name, timeoutMessage='saving data', timeout=10):
                    self.pluginManager.Explorer.activeFileFullPath = self.parentPlugin.file
                    self.pluginManager.Explorer.displayContentSignal.emit()  # call displayContent in main thread
                    self.pluginManager.Explorer.loadingContent = True
                    self.waitForCondition(condition=lambda: not self.pluginManager.Explorer.loadingContent,
                                           timeoutMessage=f'displaying content of {self.pluginManager.Explorer.activeFileFullPath.name}', timeout=10)
                    if self.parentPlugin.staticDisplay:
                        self.parentPlugin.staticDisplay.testPythonPlotCode(closePopup=True)
        super().runTestParallel()

    @synchronized()
    def copyClipboard(self) -> None:
        """Extend matplotlib based version to add support for pyqtgraph."""
        self.print('copyClipboard', flag=PRINT.DEBUG)
        # Test with light and dark mode
        # Test with and without mouse enabled
        # Test with and without active recording
        if len(self.livePlotWidgets) == 0 or not self.plotSplitter:
            self.print('Plot not initialized', flag=PRINT.WARNING)
            return
        if getDarkMode() and not getClipboardTheme():
            viewRange = self.livePlotWidgets[0].viewRange()
            viewBox = self.livePlotWidgets[0].getViewBox()
            restoreAutoRange = not viewBox.mouseEnabled()[0] if viewBox else False  # as mouse enabled is linked all livePlotWidgets will have the same state
            sizes = self.plotSplitter.sizes()
            try:
                setDarkMode(False)  # temporary switch to light mode
                self.parentPlugin.clearPlot()
                self.initFig()
                self.plotSplitter.setSizes(sizes)
                self.processEvents()  # update GUI before restoring range
                self.livePlotWidgets[0].setMouseEnabled(x=True, y=True)  # prevents autoscaling
                self.livePlotWidgets[0].setRange(xRange=viewRange[0], yRange=viewRange[1], padding=0)
                self.plot(apply=True)
                self.processEvents()  # update GUI before grabbing
                self.imageToClipboard(self.plotSplitter.grab())
            except Exception as e:  # noqa: BLE001
                self.print(f'Error while plotting in light theme: {e}')
            finally:  # make sure darkmode is restored even after errors
                setDarkMode(True)  # restore dark theme
                self.parentPlugin.clearPlot()
                self.initFig()
                self.plotSplitter.setSizes(sizes)
                if not restoreAutoRange:
                    self.livePlotWidgets[0].setMouseEnabled(x=True, y=True)
                    self.processEvents()  # update GUI before restoring range
                    self.livePlotWidgets[0].setRange(xRange=viewRange[0], yRange=viewRange[1], padding=0)
                self.plot(apply=True)
        else:
            self.imageToClipboard(self.plotSplitter.grab())

    def provideDock(self) -> bool:  # noqa: D102
        if super().provideDock():
            self.finalizeInit()
            self.afterFinalizeInit()
            return True
        return False

    def getTimeAxes(self) -> dict[str, tuple[int, int | None, int, np.ndarray[Any, np.dtype[np.float64]]]]:
        """Return the time axes for all devices.

        :return: List of time axes for all relevant devices.
        :rtype: dict[str, tuple[int, int, int, np.ndarray]]
        """
        timeAxes: dict[str, tuple[int, int | None, int, np.typing.NDArray[np.float64]]] = {}
        for device in list({channel.getDevice() for channel in self.parentPlugin.getChannels()}):
            # device could be a general ChannelManager, in which case this call is directed to all real devices corresponding to the managed channels
            # time axis should only be called once per device in each plot cycle
            # all new entries including time are added in one step to avoid any chance of unequal array sizes
            if isinstance(device, Device):
                time_axis = device.time.get()
                i_min = 0
                i_max = 0
                n = 1
                timeAxis: np.typing.NDArray[np.float64] = np.empty(0, dtype=np.float64)
                livePlotWidget0 = cast('PlotItem | PlotWidget', self.livePlotWidgets[0])
                viewBox = livePlotWidget0.getViewBox()
                if (len(self.livePlotWidgets) > 0 and viewBox and viewBox.mouseEnabled()[0] and livePlotWidget0.getAxis('bottom').range[0] != 0):  # range determined by user
                    t_min, t_max = livePlotWidget0.getAxis('bottom').range  # is [0, 1] if nothing has been plotted before, use display time in this case
                    i_min = int(np.argmin(np.abs(time_axis - t_min)))
                    i_max = int(np.argmin(np.abs(time_axis - t_max)))
                    n = max(int((i_max - i_min) / self.pluginManager.DeviceManager.max_display_size), 1) if self.pluginManager.DeviceManager.limit_display_size else 1
                    timeAxis = cast('np.typing.NDArray[np.float64]', device.time.get(index_min=i_min, index_max=i_max, n=n))
                elif device.time.size > 0:  # displayTime determines range
                    i_min = int(np.argmin(np.abs(time_axis - (time.time() - self.getDisplayTime() * 60))) if self.getDisplayTime() != -1 else 0)
                    i_max = None
                    t_length = time_axis.shape[0] - i_min  # number of indices within displaytime before thinning
                    # determine by how much to limit number of displayed data points
                    n = max(int(t_length / self.pluginManager.DeviceManager.max_display_size), 1) if self.pluginManager.DeviceManager.limit_display_size else 1
                    timeAxis = cast('np.typing.NDArray[np.float64]', device.time.get(index_min=i_min, n=n))
                timeAxes[device.name] = i_min, i_max, n, timeAxis
        return timeAxes

    def plot(self, apply: bool = False) -> None:  # noqa: C901, PLR0911, PLR0912
        """Plot the enabled and initialized channels in the main output plot.

        The x axis is either time or a selected channel.

        :param apply: Apply most recent data, otherwise update rate depends on data thinning.
        :type apply: bool
        """
        if not self.initializedDock:  # ignore request to plot if livedisplay is not visible
            return
        if len(self.livePlotWidgets) != len(self.getGroups()):
            self.initFig()
            apply = True  # need to plot everything after initializing
        if len(self.livePlotWidgets) == 0:
            return
        if (not self.initializedDock or self.parentPlugin.pluginManager.loading
            or self.pluginManager.Settings.loading):
            return  # values not yet available
        if self.parentPlugin.plotting:
            return  # previous plot not yet processed
        if isinstance(self.parentPlugin, Device) and self.parentPlugin.time.size < 1:  # no data
            return
        if self.pluginManager.resizing:
            self.print('Suspend plotting while resizing.', flag=PRINT.VERBOSE)
            return
        if any(livePlotWidget.dragging for livePlotWidget in self.livePlotWidgets):
            self.print('Suspend plotting while dragging.', flag=PRINT.VERBOSE)
            return
        self.parentPlugin.plotting = True  # protect from recursion
        # flip array to speed up search of most recent data points
        # may return None if no value is older than displaytime
        timeAxes = self.getTimeAxes()
        for livePlotWidget, channels in zip(self.livePlotWidgets, self.channelGroups.values(), strict=True):
            self.plotGroup(livePlotWidget, timeAxes, channels, apply)
        viewBox = self.livePlotWidgets[0].getViewBox()
        if viewBox and not viewBox.mouseEnabled()[0]:
            if self.getDisplayTime() != -1:
                self.livePlotWidgets[0].setXRange(time.time() - self.getDisplayTime() * 60, time.time())  # x axis linked to all others
            else:
                self.livePlotWidgets[0].setXRange(self.parentPlugin.minTime(), time.time())

        if self.updateLegend:
            for livePlotWidget, channels in zip(self.livePlotWidgets, self.channelGroups.values(), strict=True):
                if isinstance(livePlotWidget, (PlotItem, PlotWidget)):
                    legend = livePlotWidget.legend
                    if legend:
                        legend.clear()
                else:
                    legend = cast('PlotItem | PlotWidget', self.livePlotWidgets[0]).legend  # use first one (already cleared)
                if legend:
                    for channel in channels:
                        if channel.plotCurve:
                            legend.addItem(channel.plotCurve, name=channel.plotCurve.name())
            self.updateLegend = False

        if self.parentPlugin.pluginType in {PLUGINTYPE.INPUTDEVICE, PLUGINTYPE.OUTPUTDEVICE} and self.parentPlugin.recording:
            cast('Device', self.parentPlugin).measureInterval()
        self.parentPlugin.plotting = False

    def plotGroup(self, livePlotWidget: PlotWidget | PlotItem | ViewBox, timeAxes: dict[str, tuple[int, int | None, int, np.ndarray]],
                   channels: list[Channel | Parameter], apply: bool) -> None:
        """Plot a group of Channels.

        :param livePlotWidget: The PlotWidget used to display Channel values.
        :type livePlotWidget: PlotWidget
        :param timeAxes: The time axis.
        :type timeAxes: np.ndarray
        :param channels: List of Channels in group.
        :type channels: list[esibd.core.Channel]
        :param apply: Recreates all plots from scratch if True.
        :type apply: bool
        """
        for channel in channels[::-1]:  # reverse order so Channels on top of list are also plotted on top of others
            self.plotChannel(livePlotWidget, timeAxes, channel, apply)

    def plotChannel(self, livePlotWidget: PlotWidget | PlotItem | ViewBox, timeAxes: dict[str, tuple[int, int | None, int, np.ndarray]],
                    channel: 'Channel | Parameter', apply: bool) -> None:
        """Plot a channel.

        :param livePlotWidget: The PlotWidget used to display Channel values.
        :type livePlotWidget: PlotWidget
        :param timeAxes: The time axis.
        :type timeAxes: np.ndarray
        :param channel: Channels to plot.
        :type channel: esibd.core.Channel
        :param apply: Recreates all plots from scratch if True.
        :type apply: bool
        """
        device = channel.getDevice()
        if ((channel.enabled or not channel.real) and channel.display and channel.time and channel.time.size != 0 and  # noqa: PLR0916, PLR1702
                channel.convertDataDisplay and isinstance(device, Device)):
            i_min, i_max, n, timeAxis = timeAxes[device.name]
            if apply or np.remainder(i_min, n) == 0:  # otherwise no update required
                # ignoring extremely rare edge case where one channel has been patched with np.nan and i_min is not 0 and not changing
                # as it corresponds to a time without display time.
                if timeAxis.shape[0] > 1:  # need at least 2 data points to plot connecting line segment
                    # plotting is very expensive, array manipulation is negligible even with 50000 data points per channel
                    # channel should at any point have as many data points as timeAxis (missing bits will be filled with nan as soon as new data comes in)
                    # however, cant exclude that one data point added between definition of timeAxis and y
                    y = channel.convertDataDisplay(channel.getValues(subtractBackground=device.subtractBackgroundActive(),
                                          index_min=i_min, index_max=i_max, n=n))  # ignore last data point, possibly added after definition of timeAx  #, _callSync='off'
                    if y.shape[0] == 0 or all(np.isnan(y)):
                        # cannot draw if only np.nan (e.g. when zooming into old data where a channel did not exist or was not enabled and data was padded with np.nan)
                        channel.clearPlotCurve()
                    else:
                        length = min(timeAxis.shape[0], y.shape[0])  # make sure x any y have same shape
                        if channel.smooth != 0:
                            # y = uniform_filter1d(y, channel.smooth)  # revert once nan_policy implemented  # noqa: ERA001
                            y = smooth(y, channel.smooth)
                        if not channel.plotCurve:
                            # only create new plotCurve if it is actually going to be used
                            if isinstance(livePlotWidget, (PlotItem, PlotWidget)):
                                channel.plotCurve = cast('PlotDataItem', livePlotWidget.plot(pen=self.getQtPen(channel),
                                                                                             name=f'{channel.legendName} ({channel.getDisplayUnit()})'))  # initialize empty plots
                            else:  # ViewBox
                                channel.plotCurve = cast('PlotDataItem', PlotDataItem(pen=self.getQtPen(channel),
                                                                                      name=f'{channel.legendName} ({channel.getDisplayUnit()})'))  # initialize empty plots
                                channel.plotCurve.setLogMode(xState=False, yState=channel.logY)  # has to be set for axis and ViewBox https://github.com/pyqtgraph/pyqtgraph/issues/2603
                                livePlotWidget.addItem(channel.plotCurve)  # works for plotWidgets as well as viewBoxes
                                legend = cast('PlotWidget | PlotItem', self.livePlotWidgets[0]).legend
                                if legend:
                                    channel.plotCurve.curveLegend = legend  # have to explicitly remove from legend before deleting!
                            channel.plotCurve.curveParent = livePlotWidget
                            self.updateLegend = True  # curve added
                            # saving curveParent allows to remove plotCurve from curveParent before deleting, preventing curveParent from trying to access deleted object.
                        channel.plotCurve.setData(timeAxis[:length], y[:length])
                else:
                    channel.clearPlotCurve()
        else:
            channel.clearPlotCurve()

    def closeUserGUI(self) -> None:  # noqa: D102
        self.parentPlugin.toggleLiveDisplayAction.state = False  # state is remembered and restored from setting
        super().closeUserGUI()

    def closeGUI(self) -> None:  # noqa: D102
        if not self.pluginManager.closing:
            self.parentPlugin.clearPlot()  # plotCurve references will be deleted and have to be recreated later if needed
            self.pluginManager.toggleTitleBarDelayed(update=True)
        super().closeGUI()

    def updateTheme(self) -> None:  # noqa: D102
        super().updateTheme()
        if not self.loading:
            self.parentPlugin.clearPlot()  # recreate plot with new colors
            self.plot(apply=True)


class ChannelManager(Plugin):  # noqa: PLR0904
    """Generic plugin with a tree of channels. This can be extended to implement device plugins, plugins with relay channels, and more."""

    name = 'Channel Manager'  # overwrite after inheriting
    version = '1.0'
    pluginType = PLUGINTYPE.CONTROL  # overwrite after inheriting
    optional = False
    useAdvancedOptions = True
    recordingAction: 'StateAction | None'
    maxErrorCount = 25  # Note, will be replaced by setting for devices

    signalComm: 'SignalCommunicate'
    logY: 'bool | None' = None
    maxDataPoints: int
    inout: INOUT
    unit: str
    changeLog: 'list[str] | None'

    class SignalCommunicate(Plugin.SignalCommunicate):  # signals that can be emitted by external threads
        """Bundle pyqtSignals."""

        plotSignal = pyqtSignal(bool)
        """Signal that triggers plotting of history."""

    StaticDisplay = StaticDisplay
    """Defined here so that overwriting only affects single instance in device and not all instances.

    :meta private:
    """
    LiveDisplay = LiveDisplay
    """Defined here so that overwriting only affects single instance in device and not all instances.

    :meta private:
    """
    channels: list[Channel]
    """List of :class:`channels<esibd.core.Channel>`."""
    channelType = Channel
    """Type of :class:`~esibd.core.Channel` used by the device. Overwrite by appropriate type in derived classes."""
    defaultChannel: Channel
    """The default channel is an instance of channelType that is only used for reference when loading and saving the channel configuration."""
    staticDisplay: StaticDisplay  # | None ignore intentionally
    """Internal plugin to display data from file."""
    liveDisplay: LiveDisplay  # | None ignore intentionally
    """Internal plugin to display data in real time."""
    useBackgrounds: bool = False
    """If True, the device implements controls to define and subtract background signals."""
    useDisplays = True
    """use liveDisplay, StaticDisplay, ChannelPlot, and all related functionality."""
    useMonitors = False
    """Use record monitors and compare them to set points."""
    useOnOffLogic = False
    """Creates an Action in the DeviceManager that handles turning key functions on and off."""

    class ChannelPlot(Plugin):
        """Simplified version of the Line plugin for plotting channels."""

        version = '1.0'
        pluginType = PLUGINTYPE.DISPLAY
        parentPlugin: 'ChannelManager'

        def __init__(self, parentPlugin: 'ChannelManager', pluginManager: PluginManager, dependencyPath: Path, sourceCodePath: Path) -> None:  # noqa: D107
            super().__init__(pluginManager, dependencyPath=dependencyPath, sourceCodePath=sourceCodePath)
            self.parentPlugin = parentPlugin
            self.name = f'{parentPlugin.name} Channel Plot'

        def initGUI(self) -> None:  # noqa: D102
            super().initGUI()
            self.initFig()

        def initFig(self) -> None:  # noqa: D102
            self.provideFig()
            if self.fig:
                self.axes.append(self.fig.add_subplot(111))
            self.line = None  # type: ignore  # noqa: PGH003

        def provideDock(self) -> bool:  # noqa: D102
            if super().provideDock():
                self.finalizeInit()
                self.afterFinalizeInit()
                return True
            return False

        def finalizeInit(self) -> None:  # noqa: D102
            super().finalizeInit()
            self.copyAction = self.addAction(event=self.copyClipboard, toolTip=f'{self.name} to clipboard.', icon=self.imageClipboardIcon, before=self.aboutAction)

        def getIcon(self, desaturate: bool = False) -> Icon:  # noqa: D102
            return self.parentPlugin.getIcon(desaturate=desaturate)

        def runTestParallel(self) -> None:  # noqa: D102
            if self.initializedDock:
                self.testControl(self.copyAction, value=True)
            # super().runTestParallel() handled by Channelmanager

        def plot(self) -> None:
            """Plot current values from all real :class:`channels<esibd.core.Channel>`."""
            self.axes[0].clear()
            channels = [channel for channel in self.parentPlugin.getChannels() if not hasattr(channel, 'real') or channel.real]
            y = [channel.value for channel in channels]
            labels = [channel.name for channel in channels]
            x = np.arange(len(y))
            self.axes[0].scatter(x, y, marker='.', color=[channel.color for channel in channels])
            self.axes[0].set_ylabel(self.parentPlugin.unit if hasattr(self.parentPlugin, 'unit') else '')
            self.axes[0].set_xticks(x, labels, rotation=30, ha='right', rotation_mode='anchor')
            if self.canvas:
                self.canvas.draw_idle()

    def __init__(self, **kwargs) -> None:  # Always use keyword arguments to allow forwarding to parent classes.
        """Initialize a ChannelManager."""
        super().__init__(**kwargs)
        self.channels = []
        self.channelsChanged = False
        self.hasRecorded = False  # only save data if new data has been recorded
        self.channelPlot = None
        self.recordingAction = None
        self.channelPlotAction = None
        self.confINI = f'{self.name}.ini'  # not a file extension, but complete filename to save and restore configurations
        self.confh5 = f'_{self.name.lower()}.h5'
        self.previewFileTypes = [self.confINI, self.confh5]
        self.changeLog = []
        self.lagging = 0
        self.interval_tolerance = 0  # how much the acquisition interval is allowed to deviate
        self._recording = False
        self.staticDisplay = self.StaticDisplay(parentPlugin=self, **kwargs) if self.useDisplays else None  # type: ignore  # noqa: PGH003 # need to initialize to access previewFileTypes
        self.liveDisplay = self.LiveDisplay(parentPlugin=self, **kwargs) if self.useDisplays else None  # type: ignore  # noqa: PGH003
        if self.useDisplays and self.liveDisplay:
            self.signalComm.plotSignal.connect(self.liveDisplay.plot)
        self.dataThread = None

    def initGUI(self) -> None:  # noqa: D102
        super().initGUI()
        self.advancedAction.toolTipFalse = f'Show advanced options and virtual channels for {self.name}.'
        self.advancedAction.toolTipTrue = f'Hide advanced options and virtual channels for {self.name}.'
        self.advancedAction.setToolTip(self.advancedAction.toolTipFalse)
        self.importAction = self.addAction(event=lambda: self.loadConfiguration(file=None), toolTip=f'Import {self.name} channels and values.',
                                           icon=self.makeCoreIcon('blue-folder-import.png'))
        self.exportAction = self.addAction(event=lambda: self.exportConfiguration(file=None), toolTip=f'Export {self.name} channels and values.',
                                            icon=self.makeCoreIcon('blue-folder-export.png'))
        self.saveAction = self.addAction(event=self.saveConfiguration, toolTip=f'Save {self.name} channels in current session.', icon=self.makeCoreIcon('database-export.png'))
        self.duplicateChannelAction = self.addAction(event=self.duplicateChannel, toolTip='Insert copy of selected channel.',
                                                      icon=self.makeCoreIcon('table-insert-row.png'))
        self.deleteChannelAction = self.addAction(event=self.deleteChannel, toolTip='Delete selected channel.', icon=self.makeCoreIcon('table-delete-row.png'))
        self.moveChannelUpAction = self.addAction(event=lambda: self.moveChannel(up=True), toolTip='Move selected channel up.', icon=self.makeCoreIcon('table-up.png'))
        self.moveChannelDownAction = self.addAction(event=lambda: self.moveChannel(up=False), toolTip='Move selected channel down.', icon=self.makeCoreIcon('table-down.png'))
        if self.useDisplays:
            self.channelPlotAction = self.addAction(event=self.showChannelPlot, toolTip=f'Plot {self.name} values.', icon=self.makeCoreIcon('chart.png'))
            self.toggleLiveDisplayAction = self.addStateAction(toolTipFalse=f'Show {self.name} live display.', iconFalse=self.makeCoreIcon('system-monitor.png'),
                                              toolTipTrue=f'Hide {self.name} live display.', iconTrue=self.makeCoreIcon('system-monitor--minus.png'),
                                              attr='showLiveDisplay', event=lambda: self.toggleLiveDisplay(visible=None), defaultState=True)
        self.defaultChannel = self.channelType(channelParent=self, tree=None)  # needs to run in main_thread as it creates QWidgets!
        self.tree = TreeWidget()
        self.addContentWidget(self.tree)
        self.loadConfiguration(useDefaultFile=True)

    def finalizeInit(self) -> None:  # noqa: D102
        if self.useOnOffLogic:
            self.onAction = self.pluginManager.DeviceManager.addStateAction(event=lambda: self.setOn(on=None), toolTipFalse=f'{self.name} on.',
                                                                             iconFalse=self.getIcon(desaturate=True), toolTipTrue=f'{self.name} off.',
                                                                               iconTrue=self.getIcon(), before=self.pluginManager.DeviceManager.aboutAction)
        super().finalizeInit()
        self.copyAction = self.addAction(event=self.copyClipboard, toolTip=f'{self.name} channel image to clipboard.', icon=self.imageClipboardIcon, before=self.aboutAction)
        self.toggleLiveDisplay()
        self.intervalChanged()  # update tolerance

    def isOn(self) -> bool:
        """Overwrite to signal if device output (e.g. for voltage supplies) is on."""
        if self.useOnOffLogic:
            return self.onAction.state
        return False

    def setOn(self, on: 'bool | None' = None) -> None:
        """Toggles device on state.

        :param on: Requested state, defaults to None. Extended versions of this function may perform tasks based on self.isOn() even if on=None.
        :type on: bool, optional
        """
        if on is not None and self.onAction.state is not on:
            self.onAction.state = on

    def runTestParallel(self) -> None:  # noqa: D102
        if self.initializedDock:
            if hasattr(self, 'channelPlotAction') and self.channelPlotAction is not None:
                self.testControl(self.channelPlotAction, value=True)  # , 1
            self.testControl(self.copyAction, value=True)  # with advanced = False
            self.testControl(self.advancedAction, value=True)
            self.testControl(self.saveAction, value=True)
            for parameter in self.channels[0].parameters:
                if parameter.name != Channel.COLOR and not parameter.indicator:  # color requires user interaction, indicators do not trigger events
                    self.testControl(parameter.getWidget(), parameter.value, .1,
                                     label=f'Testing {self.channels[0].name}.{parameter.name} {parameter.toolTip or "No toolTip."}')
            self.testControl(self.channels[0].getParameterByName(Channel.SELECT).getWidget(), value=True, delay=.1)
            self.testControl(self.moveChannelDownAction, value=True, delay=1)
            self.testControl(self.moveChannelUpAction, value=True, delay=1)
            self.testControl(self.duplicateChannelAction, value=True, delay=1)
            self.testControl(self.deleteChannelAction, value=True, delay=1)
            self.testControl(self.advancedAction, value=False)
            if self.useOnOffLogic:
                self.testControl(self.onAction, value=True)
            if self.useDisplays and self.initializedDock and self.staticDisplayActive() and self.staticDisplay:
                self.staticDisplay.raiseDock(showPlugin=True)
                self.staticDisplay.runTestParallel()
            if self.channelPlotActive() and self.channelPlot:
                self.channelPlot.raiseDock(showPlugin=True)
                self.channelPlot.runTestParallel()
                # init, start, pause, stop acquisition will be tested by DeviceManager
            if self.useOnOffLogic:
                self.testControl(self.onAction, value=False)  # leave in save state after testing
        super().runTestParallel()

    def bufferLagging(self, wait: 'int | None' = None) -> bool:  # noqa: D102
        # treat wait time as lag free time and subtract corresponding time from self.lagging
        if wait is None:
            wait = self.pluginManager.Settings.lagWaitTime
        if super().bufferLagging(wait) and not self.pluginManager.closing:
            self.lagging = int(max(0, self.lagging - wait / self.interval * 1000))
            return True
        return False

    def copyClipboard(self) -> None:  # noqa: D102
        self.imageToClipboard(self.tree.grabItems())

    INTERVAL = 'Interval'
    interval: int

    def getDefaultSettings(self) -> dict[str, dict]:  # noqa: D102

        # definitions for type hinting
        self.interval: int

        ds = {}
        ds[f'{self.name}/{self.INTERVAL}'] = parameterDict(value=2000, minimum=100, maximum=10000, toolTip=f'Interval for {self.name} in ms.',
                                                                parameterType=PARAMETERTYPE.INT, event=self.intervalChanged, attr='interval', instantUpdate=False)
        return ds

    def customConfigFile(self, file: str) -> Path:
        """Return custom config file including full path.

        :param file: File name.
        :type file: str
        :return: File including full path.
        :rtype: pathlib.Path
        """
        return self.pluginManager.Settings.configPath / file

    def getChannelByName(self, name: str) -> Channel | None:
        """Return a device specific channel based on its unique name.

        :param name: Name of the channel to be found.
        :type name: str
        :return: Found channel, defaults to None if no channel found.
        :rtype: esibd.core.Channel
        """
        return next((channel for channel in self.channels if channel.name.strip().lower() == name.strip().lower()), None)

    def getChannels(self) -> list[Channel]:
        """Get channels. Overwrite to return subsets based on channel state."""
        # allows to replace list of internal channels with corresponding source channels if applicable.
        return self.channels

    def minTime(self) -> np.float64:
        """Get the oldest time in the stored history."""
        if isinstance(self, Device):
            return self.time.get()[0]
        return np.min([channel.time.get()[0] for channel in self.getChannels() if channel.time and channel.time.size > 0])

    def getActiveChannels(self) -> list[Channel]:
        """Return all channels that are enabled or not real."""
        return [channel for channel in self.getChannels() if (channel.enabled or not channel.real)]

    def getActiveChannelsAndParameters(self) -> list[Channel | Parameter]:
        """Return all channels that are enabled or not real."""
        channels = self.getActiveChannels()
        parameters: list[Parameter] = []
        for channel in channels:
            parameters.extend(parameter for parameter in channel.getRecordedParameters())
        return channels + parameters

    def getDataChannels(self) -> list[Channel]:
        """Return all channels that have data history."""
        return [channel for channel in self.channels if channel.getValues().shape[0] != 0]

    def addChannel(self, item: dict[str, Any], index: 'int | None' = None) -> Channel:
        """Map dictionary to :class:`~esibd.core.Channel`.

        :param item: Dictionary containing channel details.
        :type item: dict
        :param index: Index where channel should be inserted, defaults to None
        :type index: int, optional
        :return: The newly created channel
        :rtype: esibd.core.Channel
        """
        channel = self.channelType(channelParent=self, tree=self.tree)
        if index is None:
            self.channels.append(channel)
            self.tree.addTopLevelItem(channel)  # has to be added before populating
        else:
            self.channels.insert(index, channel)
            self.tree.insertTopLevelItem(index, channel)  # has to be added before populating
        channel.initGUI(item)
        return channel

    def modifyChannel(self) -> Channel | None:
        """Return selected channel. Note, channels can only be selected in advanced mode.

        :return: The selected channel. None if no channel found.
        :rtype: esibd.core.Channel
        """
        selectedChannel = next((channel for channel in self.channels if channel.select), None)
        if not selectedChannel:
            self.print('No channel selected.')
            return None
        return selectedChannel

    @synchronized()
    def duplicateChannel(self) -> Channel | None:
        """Duplicates the currently selected channel."""
        selectedChannel = self.modifyChannel()
        if selectedChannel:
            self.print(f'duplicateChannel {selectedChannel.name}', flag=PRINT.DEBUG)
            index = self.channels.index(selectedChannel)
            newChannelDict = selectedChannel.asDict()
            newChannelDict[selectedChannel.NAME] = f'{selectedChannel.name}_copy'
            self.loading = True
            self.addChannel(item=newChannelDict, index=index + 1)
            self.loading = False
            newChannel = self.getChannelByName(cast('str', newChannelDict[selectedChannel.NAME]))
            if newChannel:
                self.channelSelection(selectedChannel=newChannel)  # trigger deselecting original channel
            self.tree.scheduleDelayedItemsLayout()
            if isinstance(self, Device) and self.controller is not None:
                self.controller.initializeValues(reset=True)
            return newChannel
        return None

    @synchronized()
    def deleteChannel(self) -> None:
        """Delete the currently selected channel."""
        selectedChannel = self.modifyChannel()
        if selectedChannel:
            self.print(f'deleteChannel {selectedChannel.name}', flag=PRINT.DEBUG)
            if len(self.channels) == 1:
                self.print('Need to keep at least one channel.')
                return
            selectedChannel.onDelete()
            index = self.channels.index(selectedChannel)
            self.channels.pop(index)
            self.tree.takeTopLevelItem(index)
            self.channels[min(index, len(self.channels) - 1)].select = True
            self.pluginManager.reconnectSource(selectedChannel.name)
            if isinstance(self, Device) and self.controller is not None:
                self.controller.initializeValues(reset=True)

    @synchronized()
    def moveChannel(self, up: bool) -> Channel | None:  # noqa: C901
        """Move the channel up or down in the list of channels.

        :param up: Move up if True, else down.
        :type up: bool
        """
        selectedChannel = self.modifyChannel()
        if selectedChannel:
            self.print(f'moveChannel {selectedChannel.name} {"up" if up else "down"}', flag=PRINT.DEBUG)
            index = self.channels.index(selectedChannel)
            if (index == 0 and up) or (index == len(self.channels) - 1 and not up):
                self.print(f"Cannot move channel further {'up' if up else 'down'}.")
                return None
            self.loading = True
            selectedChannel.onDelete()
            self.channels.pop(index)
            self.tree.takeTopLevelItem(index)
            oldValues = None
            oldValue = None
            oldBackgrounds = None
            oldBackground = None
            oldParameterValues = None
            if isinstance(self, Device):
                oldValues = selectedChannel.values.get()
                oldValue = selectedChannel.value if hasattr(selectedChannel, Parameter.VALUE.lower()) else None
                if selectedChannel.useBackgrounds:
                    oldBackgrounds = selectedChannel.backgrounds
                    oldBackground = selectedChannel.background
                oldParameterValues = [parameter.values for parameter in selectedChannel.getRecordedParameters()]
            if up:
                self.addChannel(item=selectedChannel.asDict(), index=index - 1)
            else:
                self.addChannel(item=selectedChannel.asDict(), index=index + 1)
            newChannel = self.getChannelByName(selectedChannel.name)
            if isinstance(self, Device) and oldValues is not None and len(oldValues) > 0 and newChannel:
                newChannel.values = DynamicNp(initialData=oldValues, max_size=self.maxDataPoints)
                if oldValue is not None:
                    newChannel.value = oldValue
                if newChannel.useBackgrounds and oldBackground is not None and oldBackgrounds is not None:
                    newChannel.backgrounds = oldBackgrounds
                    newChannel.background = oldBackground
                if oldParameterValues is not None:
                    for parameter, values in zip(newChannel.getRecordedParameters(), oldParameterValues, strict=True):
                        parameter.values = values
            self.loading = False
            self.tree.scheduleDelayedItemsLayout()
            return newChannel
        return None

    def plot(self, apply: bool = False) -> None:
        """Plot for all active LiveDisplays.

        :param apply: Apply most recent data, otherwise update rate depends on data thinning.
        :type apply: bool
        """
        if self.liveDisplayActive() and self.liveDisplay:
            self.liveDisplay.plot(apply=apply)

    def clearPlot(self) -> None:
        """Clear the plot in the liveDisplay."""
        if self.liveDisplayActive() and self.liveDisplay and not self.pluginManager.closing:
            self.liveDisplay.clearPlot()

    def convertDataDisplay(self, data: np.ndarray[Any, np.dtype[np.float32]]) -> np.ndarray[Any, np.dtype[np.float32]]:
        """Overwrite to apply scaling and offsets to data before it is displayed. Use, e.g., to convert to another unit.

        This should only affect display in :class:`LiveDisplays<esibd.plugins.LiveDisplay>`.

        :param data: Original data.
        :type data: np.ndarray
        :return: Scaled data.
        :rtype: np.ndarray
        """
        return data

    @synchronized()
    def saveConfiguration(self) -> None:
        """Save configuration to file in current session."""
        self.pluginManager.Settings.incrementMeasurementNumber()
        file = self.pluginManager.Settings.getMeasurementFileName(self.confh5)
        self.exportConfiguration(file)
        self.print(f'Saved {file.name}.')

    CHANNEL = 'Channel'

    def exportConfiguration(self, file: 'Path | None' = None, useDefaultFile: bool = False) -> None:  # noqa: C901, PLR0912
        """Save an .ini or .h5 file which contains the configuration for this :class:`~esibd.plugins.Device`.

        Channels can be added and edited through the user interface, but the .ini file can also be edited manually with a text editor.

        :param file: The file to add the configuration to, defaults to None
        :type file: pathlib.Path, optional
        :param useDefaultFile: Indicates if the default should be used, defaults to False
        :type useDefaultFile: bool, optional
        """
        if len(self.channels) == 0:
            self.print('No channels found to export.', flag=PRINT.ERROR)
            return
        if useDefaultFile:
            file = self.customConfigFile(self.confINI)
        if not file:  # get file via dialog
            file = Path(QFileDialog.getSaveFileName(parent=None, caption=SELECTFILE, filter=self.FILTER_INI_H5)[0])
        if file != Path():
            if file.suffix == FILE_INI:
                confParser = configparser.ConfigParser()
                confParser[INFO] = infoDict(self.name)
                for i, channel in enumerate(self.channels):
                    confParser.read_dict({f'{self.CHANNEL}_{i:03d}': channel.asDict(includeTempParameters=True, formatValue=True, escapePercent=True)})
                with file.open('w', encoding=self.UTF8) as configFile:
                    confParser.write(configFile)
            else:  # h5
                with h5py.File(file, 'a', track_order=True) as h5file:
                    self.hdfUpdateVersion(h5file)
                    group = self.requireGroup(h5file, self.name)
                    for parameter in self.defaultChannel.asDict(includeTempParameters=True):
                        if parameter in group:
                            self.print(f'Ignoring duplicate parameter {parameter}.', flag=PRINT.WARNING)
                            continue
                        parameterType = self.defaultChannel.getParameterByName(parameter).parameterType
                        # Using default channel data type. If the plugin uses multiple channel specific data types it has to make sure
                        # that saving and restoring works for all of them using the data type of the default channel.
                        data = [channel.getParameterByName(parameter).value for channel in self.channels]
                        dtype = None
                        if parameterType == PARAMETERTYPE.INT:
                            dtype = np.int32
                        elif parameterType == PARAMETERTYPE.FLOAT:
                            dtype = np.float32
                        elif parameterType == PARAMETERTYPE.BOOL:
                            dtype = np.bool_  # used to be bool8
                        elif parameterType == PARAMETERTYPE.COLOR:
                            data = [channel.getParameterByName(parameter).value for channel in self.channels]
                            dtype = 'S7'
                        else:  # parameterType in [PARAMETERTYPE.EXP, PARAMETERTYPE.COMBO, PARAMETERTYPE.INTCOMBO, PARAMETERTYPE.TEXT, PARAMETERTYPE.LABEL]:
                            # NOTE data = [cast('str', value).encode(UTF8) for value in data]  # keep using ascii for backwards compatibility
                            data = [str(value).replace('°', '') for value in data]  # remove characters that are incompatible with ascii
                            dtype = f'S{len(max([str(string) for string in data], key=len))}'  # use length of longest string as fixed length is required
                        group.create_dataset(name=parameter, data=np.asarray(data, dtype=dtype))  # do not save as attributes. very very memory intensive!
        if not self.pluginManager.loading:
            self.pluginManager.Explorer.populateTree()

    @synchronized()
    def toggleAdvanced(self, advanced: 'bool | None' = False) -> None:  # noqa: C901, D102
        if advanced is not None:
            self.advancedAction.state = advanced
        self.importAction.setVisible(self.advancedAction.state)
        self.exportAction.setVisible(self.advancedAction.state)
        self.duplicateChannelAction.setVisible(self.advancedAction.state)
        self.deleteChannelAction.setVisible(self.advancedAction.state)
        self.moveChannelUpAction.setVisible(self.advancedAction.state)
        self.moveChannelDownAction.setVisible(self.advancedAction.state)
        for i, item in enumerate(self.defaultChannel.getSortedDefaultChannel().values()):
            if item[Parameter.ADVANCED]:
                self.tree.setColumnHidden(i, not self.advancedAction.state)
        for channel in self.channels:
            if self.inout == INOUT.NONE:
                channel.setHidden(False)  # Always visible
            elif channel.inout == INOUT.IN:
                channel.setHidden(not (self.advancedAction.state or channel.active or channel.display))
            else:  # INOUT.OUT:
                channel.setHidden(not (self.advancedAction.state or channel.active or channel.display))
        # Collapses all channels of same color below selected channels.
        for channel in self.channels:
            index = self.channels.index(channel)
            while True:
                if index == 0:
                    break
                c_above = self.channels[index - 1]
                if c_above.color != channel.color:
                    break
                if c_above.collapse or (c_above.isHidden() and c_above.active):
                    channel.setHidden(True)
                    break
                index -= 1

    def intervalChanged(self) -> None:
        """Extend to add code to be executed in case the :ref:`acquisition_interval` changes."""
        self.lagging = 0  # reset as lag time is calculated based on interval.
        self.interval_tolerance = max(100, self.interval / 5)  # larger margin for error if interval is large.

    def loadConfiguration(self, file: 'Path | None' = None, useDefaultFile: bool = False, append: bool = False) -> None:  # noqa: C901, PLR0912, PLR0915
        """Load :class:`channel<esibd.core.Channel>` configuration from file.

        If only values should be loaded without complete reinitialization, use :attr:`loadValues<esibd.plugins.Device.loadValues>` instead.

        :param file: File from which to load configuration, defaults to None
        :type file: pathlib.Path, optional
        :param useDefaultFile: Use internal configuration file if True, defaults to False
        :type useDefaultFile: bool, optional
        :param append: True if changelog should be appended to previous change logs, e.g. when loading from multiple devices at once. Defaults to False
        :type append: bool, optional
        """
        if useDefaultFile:
            file = self.customConfigFile(self.confINI)
        if not file:  # get file via dialog
            if isinstance(self, Device) and self.initialized:
                self.print('Stop communication to load channels.', flag=PRINT.WARNING)
                return
            file = Path(QFileDialog.getOpenFileName(parent=None, caption=SELECTFILE, filter=self.FILTER_INI_H5,
                                                    directory=self.pluginManager.Settings.getFullSessionPath().as_posix())[0])
        if file != Path():  # noqa: PLR1702
            self.loading = True
            self.tree.setUpdatesEnabled(False)
            self.tree.setRootIsDecorated(False)  # no need to show expander
            if file.suffix == FILE_INI:
                if file.exists():  # create default if not exist
                    confParser = configparser.ConfigParser()
                    confParser.read(file)
                    if len(confParser.items()) < 3:  # minimum: DEFAULT, Info, and one Channel  # noqa: PLR2004
                        self.print(f'File {file} does not contain valid channels. Repair the file manually or delete it, '
                                                    ' to trigger generation of a valid default channel on next start.', flag=PRINT.WARNING)
                        self.tree.setHeaderLabels(['No valid channels found. Repair or delete config file.'])
                        self.tree.setUpdatesEnabled(True)
                        self.loading = False
                        return
                    # NOTE: while using dict(item) in next line works for typechecking, this looses case insensitivity of SectionProxy
                    self.updateChannelConfig([item for name, item in confParser.items() if name not in {Parameter.DEFAULT.upper(), VERSION, INFO}],  # type: ignore  # noqa: PGH003
                                              file, append=append)
                else:  # Generate default settings file if file was not found.
                    # To update files with new parameters, simply delete the old file and the new one will be generated.
                    if not self.channels:
                        self.print(f'Generating default config file {file}')
                        for i in range(9):
                            self.addChannel(item={Parameter.NAME: f'{self.name}{i + 1}'})
                    else:
                        self.print(f'Generating config file {file}')
                    self.exportConfiguration(file, useDefaultFile=True)
            else:  # file.suffix == FILE_H5:
                with h5py.File(name=file, mode='r', track_order=True) as h5file:
                    if self.name in h5file:
                        group = cast('h5py.Group', h5file[self.name])
                        names = cast('h5py.Dataset', group[Parameter.NAME])
                        items = [{} for _ in range(len(names))]
                        for i, name in enumerate(datasetToStrList(cast('h5py.Dataset', names))):
                            items[i][Parameter.NAME] = name
                        for name, parameter in self.defaultChannel.getSortedDefaultChannel().items():
                            values = None
                            if parameter[Parameter.PARAMETER_TYPE] in {PARAMETERTYPE.INT, PARAMETERTYPE.FLOAT}:
                                values = cast('h5py.Dataset', group[name])
                            elif parameter[Parameter.PARAMETER_TYPE] == PARAMETERTYPE.BOOL:
                                values = [str(bool_str) for bool_str in cast('h5py.Dataset', group[name])]
                            else:
                                values = datasetToStrList(cast('h5py.Dataset', group[name]))
                            for i, value in enumerate(values):
                                items[i][name] = value
                        self.updateChannelConfig(items, file, append=append)
                    else:
                        self.print(f'Could not find group with name {self.name} in {file}. Check if this is the correct file.', flag=PRINT.ERROR)

            self.tree.setHeaderLabels([parameterDict.get(Parameter.HEADER, '') or name.title()
                                        for name, parameterDict in self.defaultChannel.getSortedDefaultChannel().items()])
            header = self.tree.header()
            if header:
                header.setStretchLastSection(False)
                header.setMinimumSectionSize(0)
                header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            for channel in self.getChannels():
                channel.collapseChanged(toggle=False)
            self.toggleAdvanced(advanced=self.advancedAction.state)  # keep state after importing new configuration
            self.tree.setUpdatesEnabled(True)
            self.tree.scheduleDelayedItemsLayout()
            self.loading = False
            self.pluginManager.DeviceManager.globalUpdate(inout=self.inout)
            # if there was a history, it has been invalidated by reinitializing all channels.
            if not self.pluginManager.loading:
                for channel in self.getChannels():
                    channel.clearPlotCurve()

    @property
    def LOADVALUES(self) -> str:
        """Load values string for context menus."""
        return f'Load {self.name} values.'

    def loadValues(self, file: 'Path | None' = None, append: bool = False) -> None:
        """Load values only, instead of entire configuration, for :class:`channels<esibd.core.Channel>` matching in file and current configuration.

        Channels that exist in the file but not in the current configuration will be ignored.
        Only used by input devices.

        :param file: The file to load values from, defaults to None
        :type file: pathlib.Path, optional
        :param append: True if changelog should be appended to previous change logs, e.g. when loading from multiple devices at once. Defaults to False
        :type append: bool, optional
        """
        if not file:  # get file via dialog
            file = Path(QFileDialog.getOpenFileName(parent=None, caption=SELECTFILE, filter=self.FILTER_INI_H5)[0])
        if file != Path():
            self.changeLog = [f'Change log for loading values for {self.name} from {file.name}:']
            if file.suffix == FILE_INI:
                confParser = configparser.ConfigParser()
                confParser.read(file)
                for name, item in confParser.items():
                    if name not in {Parameter.DEFAULT.upper(), VERSION, INFO}:
                        self.updateChannelValue(cast('str', item.get(Parameter.NAME)), float(item.get(Parameter.VALUE, '0')))
            else:  # FILE_H5
                with h5py.File(name=file, mode='r', track_order=True) as h5file:
                    group = cast('h5py.Group', h5file[self.name])
                    for name, value in zip(datasetToStrList(cast('h5py.Dataset', group[Parameter.NAME])), cast('h5py.Dataset', group[Parameter.VALUE]), strict=True):
                        self.updateChannelValue(name, value)
            if len(self.changeLog) == 1:
                self.changeLog.append('No changes.')
            self.pluginManager.Text.setText('\n'.join(self.changeLog) + '\n', showPlugin=False, append=append)
            self.print('Values updated. Change log available in Text plugin.')

    def updateChannelValue(self, name: str, value: float) -> None:
        """Update channel value und adds message to change log if the value has changed.

        :param name: Channel name.
        :type name: str
        :param value: The new value.
        :type value: float
        """
        channel = self.getChannelByName(name)
        if channel:
            parameter = channel.getParameterByName(Parameter.VALUE)
            initialVal = channel.value
            channel.value = value
            if initialVal != channel.value and self.changeLog:  # c.value might be different from value due to coerced range
                self.changeLog.append(f'Value of channel {name} changed from {parameter.formatValue(initialVal)} to {parameter.formatValue(channel.value)} {self.unit}.')
        else:
            self.print(f'Could not find channel {name}.', flag=PRINT.WARNING)

    def updateChannelConfig(self, items: list[dict[str, ParameterType]], file: Path, append: bool = False) -> None:
        """Scan for changes when loading configuration and displays change log before overwriting old channel configuration.

        :param items: :class:`~esibd.core.Channel` items from file
        :type items: list[dict[str, ParameterType]]
        :param file: config file
        :type file: pathlib.Path
        :param append: True if changelog should be appended to previous change logs, e.g. when loading from multiple devices at once. Defaults to False
        :type append: bool, optional
        """
        # Note: h5diff can be used alternatively to find changes, but the output is not formatted in a user friendly way (hard to correlate values with channels).
        if not self.pluginManager.loading:
            self.changeLog = [f'Change log for loading channels for {self.name} from {file.name}:']
            self.changeLog += self.compareItemsConfig(items)[0]
            self.pluginManager.Text.setText('\n'.join(self.changeLog) + '\n', showPlugin=False, append=append)  # show changelog
            self.print(f'Configuration updated from {file.name}. Change log available in Text plugin.')
        # clear and load new channels
        for channel in self.channels:
            channel.onDelete()
        self.channels = []
        self.tree.clear()
        for item in items:
            self.addChannel(item=item)
            if np.mod(len(self.channels), 5) == 0:
                self.processEvents()
        if not self.pluginManager.loading:
            self.pluginManager.connectAllSources()  # previous channels have become invalid

    def compareItemsConfig(self, items: list[dict[str, ParameterType]], ignoreIndicators: bool = False) -> tuple[list[str], bool]:
        """Compare channel items from file with current configuration.

        This allows to track changes and decide if files need to be updated.

        :param items: :class:`~esibd.core.Channel` items from file
        :type items: list[dict[str, ParameterType]]
        :param ignoreIndicators: Set to True if deciding about file updates (indicators are not saved in files).
        :type ignoreIndicators: bool
        """
        changeLog = []
        changed = True
        for item in items:
            channel = self.getChannelByName(cast('str', item[Parameter.NAME]))
            if channel:
                for name in self.defaultChannel.getSortedDefaultChannel():
                    if name in channel.tempParameters():
                        continue
                    parameter = channel.getParameterByName(name)
                    if not parameter.restore:
                        continue
                    if name in item and not parameter.equals(item[name]):
                        if parameter.indicator and ignoreIndicators:
                            continue
                        changeLog.append(f'Updating parameter {name} on channel {channel.name} from {parameter.formatValue()} to {parameter.formatValue(item[name])}')
            else:
                changeLog.append(f'Adding channel {item[Parameter.NAME]}')
        newNames = [item[Parameter.NAME] for item in items]
        changeLog.extend(f'Removing channel {channel.name}' for channel in self.getChannels() if channel.name not in newNames)
        if len(changeLog) == 0:
            changeLog.append('No changes.')
            changed = False
        return changeLog, changed

    def channelConfigChanged(self, file: 'Path | None' = None, useDefaultFile: bool = True) -> bool:
        """Scan for changes when saving configuration.

        :param file: Compare configuration to stat in this file, defaults to None
        :type file: pathlib.Path, optional
        :param useDefaultFile: Indicates if default file should be used, defaults to True
        :type useDefaultFile: bool, optional
        :return: True if configuration has changed and does not match file.
        :rtype: bool
        """
        changed = False
        if useDefaultFile:
            file = self.customConfigFile(self.confINI)
        if file and file.exists():
            confParser = configparser.ConfigParser()
            confParser.read(file)
            if len(confParser.items()) > 2:  # minimum: DEFAULT, Info, and one Channel  # noqa: PLR2004
                items = [i for name, i in confParser.items() if name not in {Parameter.DEFAULT.upper(), VERSION, INFO}]
                changed = self.compareItemsConfig(items, ignoreIndicators=True)[1]  # type: ignore # pylint: disable = unused-variable  # noqa: PGH003
        return changed

    def channelSelection(self, selectedChannel: Channel) -> None:
        """Ensure that only one channel is selected at all times.

        :param selectedChannel: The Channel that has been selected by the user.
        :type selectedChannel: esibd.core.Channel
        """
        if selectedChannel.select:
            for channel in self.channels:
                if channel is not selectedChannel:
                    channel.select = False

    def channelPlotActive(self) -> bool:
        """Indicate if the channel plot is active."""
        return self.channelPlot is not None and self.channelPlot.initializedDock

    def toggleChannelPlot(self, visible: bool) -> None:
        """Toggles visibility of the channelPlot.

        :param visible: Requested visibility for channelPlot.
        :type visible: bool
        """
        if visible:
            if not self.channelPlot or not self.channelPlot.initializedDock:
                self.channelPlot = self.ChannelPlot(parentPlugin=self, pluginManager=self.pluginManager, dependencyPath=self.dependencyPath, sourceCodePath=self.sourceCodePath)
                self.channelPlot.provideDock()
        elif self.channelPlot is not None and self.channelPlot.initializedDock:
            self.channelPlot.closeGUI()

    @synchronized()
    def showChannelPlot(self) -> None:
        """Show a plot based on the current values of all real channels."""
        self.toggleChannelPlot(visible=True)
        if self.channelPlot:
            self.channelPlot.raiseDock(showPlugin=True)
            self.channelPlot.plot()

    def startRecording(self) -> None:
        """Start the data recording thread."""
        if self.dataThread is not None and self.dataThread.is_alive():
            self.print('Wait for data recording thread to complete before restarting acquisition.', flag=PRINT.DEBUG)
            self.recording = False
            self.dataThread.join(timeout=5)  # may freeze GUI temporarily but need to be sure old thread is stopped before starting new one
            if self.dataThread.is_alive():
                self.print('Data recording thread did not complete. Reset connection manually.', flag=PRINT.ERROR)
                return
        if self.liveDisplayActive():
            self.liveDisplay.autoScaleAction.state = False
        self.clearPlot()  # update legend in case channels have changed
        self.intervalChanged()  # update tolerance
        self.recording = True
        self.lagging = 0
        self.dataThread = Thread(target=self.runDataThread, args=(lambda: self.recording,), name=f'{self.name} dataThread')
        self.dataThread.daemon = True  # Terminate with main app independent of stop condition
        self.dataThread.start()
        self.hasRecorded = True

    def toggleRecording(self, on: 'bool | None' = None, manual: bool = True) -> None:  # noqa: ARG002
        """Turn recording on or off or toggles state.

        Data will be plotted in :class:`~esibd.plugins.LiveDisplay` if that is active.
        Extend to add recoding logic for devices.

        :param on: If True recording will be turned on, if False it will be turned off. If already on or off nothing happens. If None, recording is toggled.
        :type on: bool, optional
        :param manual: If True, signal was send directly from corresponding live display. Otherwise might be send from device manager
        :type manual: bool, optional
        """
        if (on is not None and not on) or (on is None and self.recording):
            # toggle off
            if self.recording:
                self.recording = False
        elif not self.recording:
            # (on is not None and on) (on is None and not self.recording):
            # toggle on if not already running
            if self.liveDisplayActive():
                self.clearPlot()
            self.plotting = False  # reset in case it got stuck
            self.startRecording()

    def runDataThread(self, recording: Callable) -> None:
        """Trigger regular plotting of data.

        Extend or Overwrite to add logic for appending data to channels.

        :param recording: Queries recording state.
        :type recording: Callable
        """
        while recording():
            self.signalComm.plotSignal.emit(False)  # noqa: FBT003
            time.sleep(self.interval / 1000)  # in seconds  # wait at end to avoid emitting signal after recording set to False
            self.bufferLagging()

    @property
    def recording(self) -> bool:
        """Indicates the recording state."""
        return self._recording

    @recording.setter
    def recording(self, recording: bool) -> None:
        self._recording = recording
        # allow output widgets to react to change if acquisition state
        if hasattr(self, 'recordingAction') and self.recordingAction is not None:
            self.recordingAction.state = self.recording
            if self.liveDisplayActive() and self.liveDisplay:
                self.liveDisplay.recordingAction.state = self.recording
            if not recording:
                # reset to 0 while not updated
                self.lagging = 0
                self.lagging_seconds = 0

    def supportsFile(self, file: Path) -> bool:  # noqa: D102
        return any(file.name.endswith(suffix) for suffix in (self.getSupportedFiles()))  # does not support any files for preview, only when explicitly loading

    def closeCommunication(self) -> None:
        """Stop recording and also closes all device communication.

        Extend to add custom code to close device communication.
        """
        if self.useOnOffLogic and self.onAction.state:
            self.setOn(False)
        self.recording = False

    def initializeCommunication(self) -> None:
        """Extend to initialize communication.

        Can be used to initialize GUI.
        Redirect initialization of hardware communication to the corresponding :class:`~esibd.core.DeviceController`.
        """
        self.clearPlot()
        self.plotting = False

    def close(self) -> bool:  # noqa: D102
        if self.channelConfigChanged(useDefaultFile=True) or self.channelsChanged:
            self.exportConfiguration(useDefaultFile=True)
        return super().close()

    def closeGUI(self) -> None:  # noqa: D102
        self.toggleChannelPlot(visible=False)
        self.toggleLiveDisplay(visible=False)
        self.toggleStaticDisplay(visible=False)
        super().closeGUI()

    def toggleTitleBar(self) -> None:  # noqa: D102
        super().toggleTitleBar()
        if self.liveDisplayActive() and self.liveDisplay:
            self.liveDisplay.toggleTitleBar()
        if self.staticDisplayActive() and self.staticDisplay:
            self.staticDisplay.toggleTitleBar()

    @synchronized()
    def toggleLiveDisplay(self, visible: 'bool | None' = None) -> None:
        """Toggle visibility of the liveDisplay.

        :param visible: Requested visibility for liveDisplay.
        :type visible: bool
        """
        if not self.liveDisplay:
            return  # liveDisplay not supported
        if (visible if visible is not None else self.toggleLiveDisplayAction.state):
            if not self.liveDisplayActive():  # only if not already visible
                self.liveDisplay.provideDock()
            self.liveDisplay.raiseDock(showPlugin=True)
        elif self.liveDisplayActive():
            self.liveDisplay.closeGUI()

    def liveDisplayActive(self) -> bool:
        """Indicate if the liveDisplay is active."""
        return self.liveDisplay is not None and self.liveDisplay.initializedDock

    def toggleStaticDisplay(self, visible: bool) -> None:
        """Toggles visibility of the staticDisplay.

        :param visible: Requested visibility for staticDisplay.
        :type visible: bool
        """
        if not self.staticDisplay:
            return  # staticDisplay not supported
        if visible:
            if not self.staticDisplayActive():  # only if not already visible
                self.staticDisplay.provideDock()
            self.staticDisplay.raiseDock(showPlugin=True)
        elif self.staticDisplayActive():
            self.staticDisplay.closeGUI()

    def staticDisplayActive(self) -> bool:
        """Indicate if the staticDisplay is active."""
        return self.staticDisplay is not None and self.staticDisplay.initializedDock

    def updateTheme(self) -> None:  # noqa: D102
        super().updateTheme()
        self.loading = True
        for channel in self.getChannels():
            channel.updateColor()
        self.loading = False
        if self.staticDisplayActive() and self.staticDisplay:
            self.staticDisplay.updateTheme()
        if self.liveDisplayActive() and self.liveDisplay:
            self.liveDisplay.updateTheme()


class Device(ChannelManager):  # noqa: PLR0904
    """Handle communication with one or more physical devices, provide controls to configure the device and display live or previously recorded data.

    There are *input devices* (sending input from
    the user to hardware) and *output devices* (reading outputs from
    hardware). Note that some *input devices* may also read back data from
    hardware to confirm that the user defined values are applied correctly.

    The main interface consists of a list of :ref:`sec:channels`. By
    default only the physically relevant information is shown. By entering
    the *advanced mode*, additional channel parameters can be configured. The
    configuration can be exported and imported, though once all channels
    have been setup it is sufficient to only load values which can be done
    using a file dialog or from the context menu of an appropriate file in
    the :ref:`sec:explorer`. After loading the configurations or values, a change log will be
    available in the :ref:`sec:text` plugin to quickly identify what has changed. Each
    device also comes with a :ref:`display<sec:displays>` and a :ref:`live display<sec:live_displays>`.
    The current values can also be plotted to get a quick overview and identify any
    unusual values.
    """

    version = 1.0
    optional = True
    name = 'Device'  # overwrite after inheriting
    pluginType = PLUGINTYPE.INPUTDEVICE
    """ :class:`Devices<esibd.plugins.Device>` are categorized as input or output devices.
    Overwrite with :attr:`~esibd.const.PLUGINTYPE.OUTPUTDEVICE` after inheriting if applicable."""

    MAXSTORAGE = 'Max storage'
    MAXDATAPOINTS = 'Max data points'
    ERRORCOUNT = 'Error count'
    MAXERRORCOUNT = 'Max error count'
    LOGGING = 'Logging'
    unit: str = 'unit'
    """Unit used in user interface."""
    inout: INOUT
    """Flag specifying if this is an input or output device."""
    useBackgrounds = False
    logY = False
    signalComm: 'SignalCommunicate'
    controller: DeviceController

    class SignalCommunicate(ChannelManager.SignalCommunicate):
        """Bundle pyqtSignals."""

        appendDataSignal: pyqtSignal = pyqtSignal(bool, bool)
        """Signal that triggers appending of data from channels to history."""

    def __init__(self, **kwargs) -> None:  # Always use keyword arguments to allow forwarding to parent classes.
        """Initialize a Device."""
        super().__init__(**kwargs)
        if self.pluginType == PLUGINTYPE.INPUTDEVICE:
            self.inout = INOUT.IN
        else:
            self.inout = INOUT.OUT
        self.file = Path()
        self.documentation = ''  # use __doc__ defined in child classes, sphinx does not initialize and will use the value of documentation defined above
        self.updating = False  # Suppress events while channel equations are evaluated
        self.time = DynamicNp(dtype=np.float64)
        self.lastIntervalTime = time.time() * 1000
        self.signalComm.appendDataSignal.connect(self.appendData)
        self.controller = None  # type: ignore  # noqa: PGH003 # avoid frequent checking for not None
        # implement a controller based on DeviceController. In some cases there is no controller for the device, but for every channel. Adjust
        self.subtractBackgroundAction = None

    def initGUI(self) -> None:  # noqa: D102
        super().initGUI()
        self.closeCommunicationAction = self.addAction(event=self.closeCommunication, toolTip=f'Close {self.name} communication.', icon=self.makeCoreIcon('stop.png'))
        self.initAction = self.addAction(event=self.initializeCommunication, toolTip=f'Initialize {self.name} communication.', icon=self.makeCoreIcon('rocket-fly.png'))
        self.recordingAction = self.addStateAction(event=lambda: self.toggleRecording(manual=True),
                                                   toolTipFalse=f'Start {self.name} data acquisition.', iconFalse=self.makeCoreIcon('play.png'),
                                                   toolTipTrue=f'Pause {self.name} data acquisition.', iconTrue=self.makeCoreIcon('pause.png'))
        if self.useBackgrounds:
            self.subtractBackgroundAction = self.addStateAction(toolTipFalse=f'Subtract background for {self.name}.', iconFalse=self.makeCoreIcon('eraser.png'),
                                                        toolTipTrue=f'Ignore background for {self.name}.', iconTrue=self.makeCoreIcon('eraser.png'),
                                                        attr='subtractBackground', event=self.subtractBackgroundChanged)
            self.addAction(event=self.setBackground, toolTip=f'Set current value as background for {self.name} based on last 5 s.',
                            icon=self.makeCoreIcon('eraser--pencil.png'))
        self.estimateStorage()
        if self.inout == INOUT.IN:
            self.addAction(event=lambda: self.loadValues(None), toolTip=f'Load {self.name} values only.', before=self.saveAction, icon=self.makeCoreIcon('table-import.png'))

    def finalizeInit(self) -> None:  # noqa: D102
        super().finalizeInit()
        self.errorCountChanged()
        self.updateErrorCountToolTip()

    def getDefaultSettings(self) -> dict[str, dict]:  # noqa: D102

        # definitions for type hinting
        self.interval_measured: int
        self.lagging_seconds: int
        self.maxStorage: int
        self.maxDataPoints: int
        self.attr: bool
        self.log: bool
        self.maxErrorCount: int
        self.errorCountStr: str

        defaultSettings = super().getDefaultSettings()
        defaultSettings[f'{self.name}/{self.INTERVAL} (measured)'] = parameterDict(value=0, internal=True, restore=False,
        toolTip=f'Measured plot interval for {self.name} in ms.\n'
                'If this deviates multiple times in a row, the number of display points will be reduced and eventually acquisition\n'
                'will be stopped to ensure the application remains responsive.\n'
                f'Go to advanced mode to see how many seconds {self.name} has been lagging.',
                                                                parameterType=PARAMETERTYPE.INT, indicator=True, minimum=0, maximum=10000, attr='interval_measured')
        defaultSettings[f'{self.name}/Lagging'] = parameterDict(value=0, internal=True, indicator=True, advanced=True, restore=False,
        toolTip='Shows for how many seconds the device has not been able to achieve the desired interval.\n'
                'After 10 seconds the number of displayed data points will be reduced.\n'
                'After 60 seconds the communication will be closed to keep the application responsive.',
                                                                parameterType=PARAMETERTYPE.INT, minimum=0, attr='lagging_seconds')
        defaultSettings[f'{self.name}/{self.MAXSTORAGE}'] = parameterDict(value=50, parameterType=PARAMETERTYPE.INT, minimum=5, maximum=500, event=self.estimateStorage,
                                                        toolTip='Maximum amount of storage used to store history in MB. Effective on next restart to prevent accidental data loss!',
                                                        attr='maxStorage')
        defaultSettings[f'{self.name}/{self.MAXDATAPOINTS}'] = parameterDict(value=500000, indicator=True, parameterType=PARAMETERTYPE.INT, attr='maxDataPoints',
        toolTip='Maximum number of data points saved per channel, based on max storage.\n'
        'If this is reached, older data will be thinned to allow to keep longer history.')
        defaultSettings[f'{self.name}/{self.LOGGING}'] = parameterDict(value=False,
                                                                       toolTip="""Show additional warnings in console. Only use when debugging to keep console uncluttered.\n"""
                                                                       """consider using Log level logic instead.""",
                                          parameterType=PARAMETERTYPE.BOOL, attr='log')
        defaultSettings[f'{self.name}/{self.MAXERRORCOUNT}'] = parameterDict(value=25,
                                                                             toolTip='Communication will be closed after this many accumulated errors to keep the GUI responsive.',
                                          parameterType=PARAMETERTYPE.INT, minimum=10, maximum=1000000, attr='maxErrorCount', internal=True, advanced=True,
                                          event=self.updateErrorCountToolTip)
        defaultSettings[f'{self.name}/{self.ERRORCOUNT}'] = parameterDict(value='0', toolTip='',  # tooltip will be updated by maxErrorCountChanged
                                          parameterType=PARAMETERTYPE.LABEL, attr='errorCountStr', internal=True, indicator=True, advanced=True, restore=False,
                                          event=self.errorCountChanged)
        return defaultSettings

    def setOn(self, on: 'bool | None' = None) -> None:  # noqa: D102
        super().setOn(on)
        if self.initialized:
            self.updateValues(apply=True)  # update equations before turning on or off
            if self.controller:
                self.controller.toggleOnFromThread(parallel=False)
            else:
                for channel in self.channels:
                    if channel.controller:
                        channel.controller.toggleOnFromThread(parallel=False)
        elif self.isOn():
            self.initializeCommunication()

    def runTestParallel(self) -> None:  # noqa: D102
        if hasattr(self, 'recordingAction') and self.recordingAction is not None:
            self.testControl(self.recordingAction, value=True, delay=5)
        if hasattr(self, 'initAction') and self.initAction is not None:
            self.testControl(self.initAction, value=True, delay=5)
        if hasattr(self, 'closeCommunicationAction') and self.closeCommunicationAction is not None:
            self.testControl(self.closeCommunicationAction, value=True, delay=2)
        if self.useBackgrounds and self.subtractBackgroundAction:
            self.testControl(self.subtractBackgroundAction, not self.subtractBackgroundAction.state, 1)
        super().runTestParallel()

    def bufferLagging(self, wait: 'int | None' = None) -> bool:  # noqa: D102
        # buffer lagging can be used as a temporary fix but if the underlying issue is not solved the errorcount will increase and communication will be closed.
        if super().bufferLagging(wait):
            if self.controller:
                self.controller.errorCount += 1
            else:
                for channel in self.channels:
                    if channel.controller:
                        channel.controller.errorCount += 1
            return True
        return False

    def intervalChanged(self) -> None:
        """Extend to add code to be executed in case the :ref:`acquisition_interval` changes."""
        super().intervalChanged()
        self.estimateStorage()

    def subtractBackgroundChanged(self) -> None:
        """Update plot if background subtraction is toggled."""
        if self.liveDisplayActive() and self.liveDisplay and self.subtractBackgroundAction:
            self.liveDisplay.subtractBackgroundAction.state = self.subtractBackgroundAction.state
        self.plot(apply=True)

    def updateErrorCountToolTip(self) -> None:
        """Update the tooltip of the error count setting."""
        self.pluginManager.Settings.settings[f'{self.name}/{self.ERRORCOUNT}'].setToolTip(0, (
        f"""Communication errors within last {self.pluginManager.Settings.errorResetTime} minutes.\n"""
        f"""Communication will be closed if this reaches {self.maxErrorCount} per device or per channel.\n"""
        f"""Will be reset after {self.pluginManager.Settings.errorResetTime} minutes without errors or on initialization."""))

    def errorCountChanged(self) -> None:
        """Start a timer to reset error count if no further errors occur."""
        if self.controller:
            self.errorCountStr = f'{self.errorCount}, controller: {self.controller.errorCount}'
        elif self.defaultChannel.controller:
            self.errorCountStr = f'{self.errorCount}, channel controllers: ' + ', '.join([repr(channel.controller.errorCount) for channel in self.channels if channel.controller])
        else:
            self.errorCountStr = ''  # Device does not use device controllers. Not recommended!

    def startAcquisition(self) -> None:
        """Start device Acquisition.

        Default implementation works when using :class:`~esibd.core.DeviceController`.
        """
        self.appendData(nan=True)  # prevent interpolation to old data
        if self.controller:
            self.controller.startAcquisition()
        elif self.defaultChannel.controller is not None:
            for channel in self.channels:
                if channel.enabled and channel.controller:
                    channel.controller.startAcquisition()

    def stopAcquisition(self) -> None:
        """Stop device acquisition.

        Communication stays initialized!
        Default implementation works when using :class:`~esibd.core.DeviceController`.
        """
        if self.controller:
            self.controller.stopAcquisition()
        else:
            for channel in self.channels:
                if channel.controller:
                    channel.controller.stopAcquisition()

    @property
    def initialized(self) -> bool:
        """Extend or overwrite to indicate when the device is initialized.

        Default implementation works when using :class:`~esibd.core.DeviceController`.
        """
        if not self.controller:
            if not len(self.channels) > 0 or not self.defaultChannel.controller:
                return False
            # strictly return True if any device has an initialized controller
            # ignore virtual channels
            return any(channel.controller.initialized for channel in self.channels if channel.controller)
        return self.controller.initialized

    @property
    def plotableChannels(self) -> bool:
        """Extend or overwrite to indicate when the device has plotable channels.

        Default implementation works when using :class:`~esibd.core.DeviceController`.
        """
        if not self.controller:
            if not len(self.channels) > 0 or not self.defaultChannel.controller:
                return False
            # return True if any device has an initialized controller or is not active and thus plotable even if not initialized
            return any(channel.controller.initialized or not channel.active for channel in self.channels if channel.controller)
        return self.controller.initialized

    def initializeCommunication(self) -> None:
        """Initialize communication of device or channel controllers."""
        initialRecording = self.recording
        self.appendData(nan=True)  # prevent interpolation to old data
        if self.controller:
            self.controller.initializeCommunication()
        elif self.defaultChannel.controller is not None:
            for channel in self.channels:
                if channel.controller:
                    channel.controller.errorCount = 0
                    if channel.enabled:
                        channel.controller.initializeCommunication()
        super().initializeCommunication()
        if initialRecording:
            self.startRecording()

    def closeCommunication(self) -> None:
        """Stop all communication of this Device.

        Make sure that final calls to device are send from main thread or use a delay
        so they are not send after connection has terminated.
        """
        if self.useOnOffLogic:
            self.setOn(False)
        self.stopAcquisition()  # stop acquisition before terminating communication
        if self.controller:
            self.controller.closeCommunication()
        else:
            for channel in self.channels:
                if channel.controller:
                    channel.controller.closeCommunication()
        super().closeCommunication()  # call after controller communication has been closed

    def getSupportedFiles(self) -> list[str]:  # noqa: D102
        if self.useDisplays and self.staticDisplay and self.liveDisplay:
            return self.previewFileTypes + self.staticDisplay.previewFileTypes + self.liveDisplay.previewFileTypes
        return self.previewFileTypes

    def setBackground(self) -> None:
        """Set the background based on current channel values.

        Only used by output devices.
        """
        if self.useBackgrounds:
            for channel in self.getChannels():  # save present signal as background
                # use average of last 5 s if possible
                if channel.toggleBackgroundVisible():  # sets to 0 if not visible
                    length = min(int(5000 / self.interval), len(channel.getValues(subtractBackground=False)))
                    values = channel.getValues(subtractBackground=False)[-length:]
                    if not any(np.isnan(value) for value in values):
                        channel.background = cast('float', np.mean(values))
                    elif not np.isnan(values[-1]):
                        channel.background = values[-1]
                    else:
                        channel.background = np.nan

    def subtractBackgroundActive(self) -> bool:
        """Indicate if backgrounds should be subtracted."""
        return self.subtractBackgroundAction.state if self.useBackgrounds and self.subtractBackgroundAction else False

    def estimateStorage(self) -> None:
        """Estimates storage space required to save maximal history depending on sample rate, number of channels, and backgrounds."""
        multiplier = (2 if self.useBackgrounds else 1) + len(self.defaultChannel.getRecordedParameters())
        self.maxDataPoints = int((self.maxStorage * 1024**2) / (4 * len(self.channels) * multiplier + 8))  # including +8 for time channel
        totalDays = self.interval / 1000 * self.maxDataPoints / 3600 / 24
        widget = self.pluginManager.Settings.settings[f'{self.name}/{self.MAXDATAPOINTS}'].getWidget()
        if widget:
            widget.setToolTip(
            f'Using an interval of {self.interval} ms and maximum storage of {self.maxStorage:d} MB allows for\n'
            f'a history of {totalDays:.2f} days or {self.maxDataPoints} data points for {len(self.channels)} data channels and one time channel.\n'
            'After this time, data thinning will allow to retain even older data, but at lower resolution.')

    def applyValues(self, apply: bool = False) -> None:
        """Apply :class:`~esibd.core.Channel` values to physical devices. Only used by input :class:`devices<esibd.plugins.Device>`.

        :param apply: If False, only values that have changed since last apply will be updated, defaults to False
        :type apply: bool, optional
        """
        for channel in self.getChannels():
            channel.applyValue(apply=apply)  # only actually sets value if configured and value has changed

    def loadConfiguration(self, file: Path | None = None, useDefaultFile: bool = False, append: bool = False) -> None:  # pylint: disable = missing-function-docstring  # noqa: D102
        # make sure history is saved and restored when loading configuration
        if self.initialized:
            self.print('Stop communication to load channels.', flag=PRINT.WARNING)
            return
        self.exportOutputData(useDefaultFile=True, useAllHistory=True)
        super().loadConfiguration(file=file, useDefaultFile=useDefaultFile, append=append)
        self.restoreOutputData()

    @synchronized()
    def exportOutputData(self, useDefaultFile: bool = False, useAllHistory: bool = False) -> None:
        """Export output data.

        :param useDefaultFile: Add configuration to default file, defaults to False
        :type useDefaultFile: bool, optional
        :param useAllHistory: Export entire history and not just visible part, defaults to False
        :type useAllHistory: bool, optional
        """
        if not self.liveDisplay:
            return
        if useAllHistory:
            time_axis = self.time.get()
            if time_axis.shape[0] == 0:
                return  # no data to save
        if useDefaultFile:
            self.file = Path(self.pluginManager.Settings.configPath) / self.confh5.strip('_')
        else:
            self.pluginManager.Settings.incrementMeasurementNumber()
            self.file = self.pluginManager.Settings.getMeasurementFileName(self.liveDisplay.previewFileTypes[0])
        with h5py.File(name=self.file, mode='w' if useDefaultFile else 'a', track_order=True) as h5File:
            self.hdfUpdateVersion(h5File)
            self.appendOutputData(h5File, useAllHistory=useAllHistory)
        self.print(f'Stored data in {self.file.name}')
        if useDefaultFile:
            self.exportConfiguration(file=self.file)
        else:
            self.pluginManager.DeviceManager.exportConfiguration(file=self.file)  # save corresponding device settings in measurement file
            self.pluginManager.Explorer.populateTree()

    def appendOutputData(self, h5file: h5py.File, useAllHistory: bool = False) -> None:
        """Append :class:`~esibd.plugins.Device` data to hdf file.

        :param h5file: The file to which append the data.
        :type h5file: h5py.File
        :param useAllHistory: Export entire history and not just visible part, defaults to False
        :type useAllHistory: bool, optional
        """
        if not self.liveDisplay:
            return
        fullRange = True
        group = self.requireGroup(h5file, self.name)  # , track_order=True
        time_axis = self.time.get()
        i_min = None
        i_max = None
        if not useAllHistory and time_axis.shape[0] > 0 and len(self.liveDisplay.livePlotWidgets) > 0:
            # Only save currently visible data (specific regions of interest).
            # Otherwise history of last few days might be added to files, making it hard to find the region of interest.
            # Complete data can still be exported if needed by displaying entire history before exporting.
            # if default == True: save entire history to default file for restoring on next start
            t_min, t_max = cast('PlotItem | PlotWidget', self.liveDisplay.livePlotWidgets[0]).getAxis('bottom').range
            i_min = np.argmin(np.abs(time_axis - t_min))
            i_max = np.argmin(np.abs(time_axis - t_max))
            fullRange = False
        input_group = self.requireGroup(group, INPUTCHANNELS)
        try:
            # need double precision to keep all decimal places
            input_group.create_dataset(self.TIME, data=time_axis[i_min:i_max] if not fullRange and i_min and i_max else time_axis, dtype=np.float64, track_order=True)
        except ValueError as e:
            self.print(f'Could not create data set. If the file already exists, make sure to increase the measurement number and try again. Original error: {e}', flag=PRINT.ERROR)
            return
        output_group = self.requireGroup(group, OUTPUTCHANNELS)
        # avoid using getValues() function and use get() to make sure raw data, without background subtraction or unit correction etc. is saved in file
        for channel in self.getDataChannels():
            if channel.name in output_group:
                self.print(f'Ignoring duplicate channel {channel.name}', flag=PRINT.WARNING)
                continue
            value_dataset = output_group.create_dataset(channel.name, data=channel.values.get()[i_min:i_max] if not fullRange and i_min and i_max
                                                        else channel.values.get(), dtype='f')
            value_dataset.attrs[UNIT] = self.unit
            if self.useBackgrounds:
                # Note: If data format will be changed in future (ensuring backwards compatibility), consider saving single 2D data set with data and background instead.
                background_dataset = output_group.create_dataset(f'{channel.name}_BG', data=channel.backgrounds.get()[i_min:i_max] if not fullRange and i_min and i_max
                                                                 else channel.backgrounds.get(), dtype='f')
                background_dataset.attrs[UNIT] = self.unit
            for parameter in channel.getRecordedParameters():
                parameter_dataset = output_group.create_dataset(f'{channel.name}.{parameter.name}', data=parameter.values.get()[i_min:i_max] if not fullRange and i_min and i_max
                                                                 else parameter.values.get(), dtype='f')
                parameter_dataset.attrs[UNIT] = parameter.unit

    def restoreOutputData(self) -> None:  # noqa: C901
        """Restore data from internal restore file."""
        file = Path(self.pluginManager.Settings.configPath) / self.confh5.strip('_')
        if file.exists():  # noqa: PLR1702
            self.print(f'Restoring data from {file.name}')
            with h5py.File(name=file, mode='r', track_order=True) as h5file:
                try:
                    if self.name not in h5file:
                        return
                    group = cast('h5py.Group', h5file[self.name])
                    if not (INPUTCHANNELS in group and OUTPUTCHANNELS in group):
                        return
                    input_group = cast('h5py.Group', group[INPUTCHANNELS])
                    self.time = DynamicNp(initialData=cast('h5py.Dataset', input_group[self.TIME])[:], max_size=self.maxDataPoints, dtype=np.float64)
                    output_group = cast('h5py.Group', group[OUTPUTCHANNELS])
                    for name, item in output_group.items():
                        stripped_name = name.strip('_BG')
                        for parameter in self.defaultChannel.getRecordedParameters():
                            stripped_name = stripped_name.removesuffix(f'.{parameter.name}')
                        channel = self.getChannelByName(stripped_name)
                        if channel:
                            if name == stripped_name:
                                channel.values = DynamicNp(initialData=item[:], max_size=self.maxDataPoints)
                            elif name.endswith('_BG'):
                                channel.backgrounds = DynamicNp(initialData=item[:], max_size=self.maxDataPoints)
                            else:
                                for parameter in channel.getRecordedParameters():
                                    if name.endswith(f'.{parameter.name}'):
                                        parameter.values = DynamicNp(initialData=item[:], max_size=self.maxDataPoints)
                except RuntimeError as e:
                    self.print(f'Could not restore data from {file.name}. You can try to fix and then restart. If you record new data it will be overwritten! Error {e}',
                                flag=PRINT.ERROR)

    def close(self) -> bool:  # noqa: D102
        self.closeCommunication()
        if self.hasRecorded:
            self.exportOutputData(useDefaultFile=True, useAllHistory=True)
        return super().close()

    def loadData(self, file: Path, showPlugin: bool = True) -> None:  # noqa: D102
        if self.staticDisplay and ((self.liveDisplay and self.liveDisplay.supportsFile(file)) or self.staticDisplay.supportsFile(file)):
            self.staticDisplay.loadData(file, showPlugin)
        elif self.inout == INOUT.IN:
            self.pluginManager.Text.setText('Load values using right click or import channels from file explicitly.', showPlugin=False)
        else:
            self.pluginManager.Text.setText('Import channels from file explicitly.', showPlugin=False)

    def updateValues(self, N: int = 2, apply: bool = False) -> None:  # noqa: C901, PLR0912
        """Update channel values based on equations.

        This minimal implementation will not give a warning about circular definitions.
        It will also fail if expressions are nested on more than N levels but N can be increased as needed.
        N=2 should however be sufficient for most scenarios.
        More complex algorithms should only be implemented if they are required to solve a practical problem.

        :param N: Number of iterations, defaults to 2
        :type N: int, optional
        :param apply: If False, only values that have changed since last apply will be updated, defaults to False
        :type apply: bool, optional
        """
        if self.updating or self.pluginManager.closing:
            return
        self.updating = True  # prevent recursive call caused by changing values from here
        channels = self.pluginManager.DeviceManager.channels(inout=INOUT.BOTH)
        channelNames = [channel.name for channel in channels]
        channelNames.sort(reverse=True, key=len)  # avoid replacing a subset of a longer name with a matching shorter name of another channel
        for _ in range(N):  # go through parsing N times, in case the dependencies are not ordered  # noqa: PLR1702
            for channel in [channel for channel in self.channels if not channel.active and channel.equation]:  # ignore if no equation defined
                equ = channel.equation
                error = False
                for name in channelNames:
                    if name in equ:
                        channel_equ = next((channel for channel in channels if channel.name == name), None)
                        if channel_equ:
                            if f'{name}.' in equ:
                                # replace with channel parameter value
                                for parameter in channel_equ.parameters:
                                    if parameter.parameterType in {PARAMETERTYPE.INT, PARAMETERTYPE.FLOAT, PARAMETERTYPE.EXP}:
                                        equ = equ.replace(f'{channel_equ.name}.{parameter.name}', f'{parameter.value}')
                            # replace remaining references with channel value
                            channelValue = channel_equ.value
                            if channelValue is not None:
                                equ = equ.replace(channel_equ.name, f'{channelValue - channel_equ.background if channel_equ.useBackgrounds else channelValue}')
                        else:
                            self.print(f'Could not find channel {name} in equation of channel {channel.name}.', flag=PRINT.WARNING)
                            error = True
                if error:
                    self.print(f'Could not resolve equation of channel {channel.name}: {channel.equation}', flag=PRINT.WARNING)
                else:
                    result = aeval(equ)  # or 0 evaluate does catch exception internally so we cannot except them here
                    if isinstance(result, (float, int)):
                        channel.value = result
                    else:
                        self.print(f'Could not evaluate equation of {channel.name}: {channel.equation} as {equ}')
                        channel.value = np.nan
        if self.inout == INOUT.IN:
            self.applyValues(apply)
        self.updating = False

    def toggleRecording(self, on: 'bool | None' = None, manual: bool = False) -> None:  # noqa: ARG002, D102
        if (on is not None and not on) or (on is None and self.recording):
            # Turn off if not already off
            if self.recording:
                self.recording = False
                self.pluginManager.DeviceManager.stopScans()
        elif not self.recording:
            # (on is not None and on) (on is None and not self.recording):
            # Turn on if not already on
            self.clearPlot()
            if not self.initialized:
                self.initializeCommunication()  # will start recording when initialization is complete
            else:
                self.startAcquisition()
            self.startRecording()

    def clearHistory(self) -> None:
        """Clear all data history for this device. Should only be needed if history gets out of sync and needs clean initialization."""
        if CloseDialog(title=f'Clear {self.name} History?', ok='Clear history', prompt=f'Clear all data history for {self.name}?').exec():
            self.clearPlot()
            for channel in self.getChannels():
                channel.clearHistory()
            self.time = DynamicNp(max_size=self.maxDataPoints, dtype=np.float64)

    def appendData(self, nan: bool = False, skipPlotting: bool = False) -> None:
        """Append data from device acquisition to channels and updates plots.

        :param nan: Indicates that a nan value should be appended to prevent interpolation through areas without data. Defaults to False
        :type nan: bool, optional
        :param skipPlotting: Skip plotting if previous plot was too recent, defaults to False
        :type skipPlotting: bool, optional
        """
        if self.plotableChannels or nan:
            self.updateValues()  # this makes equations work for output devices.
            # Equations for output devices are evaluated only when plotting. Calling them for every value change event would cause a massive computational load.
            for channel in self.getChannels():
                channel.appendValue(lenT=self.time.size, nan=nan)  # add time after values to make sure value arrays stay aligned with time array
            self.time.add(time.time())  # add time in seconds
            if self.liveDisplayActive():
                if skipPlotting:
                    self.print('Skipping plotting in appendData.', flag=PRINT.VERBOSE)
                    self.measureInterval(reset=False)  # do not reset but keep track of unresponsiveness
                else:
                    self.signalComm.plotSignal.emit(False)  # noqa: FBT003
            else:
                self.measureInterval()

    lagLimitMultiplier = 6  # increase to delay automatic shutoff  #leave fixed if after auto shutoff works reliably
    MAX_DISPLAY_SIZE_DEFAULT = 1000

    def measureInterval(self, reset: bool = True) -> None:
        """Measures interval since last successful plotting.

        :param reset: Only reset if plotting was successful or not required, defaults to True
        :type reset: bool, optional
        """
        # free up resources by limiting data points or stopping acquisition if UI becomes unresponsive
        # * when GUI thread becomes unresponsive, this function is sometimes delayed and sometimes too fast.
        self.interval_measured = int(time.time() * 1000 - self.lastIntervalTime) if self.lastIntervalTime is not None else self.interval
        self.lag_limit = max(10, int(10000 / self.interval))  # 10 seconds, independent of interval (at least 10 steps)
        if abs(self.interval_measured - self.interval) < self.interval_tolerance:  # * deviation in either direction is within tolerated range
            self.lagging = max(0, self.lagging - 1)  # decrease gradually, do not reset completely if a single iteration is on time
        elif self.interval_measured > self.interval + self.interval_tolerance:  # * interval is longer than tolerated -> increase self.lagging and react
            if self.lagging < self.lag_limit:
                self.lagging += 1
            elif self.lagging < self.lagLimitMultiplier * self.lag_limit:  # lagging 10 s in a row -> reduce data points
                if self.lagging == self.lag_limit:
                    self.pluginManager.DeviceManager.limit_display_size = True
                    # keep if already smaller
                    self.pluginManager.DeviceManager.max_display_size = min(self.pluginManager.DeviceManager.max_display_size, self.MAX_DISPLAY_SIZE_DEFAULT)
                self.lagging += 1
            elif self.lagging == self.lagLimitMultiplier * self.lag_limit:
                # lagging 60 s in a row -> stop acquisition
                self.print('Slow GUI detected. Stopped acquisition. Identify which plugins are most resource intensive and contact plugin author.', flag=PRINT.WARNING)
                self.closeCommunication()
        else:  # * interval is smaller than tolerated
            # keep self.lagging unchanged. One long interval can be followed by many short intervals when GUI is catching up with events.
            # This might happen due to another part of the program blocking the GUI temporarily or after decreasing max_display_size.
            # This should not trigger a reaction but also should not reset self.lagging as plotting is not yet stable.
            pass
        self.lagging_seconds = int(self.lagging * self.interval / 1000)
        if reset:
            self.lastIntervalTime = time.time() * 1000

    def runDataThread(self, recording: Callable) -> None:
        """Regularly triggers appending and plotting of data.

        This uses the current value of :class:`channels<esibd.core.Channel>` which is updated
        independently by the corresponding :class:`~esibd.core.DeviceController`.

        :param recording: Queries recording state.
        :type recording: Callable
        """
        while recording():
            # time.sleep precision in low ms range on windows -> will usually be a few ms late
            # e.g. 0.1 will not give a true 10 Hz repetition rate
            # if that becomes important and decreasing the interval to compensate for delay is not sufficient a better method is required
            interval_measured = int(time.time() * 1000 - self.lastIntervalTime) if self.lastIntervalTime is not None else self.interval
            # do only plot when at least self.interval has expired to prevent unresponsive application due to queue of multiple parallel calls to plot
            # do not plot if other plotting has not yet completed.
            skipPlotting = interval_measured < self.interval - self.interval_tolerance or self.plotting or self.bufferLagging()  # * interval is smaller than tolerated
            self.signalComm.appendDataSignal.emit(False, skipPlotting)  # arguments nan, skipPlotting cannot be added explicitly for pyqtBoundSignal  # noqa: FBT003
            time.sleep(self.interval / 1000)  # in seconds  # wait at end to avoid emitting signal after recording set to False
            # NOTE self.bufferLagging() # does not allow to reduce lag

    def duplicateChannel(self) -> None:  # noqa: D102
        if not self.modifyChannel():
            return
        if self.initialized:
            self.print(f'Stop communication for {self.name} to duplicate selected channel.', flag=PRINT.WARNING)
            return
        super().duplicateChannel()

    def deleteChannel(self) -> None:  # noqa: D102
        if not self.modifyChannel():
            return
        if self.initialized:
            self.print(f'Stop communication for {self.name} to delete selected channel.', flag=PRINT.WARNING)
            return
        super().deleteChannel()

    def moveChannel(self, up: bool) -> None:  # noqa: D102
        if not self.modifyChannel():
            return
        if self.initialized:
            self.print(f'Stop communication for {self.name} to move selected channel.', flag=PRINT.WARNING)
            return
        newChannel = super().moveChannel(up=up)
        if newChannel is not None:
            self.pluginManager.reconnectSource(newChannel.name)

    def getDisplayUnit(self) -> str:
        """Overwrite if you want to change units dynamically."""
        return self.unit

    def updateTheme(self) -> None:  # noqa: D102
        super().updateTheme()
        if self.useOnOffLogic and self.pluginManager.DeviceManager.titleBar:
            onToolButton = self.pluginManager.DeviceManager.titleBar.widgetForAction(self.onAction)
            if onToolButton:
                onToolButton.setStyleSheet(f"""QToolButton:checked{{background-color:{mix_hex_colors(colors.green, colors.bg, ratio=.7)};}}""")


class Scan(Plugin):  # noqa: PLR0904
    """Record any number of outputChannels as a function of any number of inputs.

    The main interface consists of a list of
    scan settings. Each scan comes with a tailored display
    optimized for its specific data format. :ref:`sec:scan_settings` can be imported
    and exported from the scan toolbar, though in most cases it will be
    sufficient to import them from the context menu of a previously saved
    scan file in the :ref:`sec:explorer`. When all settings are defined and all relevant channels are
    communicating the scan can be started. A scan can be stopped at any
    time. At the end of a scan the corresponding file will be saved to the
    :ref:`session path<sec:session_settings>`. The filename is displayed inside the corresponding graph to
    allow to find the file later based on exported figures. Scan files are
    saved in the widely used HDF5 file format that allows to keep data and
    metadata together in a structured binary file. External viewers, such as
    HDFView, or minimal python scripts based on the h5py package can be used
    if files need to be accessed externally. Use the
    context menu of a scan file to create a template plot file using h5py
    and adjust it to your needs.

    Note: If the Scan crashes it may be necessary to reset the finished flag before it can be started again: Scan.finished = True.
    """

    pluginType = PLUGINTYPE.SCAN
    useAdvancedOptions = True

    signalComm: 'SignalCommunicate'
    start: float
    stop: float
    step: float

    PARAMETER = 'Parameter'
    VERSION = 'Version'
    VALUE = 'Value'
    UNIT = UNIT
    NOTES = 'Notes'
    DISPLAY = 'Display'
    LEFTRIGHT = 'Left-Right'
    UPDOWN = 'Up-Down'
    WAITLONG = 'Wait long'
    LARGESTEP = 'Large step'
    WAIT = 'Wait'
    AVERAGE = 'Average'
    SCANTIME = 'Scan time'
    INVALIDWHILEWAITING = 'Invalid while waiting'
    INTERVAL = 'Interval'
    START = 'From'  # keep old names in files to stay backwards compatible
    STOP = 'To'  # keep old names in files to stay backwards compatible
    STEP = 'Step'
    CHANNEL = 'Channel'
    SCAN = 'Scan'
    INPUTCHANNELS = INPUTCHANNELS
    OUTPUTCHANNELS = OUTPUTCHANNELS
    MYBLUE = '#1f77b4'
    MYGREEN = '#00aa00'
    MYRED = '#d62728'
    file: Path
    """The scan file. Either existing file or file to be created when scan finishes."""
    useDisplayChannel: bool
    """If True, a combobox will be created to allow to select for which
       channel data should be displayed."""
    useInvalidWhileWaiting: bool = False
    """Enable setting to ignore values while stabilizing."""
    measurementsPerStep: int
    """Number of measurements per step based on the average time and acquisition rate."""
    display: 'Scan.Display'  # | None rather ignore here once that check every where
    """The internal plugin used to display scan data."""
    runThread: 'Thread | None'
    """Parallel thread that updates the scan channel(s) and reads out the display channel(s)."""
    inputChannels: list[ScanChannel | MetaChannel]
    """List of input :class:`meta channels<esibd.core.MetaChannel>`."""
    outputChannels: list[ScanChannel]
    """List of output :class:`meta channels<esibd.core.ScanChannel>`."""
    channels: list[ScanChannel | MetaChannel]
    """List of all :class:`meta channels<esibd.core.ScanChannel>`."""
    useDisplayParameter = False
    """Use display parameter to control which scan channels are displayed."""
    invalidWhileWaiting: bool = False
    """Indicates if channel values should be changed to Nan while waiting for values to stabilize."""

    class SignalCommunicate(Plugin.SignalCommunicate):
        """Bundle pyqtSignals."""

        scanUpdateSignal = pyqtSignal(bool)
        """Signal that triggers update of the figure and, if True is passed, saving of data."""
        updateRecordingSignal = pyqtSignal(bool)
        """Signal that allows to stop recording from an external thread."""
        saveScanCompleteSignal = pyqtSignal()
        """Signal that confirms that scan data has been saved and a new scan can be started."""

    ScanChannel = ScanChannel  # allows children to extend this

    class TreeSplitter(QSplitter):
        """Splitter with optimized layout for Settings and Channel trees of a scan."""

        def __init__(self, scan: 'Scan', **kwargs) -> None:
            """Initialize a TreeSplitter.

            :param scan: Parent scan.
            :type scan: Scan
            """
            self.scan = scan
            super().__init__(**kwargs)

        def resizeEvent(self, a0: 'QResizeEvent | None') -> None:
            """Make sure settingsTree takes up as much space as possible but there is no gap between settingsTree and channelTree.

            :param a0: The resize event.
            :type a0: QResizeEvent
            """
            super().resizeEvent(a0)  # Ensure default behavior
            if self.scan.settingsTree:
                self.setSizes([self.scan.settingsTree.sizeHint().height(), self.height() - self.scan.settingsTree.sizeHint().height()])

    class Display(Plugin):
        """Display for base scan. Extend as needed."""

        pluginType = PLUGINTYPE.DISPLAY
        axes: list[Axes]
        lines: list['Line2D']

        def __init__(self, scan: 'Scan', **kwargs) -> None:
            """Initialize a Scan display.

            :param scan: Parent scan.
            :type scan: Scan
            """
            self.scan = scan
            self.name = f'{self.scan.name} Display'
            self.plot = self.scan.plot
            super().__init__(**kwargs, dependencyPath=self.scan.dependencyPath, sourceCodePath=self.scan.sourceCodePath)

        def initGUI(self) -> None:  # noqa: D102
            super().initGUI()
            self.mouseMoving = False
            self.mouseActive = False
            self.initFig()  # make sure that channel dependent parts of initFig are only called after channels are initialized.

        def initFig(self) -> None:  # noqa: D102
            self.provideFig()

        def provideDock(self) -> bool:  # noqa: D102
            if super().provideDock():
                self.finalizeInit()
                self.afterFinalizeInit()
                return True
            return False

        def finalizeInit(self) -> None:  # noqa: D102
            super().finalizeInit()
            self.copyAction = self.addAction(event=self.copyClipboard, toolTip=f'{self.name} to clipboard.', icon=self.imageClipboardIcon, before=self.aboutAction)
            if self.scan.useDisplayChannel and self.titleBar:
                self.loading = True
                self.scan.loading = True
                self.displayComboBox = CompactComboBox()
                self.displayComboBox.setMaximumWidth(100)
                self.displayComboBox.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
                self.displayComboBox.currentIndexChanged.connect(self.scan.updateDisplayChannel)
                self.displayComboBox.setToolTip('Select displayed channel.')
                self.titleBar.insertWidget(self.copyAction, self.displayComboBox)
                self.updateTheme()
                self.loading = False
                self.scan.loading = False

        def test(self) -> None:  # noqa: D102
            self.print('Run tests from parent scan.', flag=PRINT.WARNING)

        def getIcon(self, desaturate: bool = False) -> Icon:  # noqa: D102
            return self.scan.getIcon(desaturate=desaturate)

        def runTestParallel(self) -> None:  # noqa: D102
            if self.initializedDock:
                self.testControl(self.copyAction, value=True, delay=.5)
            # super().runTestParallel()  # handled by scan  # noqa: ERA001

        def mouseEvent(self, event: 'Event') -> None:  # use mouse to move beam  # use ctrl key to avoid this while zooming
            """Handle dragging beam in 2D scan or setting retarding grid potential in energy scan.

            :param event: The mouse event.
            :type event: Event
            """
            if not self.mouseActive:
                return
            if self.mouseMoving and event.name != 'button_release_event':  # dont trigger events until last one has been processed
                return
            self.mouseMoving = True
            if event.button == MouseButton.LEFT and kb.is_pressed('ctrl') and event.xdata is not None:  # type: ignore  # noqa: PGH003
                if not self.scan.finished:  # as long as scan is running it should define values and curser positions without user interference
                    self.print('Cannot move cursors while scan is running.', flag=PRINT.WARNING)
                    return
                for i, inputChannel in enumerate(self.scan.inputChannels):
                    if inputChannel.sourceChannel:
                        if i == 0:
                            inputChannel.sourceChannel.value = event.xdata  # type: ignore  # noqa: PGH003
                        elif event.ydata:  # type: ignore  # noqa: PGH003
                            inputChannel.sourceChannel.value = event.ydata  # type: ignore  # noqa: PGH003
                    else:
                        self.print(f'Could not find input channel {self.scan.inputChannels[i].name}.')
                if hasattr(self.axes[-1], 'cursor'):
                    cast('CursorAxes', self.axes[-1]).cursor.ondrag(event)
            self.mouseMoving = False

        def closeGUI(self) -> None:  # noqa: D102
            if self.scan.finished:
                super().closeGUI()
            else:
                self.print('Cannot close while scan is recording.')

    def __init__(self, **kwargs) -> None:
        """Initialize a Scan."""
        super().__init__(**kwargs)
        self.getChannelByName = self.pluginManager.DeviceManager.getChannelByName
        self._finished = True
        self.file = Path()
        self.configINI = f'{self.name}.ini'
        self.previewFileTypes = [self.configINI, f'{self.name.lower()}.h5']
        self.useDisplayChannel = False
        self.measurementsPerStep = 0
        self.oldDisplayItems = ''
        self._dummy_initialization = False
        self.stepProcessed = True
        self.inputChannelGroupItem: 'QTreeWidgetItem | None' = None
        self.outputChannelGroupItem: 'QTreeWidgetItem | None' = None
        self.display = None  # type: ignore  # noqa: PGH003
        self.runThread = None
        self.saveThread = None
        self.settingsTree = None
        self.channelTree = None
        self.initializing = False
        self.headerChannel = ScanChannel(scan=self)  # dummy channel only used to get headers
        self.channels = []
        self.signalComm.scanUpdateSignal.connect(self.scanUpdate)
        self.signalComm.updateRecordingSignal.connect(self.updateRecording)
        self.signalComm.saveScanCompleteSignal.connect(self.saveScanComplete)
        self.initData()

    def initGUI(self) -> None:  # noqa: D102
        self.loading = True
        super().initGUI()
        self.treeSplitter = self.TreeSplitter(scan=self, orientation=Qt.Orientation.Vertical)
        self.treeSplitter.setStyleSheet('QSplitter::handle{width:0px; height:0px;}')
        self.settingsTree = TreeWidget()
        self.settingsTree.setMinimumWidth(200)
        self.settingsTree.setHeaderLabels([self.PARAMETER, self.VALUE])
        self.settingsTree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.settingsTree.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        # size to content prevents manual resize
        header = self.settingsTree.header()
        if header:
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.settingsLayout = QHBoxLayout()
        self.settingsLayout.setContentsMargins(0, 0, 0, 0)
        self.settingsLayout.setSpacing(0)
        self.settingsLayout.addWidget(self.settingsTree, alignment=Qt.AlignmentFlag.AlignTop)
        widget = QWidget()
        widget.setLayout(self.settingsLayout)
        self.treeSplitter.addWidget(widget)
        self.treeSplitter.setCollapsible(0, False)  # noqa: FBT003
        self.channelTree = TreeWidget()  # minimizeHeight=True)
        header = self.channelTree.header()
        if header:
            header.setStretchLastSection(False)
            header.setMinimumSectionSize(0)
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.channelTree.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.channelTree.setRootIsDecorated(False)
        self.treeSplitter.addWidget(self.channelTree)
        self.treeSplitter.setCollapsible(1, True)  # noqa: FBT003
        self.addContentWidget(self.treeSplitter)
        self.settingsMgr = SettingsManager(parentPlugin=self, pluginManager=self.pluginManager, tree=self.settingsTree, name=f'{self.name} Settings',
                                        defaultFile=self.pluginManager.Settings.configPath / self.configINI,
                                        dependencyPath=self.pluginManager.Settings.dependencyPath, sourceCodePath=self.pluginManager.Settings.sourceCodePath)
        self.settingsMgr.addDefaultSettings(plugin=self)
        self.settingsMgr.init()
        self.settingsMgr.tree.expandAllItems()
        self.notes = ''  # should always have current notes or no notes
        self.addAction(event=lambda: self.loadSettings(file=None), toolTip=f'Load {self.name} settings.', icon=self.makeCoreIcon('blue-folder-import.png'))
        self.addAction(event=lambda: self.saveSettings(file=None), toolTip=f'Export {self.name} settings.', icon=self.makeCoreIcon('blue-folder-export.png'))
        self.recordingAction = self.addStateAction(event=self.toggleRecording,
                                                   toolTipFalse=f'Start {self.name} scan.', iconFalse=self.makeCoreIcon('play.png'),
                                                   toolTipTrue='Stop.', iconTrue=self.makeCoreIcon('stop.png'))
        self.estimateScanTime()
        self.loading = False
        self.dummyInitialization()

    def finalizeInit(self) -> None:  # noqa: D102
        self.statusAction = self.pluginManager.DeviceManager.addStateAction(event=self.statusActionEvent,
                                                toolTipFalse=f'{self.name} is running. Go to {self.name}.', iconFalse=self.getIcon(),
                                                before=self.pluginManager.DeviceManager.aboutAction, restore=False, defaultState=True)
        self.statusAction.state = True
        self.statusAction.setVisible(False)
        super().finalizeInit()

    def statusActionEvent(self) -> None:
        """Show scan and corresponding display."""
        if self.display:
            self.raiseDock(showPlugin=True)
            self.display.raiseDock(showPlugin=True)

    def afterFinalizeInit(self) -> None:  # noqa: D102
        super().afterFinalizeInit()
        self.connectAllSources()

    def connectAllSources(self) -> None:
        """Connect all available source channels."""
        # NOTE: inputChannels are already connected on creation
        if len(self.channels) > 0:
            self.loading = True
            for channel in self.channels:
                if channel.name != self.TIME:
                    channel.connectSource()
            self.loading = False
            if len(self.outputChannels) > 0:
                # update only once for all channels
                self.outputChannels[0].updateDisplay()

    def reconnectSource(self, name: str) -> None:
        """Reconnect a specific source channel.

        :param name: name of channel to reconnect
        :type name: str
        """
        for channel in self.channels:
            if channel.name == name:
                self.print(f'Source channel {channel.name} may have been lost. Attempt reconnecting.', flag=PRINT.DEBUG)
                channel.connectSource(giveFeedback=True)

    def initData(self) -> None:
        """Clear all channels before (re-)initialization."""
        for channel in self.channels:
            channel.onDelete()
        if self.channelTree:
            self.channelTree.clear()
            self.channelTree.setIndentation(0)  # indentation can push device icon out of visible part of first column
            self.inputChannelGroupItem = QTreeWidgetItem(self.channelTree.invisibleRootItem(), ['Input Channels'])
            self.channelTree.setFirstColumnSpanned(self.channelTree.indexOfTopLevelItem(self.inputChannelGroupItem), self.channelTree.rootIndex(), True)  # noqa: FBT003
            self.outputChannelGroupItem = QTreeWidgetItem(self.channelTree.invisibleRootItem(), ['Output Channels'])
            self.channelTree.setFirstColumnSpanned(self.channelTree.indexOfTopLevelItem(self.outputChannelGroupItem), self.channelTree.rootIndex(), True)  # noqa: FBT003
        self.inputChannels, self.outputChannels, self.channels = [], [], []

    def runTestParallel(self) -> None:  # noqa: D102
        # * testing the scan itself is done by DeviceManager
        if self.initializedDock and self.displayActive() and self.display:
            self.display.raiseDock(showPlugin=True)
            self.display.runTestParallel()
        super().runTestParallel()

    @property
    def recording(self) -> bool:
        """Indicate recording state. Set to False to stop recording and save available data."""
        return self.recordingAction.state

    @recording.setter
    def recording(self, recording: bool) -> None:
        # make sure to only call from main thread as GUI is affected!
        self.recordingAction.state = recording
        self.statusAction.setVisible(recording)

    @property
    def finished(self) -> bool:
        """Indicate finished state.

        True before and after scanning. Even when :attr:`~esibd.plugins.Scan.recording` is set to False
        it will take time for the scan to complete and be ready to be started again.
        """
        return self._finished

    @finished.setter
    def finished(self, finished: bool) -> None:
        self._finished = finished
        # disable inputs while scanning
        for setting in [self.START, self.STOP, self.STEP, self.CHANNEL]:
            if setting in self.settingsMgr.settings:
                self.settingsMgr.settings[setting].setEnabled(finished)

    def updateFile(self) -> None:
        """Update scan file."""
        if self._dummy_initialization:
            self.file = Path()
        else:
            self.pluginManager.Settings.incrementMeasurementNumber()
            self.file = self.pluginManager.Settings.getMeasurementFileName(f'_{self.name.lower()}.h5')
        if self.displayActive() and self.display:
            self.display.file = self.file  # for direct access of MZCalculator or similar addons that are not aware of the scan itself

    def updateDisplayChannel(self) -> None:
        """Update plot after changing display channel."""
        if (self.displayActive() and self.useDisplayChannel and
            not any([self.pluginManager.loading, self.loading, self.settingsMgr.loading, self.recording, self.initializing])):
            self.plot(update=False, done=True)

    def updateDisplayDefault(self) -> None:
        """Select displayed Channel based on default display setting."""
        newDisplayItems = ', '.join(self.settingsMgr.settings[self.DISPLAY].items)

        if self.oldDisplayItems != newDisplayItems:
            # channels renamed, deleted or added: initialize everything
            if not self.loading:
                self.dummyInitialization()
            self.oldDisplayItems = newDisplayItems
        elif self.display and self.displayActive() and self.useDisplayChannel:
            # only selection changed
            self.loading = True
            i = self.display.displayComboBox.findText(self.displayDefault)
            if i == -1:
                self.display.displayComboBox.setCurrentIndex(0)
            else:
                self.display.displayComboBox.setCurrentIndex(i)
            self.loading = False
            self.updateDisplayChannel()

    def dummyInitialization(self) -> None:
        """Initialize scan without data.

        Will populate scan channels and indicate that channels have become obsolete by plotting without data if applicable.
        """
        if self.finished and not self.recording and not self._dummy_initialization:
            self.initData()
            self._dummy_initialization = True
            self.initScan()  # runs addOutputChannels without trying to plot / also adds inputChannels
            self._dummy_initialization = False
            if self.displayActive():
                self.plot(update=False, done=False)  # init plot without data

    def populateDisplayChannel(self) -> None:
        """Populate dropdown that allows to select displayed channel for scans that can only display one channel at a time."""
        if self.display and self.displayActive() and self.useDisplayChannel:
            self.loading = True
            self.display.displayComboBox.clear()
            for outputChannel in self.outputChannels:  # use channels form current acquisition or from file.
                self.display.displayComboBox.insertItem(self.display.displayComboBox.count(), outputChannel.name)
            self.loading = False
            if not self._dummy_initialization:  # prevent recursion
                self.updateDisplayDefault()

    def loadSettings(self, file: 'Path | None' = None, useDefaultFile: bool = False) -> None:
        """Load scan settings from file.

        :param file: File to load settings from, defaults to None
        :type file: pathlib.Path, optional
        :param useDefaultFile: Indicates if default file should be used, defaults to False
        :type useDefaultFile: bool, optional
        """
        self.settingsMgr.loadSettings(file=file, useDefaultFile=useDefaultFile)
        self.settingsMgr.tree.expandAllItems()
        self.updateDisplayChannel()
        self.estimateScanTime()

    def saveSettings(self, file: 'Path | None' = None, useDefaultFile: bool = False) -> None:
        """Save scan settings to file.

        :param file: File to save settings to, defaults to None
        :type file: pathlib.Path, optional
        :param useDefaultFile: Indicates if default file should be used, defaults to False
        :type useDefaultFile: bool, optional
        """
        self.settingsMgr.saveSettings(file=file, useDefaultFile=useDefaultFile)

    def getDefaultSettings(self) -> dict[str, dict]:  # noqa: D102
        # NOTE: Changing Setting names will cause backwards incompatibility unless handled explicitly!
        # NOTE: Scan settings should not be internal as they will not have scan

        # definitions for type hinting
        self.notes: str
        self.displayDefault: str
        self.wait: int
        self.waitLong: int
        self.largestep: float
        self.average: int
        self.scantime: str
        self.invalidWhileWaiting: bool

        ds = {}
        ds[self.NOTES] = parameterDict(value='', toolTip='Add specific notes to current scan. Will be reset after scan is saved.', parameterType=PARAMETERTYPE.TEXT,
                                        attr='notes')
        ds[self.DISPLAY] = parameterDict(value='RT_Front-Plate', toolTip='Default output channel used when scanning. Other channels defined here will be recorded as well.',
                                         items='RT_Front-Plate, RT_Detector, RT_Sample-Center, RT_Sample-End, LALB-Aperture',
                                         parameterType=PARAMETERTYPE.COMBO, attr='displayDefault', event=self.updateDisplayDefault)
        # * alternatively the wait time could be determined proportional to the step.
        # While this would be technically cleaner and more time efficient,
        # the present implementation is easier to understand and should work well as long as the step sizes do not change too often
        ds[self.WAIT] = parameterDict(value=500, toolTip='Wait time before start of averaging in ms. '
                                      'This time allows for input value to change and output values to stabilize.'
                                      , minimum=10, event=self.estimateScanTime,
                                                                        parameterType=PARAMETERTYPE.INT, attr='wait')
        ds[self.WAITLONG] = parameterDict(value=2000, toolTip=f'Wait time for steps larger than {self.LARGESTEP} in ms.', minimum=10,
                                                                        parameterType=PARAMETERTYPE.INT, attr='waitLong', event=self.estimateScanTime)
        ds[self.LARGESTEP] = parameterDict(value=2, toolTip='Threshold step size to use longer wait time.', event=self.estimateScanTime,
                                                                        parameterType=PARAMETERTYPE.FLOAT, attr='largestep')
        ds[self.AVERAGE] = parameterDict(value=1000, toolTip='Time used for averaging in ms.', parameterType=PARAMETERTYPE.INT, attr='average', event=self.estimateScanTime)
        ds[self.SCANTIME] = parameterDict(value='n/a', toolTip='Estimated scan time.', parameterType=PARAMETERTYPE.LABEL, attr='scantime', internal=True, indicator=True)
        if self.useInvalidWhileWaiting:
            ds[self.INVALIDWHILEWAITING] = parameterDict(value=False, toolTip='Check to disable device readings during wait period,\n'
                                                 'e.g. to avoid incorrect readings due to pickup when changing a voltage.\n'
                                                 'The device will only return NaN while waiting to stabilize.\n'
                                                 'Note that this is especially useful when running different scans in parallel.'
                                                 , parameterType=PARAMETERTYPE.BOOL, attr='invalidWhileWaiting', advanced=True, internal=True)
        return ds

    def getOutputIndex(self) -> int:
        """Get the index of the output channel corresponding to the currently selected display channel. See :attr:`~esibd.plugins.Scan.useDisplayChannel`."""
        if self.useDisplayChannel and self.display:
            try:
                if self.displayActive():
                    return next((i for i, output in enumerate(self.outputChannels) if output.name == self.display.displayComboBox.currentText()), 0)
                return next((i for i, output in enumerate(self.outputChannels) if output.name == self.displayDefault), 0)
            except ValueError:
                return 0
        return 0

    def initScan(self) -> bool:
        """Initialize all data and metadata.

        Returns True if initialization successful and scan is ready to start.
        Will likely need to be adapted for custom scans.
        """
        self.initializing = True
        self.addInputChannels()
        self.addOutputChannels()
        self.finalizeChannelTree()
        self.initializing = False
        initialized = True
        if len(self.outputChannels) == 0:
            if not self._dummy_initialization:
                self.print('No initialized output channel found.', flag=PRINT.WARNING)
            initialized = False
        if len(self.inputChannels) != len([channel for channel in self.channels if channel.inout == INOUT.IN]):
            if not self._dummy_initialization:
                self.print('Not all input channels initialized.', flag=PRINT.WARNING)
            initialized = False
        if initialized:
            device = self.outputChannels[self.getOutputIndex()].getDevice()
            if not self.loading and not self._dummy_initialization and isinstance(device, ChannelManager):
                self.measurementsPerStep = max(int(self.average / device.interval) - 1, 1)
            if not self._dummy_initialization:
                self.toggleDisplay(visible=True)
            self.updateFile()
            self.populateDisplayChannel()
            return True
        return False

    def finalizeChannelTree(self) -> None:
        """Expand all items and hide unused category."""
        if self.channelTree:
            self.channelTree.expandAllItems()
            self.channelTree.setMinimumHeight(self.channelTree.tree_height_hint_minimal())  # enough to show two input and two output channels
        if self.inputChannelGroupItem and self.inputChannelGroupItem.childCount() == 0:
            self.inputChannelGroupItem.setHidden(True)

    def addOutputChannels(self) -> None:
        """Add all output channels."""
        self.print('addOutputChannels', flag=PRINT.DEBUG)
        recordingData = None
        if not self.channelTree or len(self.inputChannels) == 0:
            return
        inputRecordingData0 = self.inputChannels[0].getRecordingData()
        if len(self.inputChannels) == 1 and inputRecordingData0 is not None:  # 1D scan
            recordingData = np.zeros(len(inputRecordingData0), dtype=np.float32)  # cant reuse same array for all outputChannels as they would refer to same instance.
        elif len(self.inputChannels) == 2 and inputRecordingData0 is not None and self.inputChannels[1].getRecordingData() is not None:  # noqa: PLR2004
            # 2D scan, higher dimensions not jet supported
            lengths = [len(data) if data is not None else 0 for inputChannel in self.inputChannels for data in [inputChannel.getRecordingData()]]
            recordingData = np.zeros(np.prod(lengths), dtype=np.float32).reshape(*lengths).transpose()
            # note np.zeros works better than np.full(len, np.nan) as some plots give unexpected results when given np.nan
        if self.DISPLAY in self.getDefaultSettings():
            for name in self.settingsMgr.settings[self.DISPLAY].items:
                self.addOutputChannel(name=name, recordingData=recordingData.copy() if recordingData is not None else None)
            self.channelTree.setHeaderLabels([parameterDict.get(Parameter.HEADER, '') or name.title()
                                            for name, parameterDict in self.headerChannel.getSortedDefaultChannel().items()])
            self.toggleAdvanced(advanced=False)
        else:
            self.channelTree.hide()

    def addOutputChannel(self, name: str, unit: str = '', recordingData: 'np.ndarray | DynamicNp | None' = None,  # noqa: C901, PLR0912
                          recordingBackground: 'np.ndarray | None' = None) -> ScanChannel | None:
        """Convert channel to generic output data.

        Uses data from file if provided.

        :param name: channel name.
        :type name: str
        :param unit: channel unit, defaults to ''
        :type unit: str, optional
        :param recordingData: Recorded values from previous scan or initialized array for new scan, defaults to None
        :type recordingData: np.ndarray, optional
        :param recordingBackground: Recorded background values from previous scan or initialized array for new scan, defaults to None
        :type recordingBackground: np.ndarray, optional
        :return: Generic channel that is used to store and restore scan data.
        :rtype: esibd.core.ScanChannel
        """
        if not self.outputChannelGroupItem:
            return None
        sourceInitialized = True
        outputChannel = self.ScanChannel(scan=self, tree=self.channelTree)
        self.outputChannelGroupItem.addChild(outputChannel)
        outputChannel.initGUI(item={Parameter.NAME: name, ScanChannel.UNIT: unit})
        outputChannel.inout = INOUT.OUT
        self.channels.append(outputChannel)
        if not self.loading or recordingData is not None:
            outputChannel.connectSource()
            if not outputChannel.sourceChannel:
                if not self._dummy_initialization and not self.loading:
                    self.print(f'Could not find output channel {name}.', flag=PRINT.WARNING)
                sourceInitialized = False
        if not self.loading:
            device = outputChannel.getDevice()
            if isinstance(device, Device):
                if not device.initialized:
                    if not self._dummy_initialization:
                        self.print(f'{device.name} is not initialized.', flag=PRINT.WARNING)
                    sourceInitialized = False
                elif outputChannel.real and not outputChannel.acquiring:
                    # do not check for virtual channels
                    if not self._dummy_initialization:
                        self.print(f'{outputChannel.name} is not acquiring.', flag=PRINT.WARNING)
                    sourceInitialized = False
                elif outputChannel.real and not device.recording:
                    # do not check for virtual channels
                    if not self._dummy_initialization:
                        self.print(f'{outputChannel.name} is not recording.', flag=PRINT.WARNING)
                    sourceInitialized = False
            if (outputChannel.sourceParameter and not outputChannel.sourceParameter.indicator) or (outputChannel.sourceChannel and outputChannel.sourceChannel.inout == INOUT.IN):  # noqa: SIM102
                # Input channels only write and only have a read option for monitors.
                # May still make sense if the output channel is a virtual channel only used for calculations.
                if not self._dummy_initialization:
                    self.print(f'Using input channel {name} as output channel.', flag=PRINT.WARNING)
        if recordingData is not None:
            outputChannel.recordingData = recordingData  # type: ignore  # noqa: PGH003
        if recordingBackground is not None:
            outputChannel.recordingBackground = recordingBackground
        if ((self.loading and outputChannel.recordingData is not None) or sourceInitialized):
            self.outputChannels.append(outputChannel)
        return outputChannel

    MIN_STEPS = 3

    def addInputChannels(self) -> None:
        """Add all input channels.

        Extend to add scan specific input channels.
        """
        self.print('addInputChannels', flag=PRINT.DEBUG)

    def addTimeInputChannel(self) -> MetaChannel:
        """Add a time channel to save the time of each step in the scan.

        :return: The time channel
        :rtype: MetaChannel
        """
        timeChannel = MetaChannel(parentPlugin=self, name=self.TIME, recordingData=DynamicNp(dtype=np.float64))
        timeChannel.inout = INOUT.IN
        self.channels.append(timeChannel)
        self.inputChannels.append(timeChannel)
        return timeChannel

    def addInputChannel(self, name: str, start: 'float | None' = None, stop: 'float | None' = None,  # noqa: C901, PLR0912, PLR0913, PLR0917
                        step: 'float | None' = None, unit: str = '', recordingData: 'np.ndarray | None' = None) -> ScanChannel | None:
        """Convert channel to generic input data.

        :param name: Channel name.
        :type name: str
        :param start: Start value, defaults to None
        :type start: float
        :param stop: Final value, defaults to None
        :type stop: float
        :param step: Step size, defaults to None
        :type step: float
        :param unit: channel unit, defaults to ''
        :type unit: str, optional
        :param recordingData: Recorded values from previous scan or initialized array for new scan, defaults to None
        :type recordingData: np.ndarray, optional
        :return: Generic channel that is used to store and restore scan data.
        :rtype: esibd.core.ScanChannel
        """
        if not self.inputChannelGroupItem:
            return None
        sourceInitialized = True
        inputChannel = self.ScanChannel(scan=self, tree=self.channelTree)
        self.inputChannelGroupItem.addChild(inputChannel)
        inputChannel.initGUI(item={Parameter.NAME: name, ScanChannel.UNIT: unit})
        inputChannel.inout = INOUT.IN
        self.channels.append(inputChannel)
        if not self.loading:
            inputChannel.connectSource()
            device = inputChannel.getDevice()
            if not inputChannel.sourceChannel:  # noqa: SIM102
                if not self._dummy_initialization and not self.loading:
                    self.print(f'No channel found with name {name}.', flag=PRINT.WARNING)
            if not (start is not None and stop is not None and step is not None and
                    (not hasattr(inputChannel, 'min') or (inputChannel.min is not None and inputChannel.max is not None))
                      and device and isinstance(device, Device)):
                self.print(f'Not enough information to initialize input channel {name}.', flag=PRINT.WARNING)
                return None
            if hasattr(self, 'wait') and hasattr(self, 'average') and self.wait + self.average < inputChannel.getDevice().interval:  # noqa: SIM102
                # If input device does not update fast enough it may be lagging behind and indicate longer steps.
                # This may cause use of waitLong instead of wait but should not have other side effects.
                if not self._dummy_initialization:
                    self.print(f'Input device {inputChannel.getDevice().name} does not update fast enough for given values of wait and average.', flag=PRINT.WARNING)
            if (inputChannel.sourceParameter and inputChannel.sourceParameter.indicator) or (inputChannel.sourceChannel and inputChannel.sourceChannel.inout == INOUT.OUT):  # noqa: SIM102
                # Output channels only read and have no set option as well as no min max range.
                # May still make sense if the output channel is a virtual channel only used for calculations.
                if not self._dummy_initialization:
                    self.print(f'Using output channel {name} as input channel.', flag=PRINT.WARNING)
            if start == stop:
                if not self._dummy_initialization:
                    self.print('Limits are equal.', flag=PRINT.WARNING)
                sourceInitialized = False
            elif hasattr(inputChannel, 'min') and (inputChannel.min > min(start, stop) or inputChannel.max < max(start, stop)):
                if not self._dummy_initialization:
                    self.print(f'Limits are larger than allowed for {name}.', flag=PRINT.WARNING)
                sourceInitialized = False
            elif not device.initialized:
                if not self._dummy_initialization:
                    self.print(f'{inputChannel.getDevice().name} is not initialized.', flag=PRINT.WARNING)
                sourceInitialized = False
            else:
                inputChannel.recordingData = self.getSteps(start, stop, step)
                if inputChannel.recordingData is not None and len(inputChannel.recordingData) < self.MIN_STEPS:
                    if not self._dummy_initialization:
                        self.print('Not enough steps.', flag=PRINT.WARNING)
                    sourceInitialized = False
        elif recordingData is not None:
            inputChannel.recordingData = recordingData
        if ((self.loading and inputChannel.recordingData is not None) or sourceInitialized):
            self.inputChannels.append(inputChannel)
        return inputChannel

    def getSteps(self, start: float, stop: float, step: float) -> np.ndarray | None:
        """Return steps based on start, stop, and step parameters.

        :param start: Start value.
        :type start: float
        :param stop: Final value.
        :type stop: float
        :param step: Step size.
        :type step: float
        :return: Array of steps.
        :rtype: np.ndarray
        """
        if start == stop:
            self.print('Limits are equal.', flag=PRINT.WARNING)
            return None
        return np.arange(start, stop + step * np.sign(stop - start), step * np.sign(stop - start))

    def getData(self, i: int, inout: INOUT) -> np.ndarray | None:
        """Get the data of a scan channel based on index and type.

        :param i: Index of channel.
        :type i: int
        :param inout: Type of channel.
        :type inout: :attr:`~esibd.const.INOUT`
        :return: The requested data.
        :rtype: np.ndarray
        """
        return self.inputChannels[i].getRecordingData() if inout == INOUT.IN else self.outputChannels[i].getRecordingData()

    def toggleAdvanced(self, advanced: 'bool | None' = False) -> None:  # noqa: D102
        if advanced is not None:
            self.advancedAction.state = advanced
        for setting in self.settingsMgr.settings.values():
            if setting.advanced:
                setting.setHidden(not self.advancedAction.state)
        if len(self.channels) > 0 and self.channelTree:
            for i, item in enumerate(self.headerChannel.getSortedDefaultChannel().values()):
                if item[Parameter.ADVANCED]:
                    self.channelTree.setColumnHidden(i, not self.advancedAction.state)

    def estimateScanTime(self) -> None:
        """Estimates scan time. Will likely need to be adapted for custom scans."""
        # overwrite with scan specific estimation if applicable
        if hasattr(self, 'start'):
            # Simple time estimate for scan with single input channel.
            steps = self.getSteps(self.start, self.stop, self.step)
            seconds = 0  # estimate scan time
            if steps is not None and len(steps) > 0:
                for i in range(len(steps)):  # pylint: disable = consider-using-enumerate
                    waitLong = False
                    if not waitLong and abs(steps[i - 1] - steps[i]) > self.largestep:
                        waitLong = True
                    seconds += (self.waitLong if waitLong else self.wait) + self.average
                seconds = round((seconds) / 1000)
                self.scantime = f'{seconds // 60:02d}:{seconds % 60:02d}'
            else:
                self.scantime = 'n/a'
        else:
            self.scantime = 'n/a'

    def saveData(self, file: Path) -> None:
        """Write generic scan data to hdf file.

        :param file: The file to save data to.
        :type file: pathlib.Path
        """
        with h5py.File(file, 'a', track_order=True) as h5File:
            # avoid using getValues() function and use get() to make sure raw data, without background subtraction or unit correction etc. is saved in file
            top_group = self.requireGroup(h5File, self.name)  # , track_order=True
            input_group = self.requireGroup(top_group, self.INPUTCHANNELS)
            for j, inputChannel in enumerate(self.inputChannels):
                try:
                    dataset = input_group.create_dataset(name=inputChannel.name, data=self.getData(j, INOUT.IN), track_order=True)
                    dataset.attrs[self.UNIT] = self.inputChannels[j].unit
                except ValueError as e:
                    self.print(f'Cannot create dataset for channel {inputChannel.name}: {e}', flag=PRINT.ERROR)

            output_group = self.requireGroup(top_group, self.OUTPUTCHANNELS)
            for j, output in enumerate(self.outputChannels):
                if output.name in output_group:
                    self.print(f'Ignoring duplicate channel {output.name}', flag=PRINT.WARNING)
                    continue
                try:
                    dataset = output_group.create_dataset(name=output.name, data=self.getData(j, INOUT.OUT), track_order=True)
                    dataset.attrs[self.UNIT] = self.outputChannels[j].unit
                except ValueError as e:
                    self.print(f'Cannot create dataset for channel {output.name}: {e}', flag=PRINT.ERROR)

    def loadData(self, file: Path, showPlugin: bool = True) -> None:  # noqa: D102
        if file.name.endswith(self.configINI):
            return  # will be handled by Text plugin
        if self.finished:
            self.toggleDisplay(visible=True)
            if self.display:
                self.file = file
                self.display.file = file  # for direct access of MZCalculator or similar addons that are not aware of the scan itself
                self.loading = True
                self.initData()
                if self.loadDataInternal():
                    self.connectAllSources()
                    if self.useDisplayChannel:
                        self.populateDisplayChannel()  # select default scan channel if available
                    self.loading = False
                else:
                    self.toggleDisplay(visible=False)
                    self.loading = False
                    return
                self.plot(update=False, done=True)  # self.populateDisplayChannel() does not trigger plot while loading
                self.display.raiseDock(showPlugin)
        else:
            self.print('Cannot open file while scanning.', flag=PRINT.WARNING)

    def loadDataInternal(self) -> bool:
        """Load data from scan file. Data is stored in scan-neutral format of input and output channels.

        Extend to provide support for previous file formats.
        """
        with h5py.File(self.file, 'r') as h5file:
            group = cast('h5py.Group', h5file[self.name])
            input_group = cast('h5py.Group', group[self.INPUTCHANNELS])
            for name, data in input_group.items():
                if name == self.TIME:
                    timeChannel = self.addTimeInputChannel()
                    timeChannel.recordingData = data[:]
                else:
                    self.addInputChannel(name=name, unit=data.attrs[self.UNIT], recordingData=data[:])
            output_group = cast('h5py.Group', group[self.OUTPUTCHANNELS])
            for name, data in output_group.items():
                self.addOutputChannel(name=name, unit=data.attrs[self.UNIT], recordingData=data[:])
            self.finalizeChannelTree()
        return True

    def generatePythonPlotCode(self) -> str:  # noqa: D102
        return self.pythonLoadCode() + self.pythonPlotCode()

    def pythonLoadCode(self) -> str:
        """Import channels from scan file."""
        return f"""import h5py
import numpy as np
import matplotlib.pyplot as plt
inputChannels, outputChannels = [], []
class MetaChannel():
    def __init__(self, name, recordingData, initialValue=None, recordingBackground=None, unit=''):
        self.name = name
        self.recordingData = recordingData
        self.initialValue = initialValue
        self.recordingBackground = recordingBackground
        self.unit = unit

    @property
    def logY(self):
        if self.unit in ['mbar', 'Pa']:
            return True
        else:
            return False

with h5py.File('{self.pluginManager.Explorer.activeFileFullPath.as_posix() if self.pluginManager.Explorer.activeFileFullPath else ''}','r') as h5file:
    group = h5file['{self.name}']

    input_group = group['Input Channels']
    for name, data in input_group.items():
        inputChannels.append(MetaChannel(name=name, recordingData=data[:], unit=data.attrs['Unit']))
    output_group = group['Output Channels']
    for name, data in output_group.items():
        outputChannels.append(MetaChannel(name=name, recordingData=data[:], unit=data.attrs['Unit']))

# select channel to plot
output_index = next((i for i, output in enumerate(outputChannels) if output.name == '{self.outputChannels[0].name if len(self.outputChannels) > 0 else "no output found"}'), 0)

"""

    def pythonPlotCode(self) -> str:
        """Define minimal code to create a plot which can be customized by user.

        Accessible from context menu of scan files.
        Overwrite to add scan specific plot code here.
        """
        return """# Add your custom plot code here:"""

    def toggleRecording(self) -> None:
        """Handle start and stop of scan."""
        if self.recording:
            if self.finished:
                self.initData()
                if self.initScan() and self.display:
                    if self.runThread is not None and self.recording:  # stop old scan if applicable
                        self.recordingAction.state = False  # allow thread to finish without triggering toggleRecording recursion
                        self.runThread.join()
                        self.recordingAction.state = True
                    self.finished = False
                    self.stepProcessed = True
                    self.plot(update=False, done=False)  # init plot without data, some widgets may be able to update data only without redrawing the rest
                    self.runThread = Thread(target=self.runScan, args=(lambda: self.recording,), name=f'{self.name} runThread')
                    self.runThread.daemon = True
                    self.runThread.start()
                    self.display.raiseDock()
                else:
                    self.recordingAction.state = False
            else:
                self.print('Wait for scan to finish.')
                self.recordingAction.state = False
        else:
            self.print('Stopping scan.')
        self.statusAction.setVisible(self.recordingAction.state)

    def scanUpdate(self, done: bool = False) -> None:
        """Update plot and saves data when done.

        :param done: Call with True in last step of scan to indicate that data should be saved to file. Defaults to False
        :type done: bool, optional
        """
        self.print('scanUpdate', flag=PRINT.VERBOSE)
        self.plot(update=not done, done=done)
        self.stepProcessed = True
        if done:  # save data
            root = self.pluginManager.Explorer.root
            if hasattr(self.pluginManager, 'Notes') and root:
                self.pluginManager.Notes.saveData(file=root, useDefaultFile=True)  # to current root ( == session if in session folder)
            notesFile = self.pluginManager.Settings.getFullSessionPath() / 'notes.txt'
            if notesFile.exists():
                with notesFile.open('r', encoding=UTF8) as file:
                    if self.notes:
                        self.notes = f'{self.notes}\n{file.read()}'  # append notes from file # has to run in main thread
                    else:
                        self.notes = file.read()  # append notes from file # has to run in main thread
            self.saveThread = Thread(target=self.saveScanParallel, args=(self.file,), name=f'{self.name} saveThread')
            self.saveThread.daemon = True  # Terminate with main app independent of stop condition
            self.saveThread.start()

    def saveScanParallel(self, file: Path) -> None:
        """Keep GUI interactive by saving scan data in external thread.

        :param file: The file to save to.
        :type file: pathlib.Path
        """
        # only reads data from gui but does not modify it -> can run in parallel thread
        self.settingsMgr.saveSettings(file=file)  # save settings
        self.saveData(file=file)  # save data to same file
        self.pluginManager.DeviceManager.exportConfiguration(file=file)  # save corresponding device settings in measurement file
        self.pluginManager.Settings.saveSettings(file=file)
        self.signalComm.saveScanCompleteSignal.emit()
        self.print(f'Saved {file.name}')

    def saveScanComplete(self) -> None:
        """Mark scan as finished after saving file completed."""
        if not self.pluginManager.closing:
            self.pluginManager.Explorer.populateTree()
            self.notes = ''  # reset after saved to last scan
        self.finished = True  # Main thread waits for this on closing. No new scan can be started before the previous one is finished

    @plotting
    def plot(self, update: bool = False, **kwargs) -> None:  # pylint: disable = unused-argument, missing-param-doc  # use **kwargs to allow child classed to extend the signature
        """Plot showing a current or final state of the scan.

        Extend to add scan specific plot code.
        Make sure to also use the @plotting decorator when overwriting this function.

        :param update: If update is True, only data will be updates, otherwise entire figure will be initialized, defaults to False
        :type update: bool, optional
        """

    def updateToolBar(self, update: bool) -> None:
        """Update toolbar if this is not just an update.

        :param update: Indicates if this us an update. Otherwise this is initializing of finalizing a plot.
        :type update: bool
        """
        if len(self.outputChannels) > 0 and not update and self.display and self.display.navToolBar and self.display.canvas:
            self.display.navToolBar.update()
            self.display.canvas.get_default_filename = lambda: self.file.with_suffix('.pdf').as_posix()  # set up save file dialog

    def updateRecording(self, recording: bool) -> None:
        """Set recording state. (Make sure this is called in main thread!).

        :param recording: New recording state.
        :type recording: bool
        """
        self.recording = recording

    def runScan(self, recording: Callable) -> None:  # noqa: C901, PLR0912
        """Step through input values, records output values, and triggers plot update.

        Executed in runThread. Will likely need to be adapted for custom scans.

        :param recording: Queries recording state.
        :type recording: Callable
        """
        steps = list(itertools.product(*[inputRecordingData for inputChannel in self.inputChannels if (inputRecordingData := inputChannel.getRecordingData()) is not None]))
        self.print(f'Starting scan M{self.pluginManager.Settings.measurementNumber:03}. Estimated time: {self.scantime}')
        for i, step in enumerate(steps):  # scan over all steps  # noqa: PLR1702
            waitLong = False
            for j, inputChannel in enumerate(self.inputChannels):
                if not waitLong and abs(inputChannel.value - step[j]) > self.largestep:
                    waitLong = True
                if inputChannel.updateValueSignal:
                    inputChannel.updateValueSignal.emit(step[j])
            if self.invalidWhileWaiting:
                for outputChannel in self.outputChannels:
                    if isinstance(outputChannel, ScanChannel):
                        outputChannel.signalComm.waitUntilStableSignal.emit(self.waitLong if waitLong else self.wait)
            time.sleep(((self.waitLong if waitLong else self.wait) + self.average) / 1000)  # if step is larger than threshold use longer wait time
            self.bufferLagging()
            self.waitForCondition(condition=lambda: self.stepProcessed, timeoutMessage='processing scan step.', timeout=10)
            for outputChannel in self.outputChannels:
                outputChannelDevice = outputChannel.getDevice()
                if isinstance(outputChannelDevice, Device):
                    if outputChannel.recording:
                        outputChannelValues = outputChannel.getValues(subtractBackground=outputChannelDevice.subtractBackgroundActive(), length=self.measurementsPerStep)
                    else:  # e.g. a virtual output channel that is not recording
                        outputChannelValues = outputChannel.value
                    if outputChannelValues is not None and outputChannel.recordingData is not None and isinstance(outputChannel, ScanChannel):
                        if not np.isnan(np.mean(outputChannelValues)):
                            if len(self.inputChannels) == 1:  # 1D scan
                                outputChannel.recordingData[i] = np.mean(outputChannelValues)
                            else:  # 2D scan, higher dimensions not jet supported
                                inputRecordingData1 = self.inputChannels[1].getRecordingData()
                                if inputRecordingData1 is not None:
                                    outputChannel.recordingData[i % len(inputRecordingData1), i // len(inputRecordingData1)] = np.mean(outputChannelValues)
                        else:
                            self.print('Ignoring nan value', flag=PRINT.DEBUG)
            if i == len(steps) - 1 or not recording():  # last step
                for inputChannel in self.inputChannels:
                    if inputChannel.updateValueSignal:
                        inputChannel.updateValueSignal.emit(inputChannel.initialValue)
                time.sleep(.5)  # allow time to reset to initial value before saving
                self.signalComm.scanUpdateSignal.emit(True)  # update graph and save data  # noqa: FBT003
                self.signalComm.updateRecordingSignal.emit(False)  # noqa: FBT003
                break  # in case this is last step
            self.stepProcessed = False
            self.signalComm.scanUpdateSignal.emit(False)  # update graph  # noqa: FBT003

    def close(self) -> bool:  # noqa: D102
        response = super().close()
        if self.recording:
            self.recording = False
        return response

    def closeGUI(self) -> None:  # noqa: D102
        self.toggleDisplay(visible=False)
        super().closeGUI()

    def toggleDisplay(self, visible: bool) -> None:
        """Toggles visibility of display.

        :param visible: Indicates if the display should be visible.
        :type visible: bool
        """
        if visible:
            if not self.display or not self.display.initializedDock:
                self.display = self.Display(scan=self, pluginManager=self.pluginManager)
                self.display.provideDock()
        elif self.displayActive() and self.display:
            self.display.closeGUI()

    def updateTheme(self) -> None:  # noqa: D102
        super().updateTheme()
        if self.displayActive() and self.display:
            self.display.updateTheme()
        if self.pluginManager.DeviceManager.titleBar:
            statusToolButton = self.pluginManager.DeviceManager.titleBar.widgetForAction(self.statusAction)
            if statusToolButton:
                statusToolButton.setStyleSheet(f"""QToolButton:checked{{background-color:{mix_hex_colors(colors.yellow, colors.bg, ratio=.7)};}}""")


class Browser(Plugin):
    """Display various file formats.

    In addition, it
    provides access to the program description and documentation. Finally, it shows
    the about content of other plugins when clicking on their respective question mark icons.
    """

    # Should show caret when mouse over selectable text but only shows pointer.

    name = 'Browser'
    version = '1.0'
    optional = False
    pluginType = PLUGINTYPE.DISPLAY
    iconFile = 'QWebEngine.png'

    def __init__(self, **kwargs) -> None:
        """Initialize a Browser."""
        super().__init__(**kwargs)
        self.previewFileTypes = ['.pdf', '.html', '.htm', '.svg', '.wav', '.mp3', '.ogg', '.mp4', '.avi']
        # '.mp4', '.avi' only work with codec https://doc.qt.io/qt-5/qtwebengine-features.html#audio-and-video-codecs
        self.previewFileTypes.extend(['.jpg', '.jpeg', '.png', '.bmp', '.gif'])
        web_engine_context_log = QLoggingCategory('qt.webenginecontext')
        web_engine_context_log.setFilterRules('*.info=false')
        self.ICON_BACK = self.makeCoreIcon('arrow-180.png')
        self.ICON_FORWARD = self.makeCoreIcon('arrow.png')
        self.ICON_RELOAD = self.makeCoreIcon('arrow-circle-315.png')
        self.ICON_STOP = self.makeCoreIcon('cross.png')
        self.ICON_HOME = self.makeCoreIcon('home.png')
        self.ICON_MANUAL = self.makeCoreIcon('address-book-open.png')
        self.file = Path()
        self.title = None
        self.html = None
        self.plugin = None

    def initDock(self) -> None:  # noqa: D102
        super().initDock()
        if self.dock:
            self.dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)  # not floatable or movable

    def initGUI(self) -> None:  # noqa: D102
        super().initGUI()
        self.webEngineView = QWebEngineView(parent=self.app.mainWindow)
        settings = self.webEngineView.settings()
        if settings:
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)  # noqa: FBT003
            settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)  # required to open local PDFs  # noqa: FBT003

        self.webEngineView.loadFinished.connect(self.adjustLocation)

        if not self.titleBar:
            return
        self.titleBar.setIconSize(QSize(16, 16))  # match size of other titleBar elements
        self.backAction = cast('Action', self.webEngineView.pageAction(QWebEnginePage.WebAction.Back))
        if self.backAction:
            self.backAction.setIcon(self.ICON_BACK)
            self.backAction.setObjectName('backAction')
            self.backAction.fileName = self.ICON_BACK.fileName
        self.titleBar.addAction(self.backAction)
        self.forwardAction = cast('Action', self.webEngineView.pageAction(QWebEnginePage.WebAction.Forward))
        if self.forwardAction:
            self.forwardAction.setIcon(self.ICON_FORWARD)
            self.forwardAction.setObjectName('forwardAction')
            self.forwardAction.fileName = self.ICON_FORWARD.fileName
        self.titleBar.addAction(self.forwardAction)
        self.reloadAction = cast('Action', self.webEngineView.pageAction(QWebEnginePage.WebAction.Reload))
        if self.reloadAction:
            self.reloadAction.setIcon(self.ICON_RELOAD)
            self.reloadAction.setObjectName('reloadAction')
            self.reloadAction.fileName = self.ICON_RELOAD.fileName
        self.titleBar.addAction(self.reloadAction)
        self.stopAction = cast('Action', self.webEngineView.pageAction(QWebEnginePage.WebAction.Stop))
        if self.stopAction:
            self.stopAction.setIcon(self.ICON_STOP)
            self.stopAction.setObjectName('stopAction')
            self.stopAction.fileName = self.ICON_STOP.fileName
        self.titleBar.addAction(self.stopAction)
        self.homeAction = self.addAction(event=self.openAbout, toolTip='Home', icon=self.ICON_HOME)
        self.locationEdit = QLineEdit()
        self.locationEdit.setSizePolicy(QSizePolicy.Policy.Expanding, self.locationEdit.sizePolicy().verticalPolicy())
        self.locationEdit.returnPressed.connect(self.loadUrl)
        self.locationEdit.setMaximumHeight(QPushButton().sizeHint().height())
        self.titleBar.addWidget(self.locationEdit)
        self.docAction = self.addAction(event=self.openDocumentation, toolTip='Offline Documentation', icon=self.ICON_MANUAL)
        self.addContentWidget(self.webEngineView)

    def finalizeInit(self) -> None:  # noqa: D102
        super().finalizeInit()
        self.floatAction.deleteLater()
        del self.floatAction
        self.stretch.deleteLater()
        self.openAbout()

    def runTestParallel(self) -> None:  # noqa: D102
        if self.initializedDock:
            self.testControl(self.docAction, value=True, delay=.5)
            self.testControl(self.homeAction, value=True, delay=.5)
            if self.backAction:
                self.testControl(self.backAction, value=True, delay=.5)
            if self.forwardAction:
                self.testControl(self.forwardAction, value=True, delay=.5)
            if self.stopAction:
                self.testControl(self.stopAction, value=True, delay=.5)
            if self.reloadAction:
                self.testControl(self.reloadAction, value=True, delay=.5)
        super().runTestParallel()

    def loadData(self, file: Path, showPlugin: bool = True) -> None:  # noqa: D102
        self.provideDock()
        self.file = file
        # overwrite parent
        if any(file.name.endswith(fileType) for fileType in ['.html', '.htm']):
            self.webEngineView.load(QUrl.fromLocalFile(file.as_posix()))
        elif any(file.name.endswith(fileType) for fileType in ['.mp4', '.avi']):
            self.webEngineView.setHtml('Note: .mp4 and .avi files are not supported due to licensing limitations as '
                                       'explained <a href="https://doc.qt.io/qt-5/qtwebengine-features.html#audio-and-video-codecs">here</a>.\nPlease open in external program.')
        elif file.name.endswith('.svg'):
            self.webEngineView.setHtml(f'<img src={file.name.replace(" ", "%20")} width="100%"/>',
                baseUrl=QUrl.fromLocalFile(file.as_posix().replace(' ', '%20')))
        else:  # if file.name.endswith('.pdf', ...):
            self.webEngineView.setUrl(QUrl(f'file:///{file.as_posix()}'))
            # self.webEngineView.??? how to collapse thumbnail / Document outline after loading pdf?
        self.raiseDock(showPlugin)

    def loadUrl(self) -> None:
        """Load content from an URL."""
        self.webEngineView.load(QUrl.fromUserInput(self.locationEdit.text()))

    def adjustLocation(self) -> None:
        """Adjust text in address bar after loading completed."""
        if self.title:
            self.locationEdit.setText(self.title)
            self.title = None  # reset for next update
        else:
            self.locationEdit.setText(self.webEngineView.url().toString().replace('%5C', '/'))
            self.html = None
            self.plugin = None

    def openDocumentation(self) -> None:
        """Display the offline documentation."""
        self.title = 'Offline Documentation'
        self.loadData(file=(Path(__file__).parent / 'docs/index.html').resolve())

    def openAbout(self) -> None:
        """Display program purpose, version, and creators."""
        updateHTML = '(Offline)'
        try:
            response = requests.get('https://github.com/ioneater/ESIBD-Explorer/releases/latest', timeout=5)
            onlineVersion = version.parse(response.url.split('/').pop())
            if onlineVersion == PROGRAM_VERSION:
                updateHTML = '(<span style="color: green">Up to date!</span>)'
            elif onlineVersion < PROGRAM_VERSION:
                updateHTML = '(<span style="color: orange">Unreleased Version!</span>)'
            elif onlineVersion > PROGRAM_VERSION:
                updateHTML = f'(<a href="https://github.com/ioneater/ESIBD-Explorer/releases/latest">Version {onlineVersion.base_version} available!</a>)'
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout, InvalidVersion):
            pass
        self.setHtml(title=f'About {PROGRAM_NAME}.', html=f"""
        <h1><img src='{PROGRAM_ICON.resolve()}' width='22'> {PROGRAM_NAME} {PROGRAM_VERSION} {updateHTML}</h1>{ABOUTHTML}""")

    def setHtml(self, title: str, html: str) -> None:
        """Display HTML content.

        :param title: Title displayed in address bar
        :type title: str
        :param html: Valid HTML code.
        :type html: str
        """
        self.provideDock()
        self.html = html
        self.title = title
        self.webEngineView.setHtml(self.htmlStyle() + self.html, baseUrl=QUrl.fromLocalFile(Path().cwd().as_posix().replace(' ', '%20')))  # baseURL required to access local files
        self.raiseDock(showPlugin=True)

    def setAbout(self, plugin: Plugin, title: str, html: str) -> None:
        """Create and display the about dialog for a given plugin.

        :param plugin: Plugin for which the dialog should be shown
        :type plugin: :class:`~esibd.plugins.Plugin`
        :param title: Title displayed in address bar
        :type title: str
        :param html: Plugin specific HTML content of about page.
        :type html: str
        """
        self.provideDock()
        self.title = title
        self.html = html
        self.plugin = plugin
        # baseURL required to access local files
        self.webEngineView.setHtml(self.htmlStyle() + self.htmlTitle(self.plugin) + self.html, baseUrl=QUrl.fromLocalFile(Path.cwd().as_posix().replace(' ', '%20')))
        self.raiseDock(showPlugin=True)

    def htmlStyle(self) -> str:
        """Style definitions for HTML content."""
        return f"""
        <style>
        body {{
          background-color: {colors.bg};
          color: {colors.fg};
        }}
        a:link    {{color:  #8ab4f8; background-color: transparent; text-decoration: none; }}
        a:visited {{color:  #c58af9; background-color: transparent; text-decoration: none; }}
        a:hover   {{color:  #8ab4f8; background-color: transparent; text-decoration: underline; }}
        a:active  {{color:  #8ab4f8; background-color: transparent; text-decoration: underline; }}
        </style>"""

    def htmlTitle(self, plugin: Plugin) -> str:
        """Title line for about page in HTML.

        :param plugin: The plugin for which the title line is requested.
        :type plugin: esibd.plugins.Plugin
        :return: HTML title line.
        :rtype: str
        """
        return f"<h1><img src='{Path(plugin.getIcon().fileName).resolve()}' width='22'> {plugin.name} {plugin.version}</h1>"

    def updateTheme(self) -> None:  # noqa: D102
        super().updateTheme()
        if self.html and self.title:
            if self.plugin:
                self.setAbout(self.plugin, self.title, self.html)
            else:
                self.setHtml(self.title, self.html)


class Text(Plugin):
    """May contain additional useful representation of files, even if they are handled by other plugins.

    In addition, it may contain
    information such as change logs after loading settings or
    configurations from file. It also allows to edit and save simple text files.
    """

    name = 'Text'
    version = '1.0'
    optional = False
    pluginType = PLUGINTYPE.DISPLAY
    iconFile = 'text.png'
    iconFileDark = 'text_dark.png'

    signalComm: 'SignalCommunicate'

    class SignalCommunicate(Plugin.SignalCommunicate):
        """Bundle pyqtSignals."""

        setTextSignal = pyqtSignal(str, bool)

    def __init__(self, **kwargs) -> None:  # noqa: D107
        super().__init__(**kwargs)
        self.previewFileTypes = ['.txt', '.dat', '.ter', '.cur', '.tt', '.log', '.py', '.star', '.pdb1', '.css', '.js', '.html', '.tex', '.ini', '.bat']
        self.signalComm.setTextSignal.connect(self.setText)
        self.lineLimit = 10000

    def initGUI(self) -> None:  # noqa: D102
        super().initGUI()
        self.editor = TextEdit()
        self.editor.setFont(QFont('Courier', 10))
        self.numbers = NumberBar(parent=self.editor)
        lay = QHBoxLayout()
        lay.addWidget(self.numbers)
        lay.addWidget(self.editor)
        self.addContentLayout(lay)

    def provideDock(self) -> bool:  # noqa: D102
        if super().provideDock():
            self.finalizeInit()
            self.afterFinalizeInit()
            return True
        return False

    def finalizeInit(self) -> None:  # noqa: D102
        super().finalizeInit()
        self.addAction(event=self.saveFile, toolTip='Save', icon=self.makeCoreIcon('disk-black.png'), before=self.aboutAction)
        self.wordWrapAction = self.addStateAction(event=self.toggleWordWrap, toolTipFalse='Word wrap on.', iconFalse=self.makeCoreIcon('ui-scroll-pane-text.png'),
                                                  toolTipTrue='Word wrap off.', before=self.aboutAction, attr='wordWrap')
        self.textClipboardAction = self.addAction(event=self.copyTextClipboard,
                       toolTip='Copy text to clipboard.', icon=self.makeCoreIcon('clipboard-paste-document-text.png'), before=self.aboutAction)
        self.toggleWordWrap()

    def runTestParallel(self) -> None:  # noqa: D102
        if self.initializedDock:
            self.testControl(self.wordWrapAction, value=True)
            self.testControl(self.textClipboardAction, value=True)
        super().runTestParallel()

    def updateTheme(self) -> None:  # noqa: D102
        super().updateTheme()
        if not self.pluginManager.loading:
            self.numbers.updateTheme()

    def saveFile(self) -> None:
        """Save text file containing current text."""
        file = None
        if self.pluginManager.Explorer.activeFileFullPath:
            file = Path(QFileDialog.getSaveFileName(parent=None, caption=SELECTFILE, directory=self.pluginManager.Explorer.activeFileFullPath.as_posix())[0])
        else:
            file = Path(QFileDialog.getSaveFileName(parent=None, caption=SELECTFILE)[0])
        if file != Path():
            file.write_text(self.editor.toPlainText(), encoding=self.UTF8)
            self.pluginManager.Explorer.populateTree()

    def loadData(self, file: Path, showPlugin: bool = True) -> None:  # noqa: D102
        self.provideDock()
        self.editor.clear()
        if any(file.name.endswith(fileType) for fileType in self.previewFileTypes):
            try:
                with file.open(encoding=self.UTF8) as dataFile:
                    for i, line in enumerate(islice(dataFile, self.lineLimit)):  # dont use f.read() as files could potentially be very large
                        self.editor.insertPlainText(line)  # always populate text box but only change to tab if no other display method is available
                        if i == self.lineLimit - 1:
                            self.print(f'Limited display to {self.lineLimit} lines.', flag=PRINT.WARNING)
            except UnicodeDecodeError as e:
                self.print(f'Cant read file: {e}')
        verticalScrollBar = self.editor.verticalScrollBar()
        if verticalScrollBar:
            verticalScrollBar.triggerAction(QScrollBar.SliderAction.SliderToMinimum)   # scroll to top
        self.raiseDock(showPlugin)

    def setText(self, text: str, showPlugin: bool = False, append: bool = False) -> None:
        """Set the displayed text.

        :param text: Text to be shown.
        :type text: str
        :param showPlugin: Show plugin after setting text, defaults to False. Some files are handled by multiple plugins and only one of them should be shown by default.
        :type showPlugin: bool, optional
        :param append: Indicates if text should be appended to or overwrite previous content, defaults to False.
        :type append: bool, optional
        """
        self.provideDock()
        if append:
            self.editor.appendPlainText(text)
        else:
            self.editor.setPlainText(text)
        tc = self.editor.textCursor()
        tc.setPosition(0)
        self.editor.setTextCursor(tc)
        self.raiseDock(showPlugin)

    def setTextParallel(self, text: str, showPlugin: bool = False) -> None:
        """Set the displayed text (thread save).

        :param text: Text to be shown.
        :type text: str
        :param showPlugin: Show plugin after setting Text, defaults to False. Some files are handled by multiple plugins and only one of them should be shown by default.
        :type showPlugin: bool, optional
        """
        self.signalComm.setTextSignal.emit(text, showPlugin)

    def inspect(self, obj: Any, search_term: str = '') -> None:  # noqa: ANN401
        """Display a simple overview of all object attributes and methods.

        :param obj: A valid object.
        :type obj: Any
        :param search_term: Results will be filtered based on this, defaults to None
        :type search_term: str, optional
        """
        member_list = []
        member_list = [repr(member) for member in inspect.getmembers(obj) if search_term in repr(member)] if search_term else [repr(member) for member in inspect.getmembers(obj)]
        self.setText('\n'.join(member_list), append=True)

    @synchronized()
    def copyTextClipboard(self) -> None:
        """Copy current text to clipboard."""
        clipboard = QApplication.clipboard()
        if not self.testing and clipboard:
            clipboard.setText(self.editor.toPlainText())

    @synchronized()
    def toggleWordWrap(self) -> None:
        """Toggles use of word wrap."""
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth if self.wordWrapAction.state else QPlainTextEdit.LineWrapMode.NoWrap)


class Tree(Plugin):
    """Give an overview of the content of .py, .hdf5, and .h5 files.

    This includes configuration or scan files and even python source code.
    It can also help inspect any object using Tree.inspect() or give an overview of icons using Tree.iconOverview() from the :ref:`sec:console`.
    """

    documentation = """The Tree plugin gives an overview of the content of .py, .hdf5, and
    .h5 files. This includes configuration or scan files and even python source code.
    It can also help inspect any object using Tree.inspect() or give an overview of icons using Tree.iconOverview() from the Console.
    """

    name = 'Tree'
    version = '1.0'
    optional = False
    pluginType = PLUGINTYPE.DISPLAY
    iconFile = 'tree.png'

    def __init__(self, **kwargs) -> None:  # noqa: D107
        super().__init__(**kwargs)
        self.h5PreviewFileTypes = ['.hdf5', '.h5']
        self.pyPreviewFileTypes = ['.py']
        self.previewFileTypes = self.h5PreviewFileTypes + self.pyPreviewFileTypes
        self.ICON_ATTRIBUTE = str(self.dependencyPath / 'blue-document-attribute.png')
        self.ICON_DATASET = str(self.dependencyPath / 'database-medium.png')
        self.ICON_FUNCTIONMETHOD = str(self.dependencyPath / 'block-small.png')
        self.ICON_CLASS = str(self.dependencyPath / 'application-block.png')
        self.ICON_GROUP = str(self.dependencyPath / 'folder.png')
        self._inspect = False

    def initGUI(self) -> None:  # noqa: D102
        super().initGUI()
        self.tree = TreeWidget()
        self.addContentWidget(self.tree)
        self.tree.itemExpanded.connect(self.expandObject)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.initContextMenu)

    def finalizeInit(self) -> None:  # noqa: D102
        super().finalizeInit()
        self.filterLineEdit = QLineEdit()
        self.filterLineEdit.setMaximumWidth(100)
        self.filterLineEdit.setMinimumWidth(50)
        self.filterLineEdit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.filterLineEdit.textChanged.connect(lambda: self.filterTree(parentItem=None))
        self.filterLineEdit.setPlaceholderText('Search')
        if self.titleBar:
            self.titleBar.insertWidget(self.aboutAction, self.filterLineEdit)

    def provideDock(self) -> bool:  # noqa: D102
        if super().provideDock():
            self.finalizeInit()
            self.afterFinalizeInit()
            return True
        return False

    def loadData(self, file: Path, showPlugin: bool = True) -> None:  # noqa: D102
        self.provideDock()
        self.tree.clear()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnWidth(0, max(self.tree.columnWidth(0), 200))
        self._inspect = False
        if any(file.name.endswith(fileType) for fileType in self.h5PreviewFileTypes):
            invisibleRootItem = self.tree.invisibleRootItem()
            if invisibleRootItem:
                with h5py.File(file, 'r', track_order=True) as dataFile:
                    self.hdfShow(dataFile, invisibleRootItem, 0)
        else:  # self.pyPreviewFileTypes
            # """from https://stackoverflow.com/questions/44698193/how-to-get-a-list-of-classes-and-functions-from-a-python-file-without-importing/67840804#67840804"""
            try:
                with file.open(encoding=self.UTF8) as file_obj:
                    node = ast.parse(file_obj.read())
            except SyntaxError as e:
                self.print(f'Could not parse syntax of file {file.name}: {e}', flag=PRINT.ERROR)
                return
            functions = [node for node in node.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
            classes = [node for node in node.body if isinstance(node, ast.ClassDef)]
            for function in functions:
                function_widget = QTreeWidgetItem(self.tree, [function.name])
                function_widget.setIcon(0, QIcon(self.ICON_FUNCTIONMETHOD))
                function_widget.setToolTip(0, ast.get_docstring(function))
                function_widget.setExpanded(True)
            for ast_class in classes:
                self.pyShow(ast_class, self.tree, 0)
        if self.tree.topLevelItemCount() == 0:  # show text if no items found
            self.pluginManager.Text.provideDock()
            self.pluginManager.Text.raiseDock(showPlugin)
        else:
            self.filterTree()
            self.raiseDock(showPlugin)

    def filterTree(self, parentItem: 'QTreeWidgetItem | None' = None) -> bool:
        """Filter content based on filterLineEdit.text.

        :param parentItem: The item to be filtered, defaults to None
        :type parentItem: QTreeWidgetItem, optional
        :return: True if widget is visible
        :rtype: bool
        """
        if not parentItem:
            parentItem = self.tree.invisibleRootItem()
        if not parentItem:
            return False
        children = [parentItem.child(i) for i in range(parentItem.childCount())]
        show = (not self.filterLineEdit.text() or self.filterLineEdit.text().lower() in parentItem.text(0).lower() or
                 self.filterLineEdit.text().lower() in parentItem.text(1).lower())
        for item in children:
            if self.filterTree(item):
                show = True  # show if filter matches at least one child
        parentItem.setHidden(not show)
        return show

    def hdfShow(self, hdfItem: h5py.Group | h5py.File, tree: QTreeWidgetItem, expansionLevel: int) -> None:
        """Populate tree based on contents of a hdf5 file.

        :param hdfItem: HDF Group
        :type hdfItem: h5py.Group | h5py.Dataset | h5py.File, ...
        :param tree: The tree used to display the file contents.
        :type tree: QTreeWidgetItem
        :param expansionLevel: Only expand items up to this level.
        :type expansionLevel: int
        """
        for name, item in hdfItem.items():
            if isinstance(item, h5py.Group):
                groupItem = QTreeWidgetItem(tree, [name])
                groupItem.setIcon(0, QIcon(self.ICON_GROUP))
                if expansionLevel < 1:
                    groupItem.setExpanded(True)
                for attribute, value in item.attrs.items():
                    attribute_str = f'{attribute}: {value}'
                    attribute_widget = QTreeWidgetItem(groupItem, [attribute_str.split('\n', maxsplit=1)[0]])
                    attribute_widget.setIcon(0, QIcon(self.ICON_ATTRIBUTE))
                    attribute_widget.setToolTip(0, attribute_str)
                self.hdfShow(item, groupItem, expansionLevel + 1)
            elif isinstance(item, h5py.Dataset):
                dataset_widget = QTreeWidgetItem(tree, [name])
                dataset_widget.setIcon(0, QIcon(self.ICON_DATASET))
                dataset_widget.setToolTip(0, self.get_hdf5_preview(item))

    def get_hdf5_preview(self, dataset: h5py.Dataset, max_elements: int = 10) -> str:
        """Return a short string preview of an HDF5 dataset.

        :param dataset: h5py.Dataset object
        :param max_elements: Max number of elements to preview
        :return: String preview
        """
        try:
            shape = dataset.shape
            dtype = dataset.dtype

            # Scalar
            if shape == ():
                value = dataset[()]
                return f'{value} ({dtype})'

            # Strings
            if dtype.kind in {'S', 'O', 'U'}:
                preview = dataset[:max_elements]
                preview = [s.decode('utf-8', errors='replace') for s in preview] if dtype.kind == 'S' else [str(s) for s in preview]
                preview_str = ', '.join(str(s) for s in preview)
                if shape[0] > max_elements:
                    preview_str += ', ...'
                return f'[{preview_str}] ({dtype}, shape={shape})'

            # Multidimensional arrays
            preview_shape = list(shape)
            preview_shape[0] = min(preview_shape[0], max_elements)

            slicing = tuple(slice(0, s) for s in preview_shape)
            data = dataset[slicing]

            # Use numpy formatting
            data_preview = np.array2string(data, threshold=max_elements, edgeitems=2, max_line_width=60)

        except Exception as e:  # noqa: BLE001
            return f'<Error reading data: {e}>'
        else:
            return f'{data_preview} ({dtype}, shape={shape})'

    def pyShow(self, class_obj: Any, tree: 'QTreeWidget | QTreeWidgetItem', expansionLevel: int) -> None:  # noqa: ANN401
        """Populate tree based on contents of a python class.

        :param class_obj: Valid python class.
        :type class_obj: Any
        :param tree: The tree used to display the attributes and methods if the class.
        :type tree: QTreeWidgetItem
        :param expansionLevel: Only expand items up to this level.
        :type expansionLevel: int
        """
        class_widget = QTreeWidgetItem(tree, [class_obj.name])
        class_widget.setIcon(0, QIcon(self.ICON_CLASS))
        class_widget.setToolTip(0, ast.get_docstring(class_obj))
        if expansionLevel < 1:
            class_widget.setExpanded(True)
        for ast_class_obj in [node for node in class_obj.body if isinstance(node, ast.ClassDef)]:
            self.pyShow(ast_class_obj, class_widget, expansionLevel + 1)
        for method in [node for node in class_obj.body if isinstance(node, ast.FunctionDef)]:
            method_widget = QTreeWidgetItem(class_widget, [method.name])
            method_widget.setIcon(0, QIcon(self.ICON_FUNCTIONMETHOD))
            method_widget.setToolTip(0, ast.get_docstring(method))

    def inspect(self, obj: Any | list) -> None:  # noqa: ANN401
        """Show a list of attributes of the object and applies a filter if provided.

        :param obj: Any python class or List
        :type obj: class | list
        """
        self.provideDock()
        self._inspect = True
        self.tree.clear()
        self.tree.setHeaderHidden(False)
        self.tree.setHeaderLabels(['Object', 'Value'])
        self.tree.setColumnCount(2)
        self.tree.setColumnWidth(0, 200)
        header = self.tree.header()
        if header:
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.setUpdatesEnabled(False)
        invisibleRootItem = self.tree.invisibleRootItem()
        if invisibleRootItem:
            self.inspect_recursive(tree=invisibleRootItem, obj=obj)
        self.filterTree()
        self.tree.setUpdatesEnabled(True)

    def expandObject(self, item: TreeWidgetItem) -> None:
        """Populate and expands element of inspected class.

        :param item: The item representing the object to be expanded.
        :type item: TreeWidgetItem
        """
        if self._inspect:
            self.tree.setUpdatesEnabled(False)
            self.inspect_recursive(tree=item, obj=item.obj)
            self.tree.setUpdatesEnabled(True)
            item.setExpanded(True)

    RECURSION_DEPTH_DEFAULT = 2

    def inspect_recursive(self, tree: QTreeWidgetItem, obj: Any, recursionDepth: int = RECURSION_DEPTH_DEFAULT) -> None:  # noqa: ANN401, C901, PLR0912, PLR0915
        """Recursively populate the tree with the objects attributes and methods.

        Will also be called as user expands items.
        Similar logic is used for Explorer, but here we do not need to worry about changing filters or items that have been removed.

        :param tree: The tree used to display the attributes and methods.
        :type tree: QTreeWidgetItem
        :param obj: Valid python object.
        :type obj: Any
        :param recursionDepth: How many levels will be populated. Further levels will be populated as they are expanded. Defaults to 2
        :type recursionDepth: int, optional
        """
        if recursionDepth == 0:
            return
        recursionDepth -= 1
        children_text = [child.text(0) if child is not None else '' for i in range(tree.childCount()) for child in [tree.child(i)]]  # list of existing children
        if isinstance(obj, list):
            list_preview_number = 5  # only show subset of list
            for i, element in enumerate(obj[:list_preview_number]):
                element_name = f'[{i}]'
                if element_name in children_text:  # reuse existing
                    element_widget = tree.child(children_text.index(element_name))
                else:  # add new
                    element_widget = TreeWidgetItem(tree, [element_name])
                    element_widget.setIcon(0, QIcon(self.ICON_ATTRIBUTE))
                    element_widget.setText(1, f'{element}')
                    element_widget.obj = element
                if not isinstance(element, (bool, float, int, str, Enum)) and element_widget:
                    self.inspect_recursive(tree=element_widget, obj=element, recursionDepth=recursionDepth)
            if len(obj) > list_preview_number and '...' not in children_text:
                _ = QTreeWidgetItem(tree, ['...'])
                _.setIcon(0, QIcon(self.ICON_ATTRIBUTE))
        else:
            object_names = [object_name for object_name in dir(obj) if not object_name.startswith('_')]
            variable_names = []
            callable_names = []
            for object_name in object_names:
                try:
                    attr = getattr(obj, object_name)
                    if attr is not None:
                        if callable(attr):
                            callable_names.append(object_name)
                        else:
                            variable_names.append(object_name)
                except AttributeError as e:
                    # apparently some libraries keep deprecated attributes, just to throw deprecation AttributeError if they are accessed.
                    self.print(f'Problem with object {object_name}: {e}', flag=PRINT.VERBOSE)
                except (ValueError, RuntimeError) as e:
                    self.print(f'Problem with object {object_name}: {e}', flag=PRINT.WARNING)
            for object_name in variable_names:
                attr = getattr(obj, object_name)
                variable_name = object_name if isinstance(attr, list) else f'{object_name}'
                if variable_name in children_text:
                    variable_widget = tree.child(children_text.index(variable_name))
                else:
                    variable_widget = TreeWidgetItem(tree, [variable_name])
                    variable_widget.setIcon(0, QIcon(self.ICON_ATTRIBUTE))
                    if not isinstance(attr, list):
                        variable_widget.setText(1, repr(attr))
                        #  # attribute docstrings not yet supported https://discuss.python.org/t/revisiting-attribute-docstrings/36413/33
                        # variable_widget.setToolTip
                    variable_widget.obj = attr
                if not isinstance(attr, (bool, float, int, str, Enum)) and variable_widget:
                    self.inspect_recursive(tree=variable_widget, obj=attr, recursionDepth=recursionDepth)
            for object_name in callable_names:
                if object_name in children_text:
                    class_method_widget = tree.child(children_text.index(object_name))
                else:
                    attr = getattr(obj, object_name)
                    class_method_widget = QTreeWidgetItem(tree, [object_name])
                    class_method_widget.setIcon(0, QIcon(self.ICON_CLASS if inspect.isclass(attr) else self.ICON_FUNCTIONMETHOD))
                    doc = inspect.getdoc(attr)
                    if doc:
                        class_method_widget.setText(1, doc.split('\n')[0])
                        class_method_widget.setToolTip(1, doc)
        if recursionDepth == self.RECURSION_DEPTH_DEFAULT:  # only once on top level
            self.filterTree()
        self.raiseDock(showPlugin=True)

    def initContextMenuBase(self, item: QTreeWidgetItem, pos: QPoint) -> None:
        """Choose relevant context menu actions based on the type and properties of the Parameter.

        :param item: The item for which the context menu is requested.
        :type item: QTreeWidgetItem
        :param pos: The position of the context menu.
        :type pos: QPoint
        """
        contextMenu = QMenu(self.tree)
        consoleAction = None
        if getDebugMode():
            consoleAction = contextMenu.addAction('Add item to Console')
        copyClipboardAction = contextMenu.addAction('Copy to clipboard')
        contextMenu = contextMenu.exec(self.tree.mapToGlobal(pos))
        if contextMenu:
            if contextMenu is copyClipboardAction:
                column_index = -1
                header = self.tree.header()
                if header:
                    for col in range(self.tree.columnCount()):
                        rect = header.sectionViewportPosition(col)
                        if rect <= pos.x() < rect + header.sectionSize(col):
                            column_index = col
                            break
                    if column_index != -1:
                        clipboard = QApplication.clipboard()
                        if clipboard:
                            clipboard.setText(item.text(column_index))
            elif contextMenu is consoleAction:
                self.pluginManager.Console.addToNamespace('item', item)
                self.pluginManager.Console.execute(command='item')

    def initContextMenu(self, pos: QPoint) -> None:
        """Initialize context menu for an item.

        :param pos: The position of the context menu.
        :type pos: QPoint
        """
        try:
            item = self.tree.itemAt(pos)
            if item:
                self.initContextMenuBase(item, pos)
        except KeyError as e:
            self.print(str(e))

    def iconOverview(self) -> None:
        """Show actions from toolbar of all plugins."""
        self.provideDock()
        self._inspect = False
        self.tree.clear()
        self.tree.setHeaderHidden(False)
        self.tree.setHeaderLabels(['Icon', 'Tooltip'])
        self.tree.setColumnCount(2)
        self.tree.setColumnWidth(0, max(self.tree.columnWidth(0), 200))
        header = self.tree.header()
        if header:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for plugin in self.pluginManager.plugins:
            plugin_widget = QTreeWidgetItem(self.tree, [plugin.name])
            plugin_widget.setIcon(0, plugin.getIcon())
            self.tree.setFirstColumnSpanned(self.tree.indexOfTopLevelItem(plugin_widget), self.tree.rootIndex(), True)  # Spans all columns  # noqa: FBT003
            self.addActionWidgets(plugin_widget, plugin)
            if isinstance(plugin, ChannelManager):
                if plugin.liveDisplayActive() and plugin.liveDisplay:
                    widget = QTreeWidgetItem(plugin_widget, [plugin.liveDisplay.name])
                    widget.setIcon(0, plugin.liveDisplay.getIcon())
                    widget.setFirstColumnSpanned(True)
                    self.addActionWidgets(widget, plugin.liveDisplay)
                if plugin.staticDisplayActive() and plugin.staticDisplay:
                    widget = QTreeWidgetItem(plugin_widget, [plugin.staticDisplay.name])
                    widget.setIcon(0, plugin.staticDisplay.getIcon())
                    widget.setFirstColumnSpanned(True)
                    self.addActionWidgets(widget, plugin.staticDisplay)
                if plugin.channelPlotActive() and plugin.channelPlot:
                    widget = QTreeWidgetItem(plugin_widget, [plugin.channelPlot.name])
                    widget.setIcon(0, plugin.channelPlot.getIcon())
                    widget.setFirstColumnSpanned(True)
                    self.addActionWidgets(widget, plugin.channelPlot)
            if plugin.displayActive() and plugin.display:
                widget = QTreeWidgetItem(plugin_widget, [plugin.display.name])
                widget.setIcon(0, plugin.display.getIcon())
                widget.setFirstColumnSpanned(True)
                self.addActionWidgets(widget, plugin.display)
            plugin_widget.setExpanded(True)

        self.filterTree()
        self.raiseDock(showPlugin=True)

    def addActionWidgets(self, tree: QTreeWidgetItem, plugin: Plugin) -> None:
        """Add items representing actions.

        :param tree: The tree used to display the items.
        :type tree: QTreeWidgetItem
        :param plugin: The plugin for which actions should be displayed.
        :type plugin: esibd.plugins.Plugin
        """
        if hasattr(plugin, 'titleBar') and plugin.titleBar:
            for action in plugin.titleBar.actions():
                if action.iconText() and action.isVisible():
                    action_widget = QTreeWidgetItem(tree)
                    action_widget.setIcon(0, action.getIcon() if isinstance(action, (Action, StateAction, MultiStateAction)) else action.icon())
                    action_widget.setText(1, action.iconText())


class Console(Plugin):
    # ! Might need to switch to to more stable QtConsole eventually
    """The console should typically not be needed, unless you are a developer or assist in debugging an issue.

    It is activated from the tool bar of
    the :ref:`sec:settings`. Status messages will be logged here. In addition you can
    also enable writing status messages to a log file, that can be shared
    with a developer for debugging. All features implemented in the user
    interface and more can be accessed directly from this console. Use at
    your own Risk! You can select some commonly used examples directly from
    the combo box to get started.
    """

    documentation = """The console should typically not be needed, unless you are a developer
    or assist in debugging an issue. It is activated from the tool bar of
    the settings. Status messages will be logged here. In addition you can
    also enable writing status messages to a log file, that can be shared
    with a developer for debugging. All features implemented in the user
    interface and more can be accessed directly from this console. Use at
    your own Risk! You can select some commonly used examples directly from
    the combo box to get started.
    """

    pluginType = PLUGINTYPE.CONSOLE
    name = 'Console'
    version = '1.0'
    optional = False
    iconFile = 'terminal.png'

    signalComm: 'SignalCommunicate'

    class SignalCommunicate(Plugin.SignalCommunicate):
        """Bundle pyqtSignals."""

        writeSignal = pyqtSignal(str)
        executeSignal = pyqtSignal(str)
        executeSilentSignal = pyqtSignal(str)

    def initDock(self) -> None:  # noqa: D102
        super().initDock()
        if self.dock:
            self.dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)  # not floatable or movable

    def initGUI(self) -> None:  # noqa: D102
        super().initGUI()
        self.mainDisplayWidget.setMinimumHeight(1)  # enable hiding
        validConfigPath = getValidConfigPath()
        if validConfigPath:
            self.historyFile = validConfigPath / 'console_history.bin'
            self.mainConsole = ThemedConsole(parentPlugin=self, historyFile=self.historyFile)
            self.mainConsole.repl._lastCommandRow = 0  # not checking for None if uninitialized! -> initialize  # noqa: SLF001
            self.vertLayout.addWidget(self.mainConsole, 1)  # https://github.com/pyqtgraph/pyqtgraph/issues/404  # add before hintsTextEdit
            self.commonCommandsComboBox = CompactComboBox()
            self.commonCommandsComboBox.wheelEvent = lambda *_, **__: None
            self.commonCommandsComboBox.setToolTip('Examples and commonly used commands.')
            self.commonCommandsComboBox.addItems([
                'select command',
                'Tree.iconOverview()  # Show icon overview.',
                'Browser.previewFileTypes  # access plugin properties directly using plugin name',
                'ISEG.controller  # get device specific hardware manager',
                'RBD.channels  # get channels of a device',
                'Energy.display.fig  # get specific figure',
                'inspect.getdoc(Settings)  # get documentation of an object',
                'Tree.inspect(Settings)  # show methods and attributes of any object in Tree plugin',
                "timeit.timeit('Beam.plot(update=True, done=False)', number=100, globals=globals())  # time execution of plotting",
                "channel = DeviceManager.getChannelByName('RT_Front-Plate', inout=INOUT.IN)  # get specific input channel",
                'channel.asDict(includeTempParameters=True)  # Returns list of channel parameters and their value.',
                'channel.getParameterByName(channel.ENABLED).getWidget().height()  # get property of specific channel',
                'parameter = channel.getParameterByName(channel.ENABLED)  # get specific channel parameter',
                'print(parameter.parameterType, parameter.value, parameter.getWidget())  # print parameter properties',
                "channel.getParameterByName(channel.VALUE).getWidget().setStyleSheet('background-color:red;')  # test widget styling",
                "_=[parameter.getWidget().setStyleSheet('background-color:red;border: 0px;padding: 0px;margin: 0px;') for parameter in channel.parameters]",
                'PluginManager.showThreads()  # show all active threads',
                '[plt.figure(num).get_label() for num in plt.get_fignums()]  # show all active matplotlib figures',
                '# self.closeApplication(restart=True, confirm=False) # restart the application cleanly (uses new code if changed)',
                "# Module=dynamicImport('ModuleName', 'C:/path/to/module.py')  # import a python module, e.g. to run generated plot files.",
                '# PluginManager.test()  # Automated testing of all active plugins. Can take a few minutes.',
            ])
            self.commonCommandsComboBox.setMaximumWidth(150)
            self.commonCommandsComboBox.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
            self.commonCommandsComboBox.currentIndexChanged.connect(self.commandChanged)
            self.mainConsole.repl.inputLayout.insertWidget(1, self.commonCommandsComboBox)
            self.mainConsole.historyBtn.deleteLater()
            self.mainConsole.exceptionBtn.deleteLater()
            self.signalComm.writeSignal.connect(self.write)
            # make sure to use keyword arguments for decorated functions
            self.signalComm.executeSignal.connect(lambda command: self.execute(command=command))
            # make sure to use keyword arguments for decorated functions
            self.signalComm.executeSilentSignal.connect(lambda command: self.executeSilent(command=command))
            for message in self.pluginManager.logger.backLog:
                self.write(message)
            self.pluginManager.logger.backLog = []

    def finalizeInit(self) -> None:  # noqa: D102
        super().finalizeInit()
        self.floatAction.deleteLater()
        del self.floatAction
        namespace = {'timeit': timeit, 'esibd': esibd, 'sys': sys, 'gc': gc, 'np': np, 'itertools': itertools, 'plt': plt, 'inspect': inspect, 'INOUT': INOUT, 'qSet': qSet,
                    'Parameter': Parameter, 'QtCore': QtCore, 'Path': Path, 'Qt': Qt, 'PluginManager': self.pluginManager, 'importlib': importlib, 'version': version,
                      'datetime': datetime, 'QApplication': QApplication, 'self': self.app.mainWindow, 'help': self.help, 'dynamicImport': dynamicImport}
        for plugin in self.pluginManager.plugins:  # direct access to plugins
            namespace[plugin.name] = plugin
        self.mainConsole.localNamespace = namespace
        self.errorFilterAction = self.addStateAction(toolTipFalse='Show only errors.', iconFalse=self.makeCoreIcon('unicode_error.png'),
                                              toolTipTrue='Show all messages.', iconTrue=self.makeCoreIcon('unicode_error.png'),
                                              before=self.aboutAction, event=lambda: self.toggleMessageFilter(error=True))
        self.warningFilterAction = self.addStateAction(toolTipFalse='Show only warnings.', iconFalse=self.makeCoreIcon('unicode_warning.png'),
                                              toolTipTrue='Show all messages.', iconTrue=self.makeCoreIcon('unicode_warning.png'),
                                              before=self.aboutAction, event=lambda: self.toggleMessageFilter(error=False))
        self.openLogAction = self.addAction(toolTip='Open log file.', icon=self.makeCoreIcon('blue-folder-open-document-text.png'),
                                            before=self.aboutAction, event=self.pluginManager.logger.openLog)
        self.inspectAction = self.addAction(toolTip='Inspect object currently in input.',
                                            icon=self.makeCoreIcon(f"zoom_to_rect_large{'_dark' if getDarkMode else ''}.png"),
                                            before=self.aboutAction, event=self.inspect)
        self.closeAction = self.addAction(event=self.hide, toolTip='Hide.', icon=self.makeCoreIcon('close_dark.png' if getDarkMode() else 'close_light.png'))

    def addToNamespace(self, key: str, value: Any) -> None:  # noqa: ANN401
        """Add an attribute to the namespace of the Console.

        :param key: Attribute used to access the object
        :type key: str
        :param value: Value or object.
        :type value: Any
        """
        self.mainConsole.localNamespace[key] = value

    def runTestParallel(self) -> None:  # noqa: D102
        # test all predefined commands. Make sure critical commands are commented out to avoid reset and testing loop etc.
        for i in range(self.commonCommandsComboBox.count())[1:]:
            if not self.testing:
                break
            with self.lock.acquire_timeout(timeout=1, timeoutMessage=f'Could not acquire lock to test {self.commonCommandsComboBox.itemText(i)}') as lock_acquired:
                if lock_acquired:
                    self.signalComm.executeSilentSignal.emit(self.commonCommandsComboBox.itemText(i))
        super().runTestParallel()

    def commandChanged(self, index: int) -> None:  # noqa: ARG002
        """Execute selected command from combobox.

        :param index: Index of selected item.
        :type index: int
        """
        # ignoring second argument
        if self.commonCommandsComboBox.currentIndex() != 0:
            self.execute(command=self.commonCommandsComboBox.currentText())
            self.commonCommandsComboBox.setCurrentIndex(0)

    def write(self, message: str) -> None:
        """Write to integrated console to keep track of message history.

        :param message: The message to be added to the console.
        :type message: str
        """
        # avoid using self.mainConsole.repl.write() because stdout is already handled by core.Logger
        if self.initializedGUI:
            if current_thread() is main_thread():
                self.mainConsole.output.moveCursor(QTextCursor.MoveOperation.End)
                self.mainConsole.output.insertPlainText(message)
                self.mainConsole.scrollToBottom()
                if '⚠️' in message:  # 🪲⚠️❌ℹ️❖  # noqa: RUF003
                    self.mainConsole.outputWarnings.moveCursor(QTextCursor.MoveOperation.End)
                    self.mainConsole.outputWarnings.insertPlainText(message + '\n')
                    sb = self.mainConsole.outputWarnings.verticalScrollBar()
                    if sb:
                        sb.setValue(sb.maximum())
                elif '❌' in message:
                    self.mainConsole.outputErrors.moveCursor(QTextCursor.MoveOperation.End)
                    self.mainConsole.outputErrors.insertPlainText(message + '\n')
                    sb = self.mainConsole.outputErrors.verticalScrollBar()
                    if sb:
                        sb.setValue(sb.maximum())
            else:
                self.signalComm.writeSignal.emit(message)

    def toggleMessageFilter(self, error: bool = True) -> None:
        """Make sure only one filter is active at a time. Shows outout for selected filter.

        :param error: Indicates if messages should be filtered for errors or warnings, defaults to True
        :type error: bool, optional
        """
        if error:
            self.warningFilterAction.state = False
        else:
            self.errorFilterAction.state = False
        if self.warningFilterAction.state:
            self.mainConsole.outputLayout.setCurrentIndex(1)
        elif self.errorFilterAction.state:
            self.mainConsole.outputLayout.setCurrentIndex(2)
        else:
            self.mainConsole.outputLayout.setCurrentIndex(0)

    def toggleVisible(self) -> None:
        """Toggles visibility of Console."""
        if self.dock:
            self.dock.setVisible(self.pluginManager.Settings.showConsoleAction.state if self.pluginManager.Settings.showConsoleAction else True)

    def inspect(self) -> None:
        """Use inspect function of Tree plugin to inspect element currently in the Console input."""
        if self.mainConsole.input.text():
            self.execute(command=f'Tree.inspect({self.mainConsole.input.text()})')
        else:
            self.mainConsole.input.setText('Enter object to be inspected here first.')

    def help(self) -> None:
        """Show simple help message. Overwrites broken help function in PyQtGraph.Console."""
        self.print(f'Read the docs online at https://esibd-explorer.rtfd.io/ or offline at {(Path(__file__).parent / "docs/index.html").resolve()} to get help.')

    @synchronized(timeout=1)
    def execute(self, command: str) -> None:
        """Insert a command in the Console input and executes it.

        :param command: Any valid python command
        :type command: str
        """
        self.mainConsole.input.setText(command)
        self.mainConsole.input.execCmd()
        self.mainConsole.input.setFocus()

    @synchronized(timeout=1)
    def executeSilent(self, command: str) -> None:
        """Insert a command in the Console output and executes it.

        :param command: Any valid python command
        :type command: str
        """
        self.write(command)
        self.mainConsole.input.sigExecuteCmd.emit(command)

    def clear(self) -> None:
        """Clear the Console input."""
        self.mainConsole.input.setText('')

    def hide(self) -> None:
        """Hide the Console."""
        if self.pluginManager.Settings.showConsoleAction:
            self.pluginManager.Settings.showConsoleAction.state = False
            self.pluginManager.Settings.showConsoleAction.triggered.emit(False)  # noqa: FBT003

    def updateTheme(self) -> None:  # noqa: D102
        super().updateTheme()
        self.mainConsole.updateTheme()
        if hasattr(self, 'inspectAction'):
            self.inspectAction.setIcon(self.makeCoreIcon(f"zoom_to_rect_large{'_dark' if getDarkMode else ''}.png"))


class SettingsManager(Plugin):
    """Bundles multiple :class:`settings<esibd.core.Setting>` into a single object to handle shared functionality."""

    version = '1.0'
    pluginType = PLUGINTYPE.INTERNAL

    def __init__(self, parentPlugin: 'Scan | Settings', defaultFile: Path, tree: TreeWidget, name: str = '', **kwargs) -> None:
        """Initialize a SettingsManager.

        :param parentPlugin: Parent plugin.
        :type parentPlugin: Plugin
        :param defaultFile: Default settings file.
        :type defaultFile: Path
        :param name: Manager name, defaults to ''
        :type name: str, optional
        :param tree: Settings tree
        :type tree: QTreeWidget, optional
        """
        super().__init__(**kwargs)
        self.defaultFile = defaultFile
        self.parentPlugin = parentPlugin
        if name:
            self.name = name
        self.tree = tree
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.initSettingsContextMenu)
        self.defaultSettings: dict[str, dict[str, ParameterType | QTreeWidget | PARAMETERTYPE | QWidget | Callable | None]] = {}
        self.settings: dict[str, Setting] = {}

    def addDefaultSettings(self, plugin: Plugin) -> None:
        """Add settings from provided plugin and makes them accessible as attributes.

        :param plugin: The plugin providing the settings.
        :type plugin: :class:`~esibd.plugins.Plugin`
        """
        self.defaultSettings.update(plugin.getDefaultSettings())
        # generate property for direct access of setting value from parent
        for name, default in plugin.getDefaultSettings().items():
            if default[Parameter.ATTR] is not None:
                setattr(plugin.__class__, cast('str', default[Parameter.ATTR]), makeSettingWrapper(name, self))

    def initSettingsContextMenu(self, pos: QPoint) -> None:
        """Initialize the context menu of the setting.

        :param pos: The position of the context menu.
        :type pos: QPoint
        """
        try:
            if hasattr(self.tree.itemAt(pos), 'fullName'):
                self.initSettingsContextMenuBase(self.settings[cast('Setting', self.tree.itemAt(pos)).fullName], self.tree.mapToGlobal(pos))
        except KeyError as e:  # Setting could not be identified
            self.print(str(e))

    OPENPATH = 'Open Path'
    SETTINGS = 'Settings'
    ADDSETTOCONSOLE = 'Add Setting to Console'

    def initSettingsContextMenuBase(self, setting: Parameter | Setting, pos: QPoint) -> None:  # noqa: C901, PLR0912, PLR0915
        """Choose relevant context menu actions based on the type and properties of the Setting.

        :param setting: The setting for which the context menu is requested.
        :type setting: esibd.core.Setting
        :param pos: The position of the context menu.
        :type pos: QPoint
        """
        settingsContextMenu = QMenu(self.tree)
        openPathAction = None
        changePathAction = None
        addItemAction = None
        editItemAction = None
        removeItemAction = None
        copyClipboardAction = None
        setToDefaultAction = None
        makeDefaultAction = None
        if not setting.indicator:
            if setting.parameterType == PARAMETERTYPE.PATH:
                openPathAction = settingsContextMenu.addAction(self.OPENPATH)
                changePathAction = settingsContextMenu.addAction(SELECTPATH)
            elif (setting.parameterType in {PARAMETERTYPE.COMBO, PARAMETERTYPE.INTCOMBO, PARAMETERTYPE.FLOATCOMBO}
                    and not isinstance(setting.parameterParent, Channel) and not setting.fixedItems):
                # Channels are part of Devices which define items centrally
                addItemAction = settingsContextMenu.addAction(Channel.ADDITEM)
                editItemAction = settingsContextMenu.addAction(Channel.EDITITEM)
                removeItemAction = settingsContextMenu.addAction(Channel.REMOVEITEM)
            if not isinstance(setting.parameterParent, Channel):
                if setting.parameterType == PARAMETERTYPE.LABEL:
                    copyClipboardAction = settingsContextMenu.addAction('Copy to clipboard.')
                else:
                    setToDefaultAction = settingsContextMenu.addAction(f'Set to Default: {setting.default}')
                    makeDefaultAction = settingsContextMenu.addAction('Make Default')
            if not settingsContextMenu.actions():
                return
        addSettingToConsoleAction = settingsContextMenu.addAction(self.ADDSETTOCONSOLE)
        settingsContextMenuAction = settingsContextMenu.exec(pos)
        if settingsContextMenuAction:  # no option selected (NOTE: if it is None this could trigger a non initialized action which is also None if not tested here)
            if settingsContextMenuAction is addSettingToConsoleAction:
                self.pluginManager.Console.addToNamespace('setting', setting)
                self.pluginManager.Console.execute(command='setting')
            elif settingsContextMenuAction is copyClipboardAction:
                pyperclip.copy(str(setting.value))
            elif settingsContextMenuAction is setToDefaultAction:
                setting.setToDefault()
            elif settingsContextMenuAction is makeDefaultAction:
                setting.makeDefault()
            elif settingsContextMenuAction is openPathAction:
                openInDefaultApplication(cast('str | Path', setting.value))
            elif settingsContextMenuAction is changePathAction:
                startPath = cast('Path', setting.value)
                newPath = Path(QFileDialog.getExistingDirectory(self.pluginManager.mainWindow, SELECTPATH, startPath.as_posix(),
                                                                QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks))
                if newPath != Path():  # directory has been selected successfully
                    setting.value = newPath
            elif settingsContextMenuAction is addItemAction:
                text, ok = QInputDialog.getText(self, Channel.ADDITEM, Channel.ADDITEM)
                if ok and text:
                    setting.addItem(text)
            elif settingsContextMenuAction is editItemAction:
                text, ok = QInputDialog.getText(self, Channel.EDITITEM, Channel.EDITITEM, text=str(setting.value))
                if ok and text:
                    setting.editCurrentItem(text)
            elif settingsContextMenuAction is removeItemAction:
                setting.removeCurrentItem()

    def init(self) -> None:
        """Initialize Settings. Extended to add settings of all other plugins using addDefaultSettings."""
        # call this after creating the instance, as the instance is required during initialization
        # call after all defaultSettings have been added!
        self.loadSettings(useDefaultFile=True)

    def loadSettings(self, file: 'Path | None' = None, useDefaultFile: bool = False) -> None:  # noqa: C901, PLR0912
        """Load settings from hdf or ini file.

        :param file: File from which settings should be loaded, defaults to None
        :type file: pathlib.Path, optional
        :param useDefaultFile: Indicates if default file should be used, defaults to False
        :type useDefaultFile: bool, optional
        """
        self.loading = True
        if useDefaultFile:
            file = self.defaultFile
        if not file:  # get file via dialog
            file = Path(QFileDialog.getOpenFileName(parent=self.pluginManager.mainWindow, caption=SELECTFILE,
                                                    directory=self.pluginManager.Settings.configPath.as_posix(), filter=self.FILTER_INI_H5)[0])
        if file == Path():
            return
        useFile = False
        defaults_added = False
        items = []
        if file.suffix == FILE_INI:
            # Load settings from INI file
            confParser = None
            if file.exists():
                confParser = configparser.ConfigParser()
                try:
                    confParser.read(file)
                    useFile = True
                except KeyError:
                    pass
            else:
                self.print(f'Could not find file {file.name}.', flag=PRINT.WARNING)
            for name, defaultSetting in self.defaultSettings.items():
                if not defaultSetting[Parameter.INTERNAL] and confParser and name not in confParser:
                    self.print(f'Using default value {defaultSetting[Parameter.VALUE]} for setting {name}.')
                    defaults_added = True
                items.append(parameterDict(name=name,
                    value=confParser[name][Parameter.VALUE] if confParser and name in confParser and Parameter.VALUE in confParser[name] else defaultSetting[Parameter.VALUE],  # type: ignore  # noqa: PGH003
                    default=confParser[name][Parameter.DEFAULT] if confParser and name in confParser and  # type: ignore  # noqa: PGH003
                      Parameter.DEFAULT in confParser[name] else defaultSetting[Parameter.DEFAULT],
                    items=cast('str', confParser[name][Parameter.ITEMS]
                               if confParser and name in confParser and Parameter.ITEMS in confParser[name] else defaultSetting[Parameter.ITEMS]),
                    fixedItems=cast('bool', defaultSetting.get(Parameter.FIXEDITEMS, False)),
                    minimum=cast('float', defaultSetting[Parameter.MIN]), maximum=cast('float', defaultSetting[Parameter.MAX]),
                    internal=cast('bool', defaultSetting.get(Parameter.INTERNAL, False)),
                    advanced=cast('bool', defaultSetting.get(Parameter.ADVANCED, False)),
                    indicator=cast('bool', defaultSetting.get(Parameter.INDICATOR, False)),
                    restore=cast('bool', defaultSetting.get(Parameter.RESTORE, True)),
                    instantUpdate=cast('bool', defaultSetting.get(Parameter.INSTANTUPDATE, True)),
                    displayDecimals=cast('int', defaultSetting.get(Parameter.DISPLAYDECIMALS, 2)),
                    toolTip=cast('str', defaultSetting[Parameter.TOOLTIP]),
                    tree=self.tree if defaultSetting[Parameter.WIDGET] is None else None,
                    parameterType=cast('PARAMETERTYPE', defaultSetting[Parameter.PARAMETER_TYPE]),
                    widget=cast('QWidget', defaultSetting[Parameter.WIDGET]),
                    event=cast('Callable', defaultSetting[Parameter.EVENT])))
        else:
            with h5py.File(file, 'r' if file.exists() else 'w') as h5file:
                group = None
                if self.parentPlugin.name == self.SETTINGS:
                    group = cast('h5py.Group', h5file[self.parentPlugin.name])
                    useFile = True
                elif self.parentPlugin.name in h5file and self.SETTINGS in cast('h5py.Group', h5file[self.parentPlugin.name]):
                    group = cast('h5py.Group', cast('h5py.Group', h5file[self.parentPlugin.name])[self.SETTINGS])
                    useFile = True
                for name, defaultSetting in self.defaultSettings.items():
                    if group and name not in group:
                        self.print(f'Using default value {defaultSetting[Parameter.VALUE]} for setting {name}.')
                        defaults_added = True
                    items.append(parameterDict(name=name,
                        value=group[name].attrs[Parameter.VALUE] if group and name in group and Parameter.VALUE in group[name].attrs else defaultSetting[Parameter.VALUE],  # type: ignore  # noqa: PGH003
                        default=group[name].attrs[Parameter.DEFAULT] if group and name in group and Parameter.DEFAULT in group[name].attrs else defaultSetting[Parameter.DEFAULT],  # type: ignore  # noqa: PGH003
                        items=group[name].attrs[Parameter.ITEMS] if group and name in group and Parameter.ITEMS in group[name].attrs else defaultSetting[Parameter.ITEMS],  # type: ignore  # noqa: PGH003
                        fixedItems=cast('bool', defaultSetting.get(Parameter.FIXEDITEMS, False)),
                        minimum=cast('float', defaultSetting[Parameter.MIN]), maximum=cast('float', defaultSetting[Parameter.MAX]),
                        internal=cast('bool', defaultSetting.get(Parameter.INTERNAL, False)),
                        advanced=cast('bool', defaultSetting.get(Parameter.ADVANCED, False)),
                        indicator=cast('bool', defaultSetting.get(Parameter.INDICATOR, False)),
                        restore=cast('bool', defaultSetting.get(Parameter.RESTORE, True)),
                        instantUpdate=cast('bool', defaultSetting.get(Parameter.INSTANTUPDATE, True)),
                        displayDecimals=cast('int', defaultSetting.get(Parameter.DISPLAYDECIMALS, 2)),
                        toolTip=cast('str', defaultSetting[Parameter.TOOLTIP]),
                        tree=self.tree if defaultSetting[Parameter.WIDGET] is None else None,  # dont use tree if widget is provided independently
                        parameterType=cast('PARAMETERTYPE', defaultSetting[Parameter.PARAMETER_TYPE]),
                        widget=cast('QWidget', defaultSetting[Parameter.WIDGET]),
                        event=cast('Callable', defaultSetting[Parameter.EVENT])))
        self.updateSettings(items, file)
        if not useFile:  # create default if not exist
            self.print(f'Adding default settings in {file.name} for {self.parentPlugin.name}.')
            self.saveSettings(file=file)
        elif defaults_added:
            self.saveSettings(file=file)  # update file with defaults. Defaults would otherwise not be written to file unless they are changed by the user.

        self.tree.collapseAll()  # only session should be expanded by default
        self.tree.expandItem(self.tree.topLevelItem(1))
        self.loading = False

    def updateSettings(self, items: list[dict], file: Path) -> None:
        """Scan for changes and shows change log before overwriting old channel configuration.

        :param items: List of setting dictionaries
        :type items: list[dict]
        :param file: File from which settings are being loaded.
        :type file: pathlib.Path
        """
        # Note: h5diff can be used alternatively to find changes, but the output is not formatted in a user friendly way (hard to correlate values with channels).
        if not self.pluginManager.loading:
            self.changeLog = [f'Change log for loading {self.name} from {file.name}:']
            for item in items:
                if item[Parameter.NAME] in self.settings:
                    if not item[Parameter.INTERNAL]:
                        setting = self.settings[item[Parameter.NAME]]
                        if not setting.equals(item[Parameter.VALUE]):
                            self.changeLog.append(f'Updating setting {setting.fullName} from {setting.formatValue()} to {setting.formatValue(item[Parameter.VALUE])}')
                else:
                    self.changeLog.append(f'Adding setting {item[Parameter.NAME]}')
            newNames = [item[Parameter.NAME] for item in items]
            for setting in self.settings.values():
                if not setting.internal and setting.fullName not in newNames:
                    self.changeLog.append(f'Removing setting {setting.fullName}')
            if len(self.changeLog) == 1:
                self.changeLog.append('No changes.')
            self.pluginManager.Text.setText('\n'.join(self.changeLog), showPlugin=False)  # show changelog
            self.print('Settings updated. Change log available in Text plugin.')
        self.settings.clear()  # clear and load new settings
        self.tree.clear()  # Remove all previously existing widgets. They will be recreated based on settings in file.
        for item in items:
            self.addSetting(item)

    def addSetting(self, item: dict) -> None:
        """Add a Setting including GUI elements.

        :param item: Setting dict.
        :type item: dict
        """
        invisibleRootItem = self.tree.invisibleRootItem()
        if invisibleRootItem:
            self.settings[item[Parameter.NAME]] = Setting(parameterParent=self, name=item[Parameter.NAME], value=item[Parameter.VALUE], default=item[Parameter.DEFAULT],
                            items=item[Parameter.ITEMS], fixedItems=item[Parameter.FIXEDITEMS], minimum=item[Parameter.MIN], maximum=item[Parameter.MAX],
                            internal=item[Parameter.INTERNAL], indicator=item[Parameter.INDICATOR], restore=item[Parameter.RESTORE], instantUpdate=item[Parameter.INSTANTUPDATE],
                            displayDecimals=item[Parameter.DISPLAYDECIMALS], toolTip=item[Parameter.TOOLTIP], tree=item[Parameter.TREE],
                            parameterType=item[Parameter.PARAMETER_TYPE],
                            widget=item[Parameter.WIDGET], event=item[Parameter.EVENT], parentItem=self.requireParentItem(name=item[Parameter.NAME],
                            parentItem=invisibleRootItem), advanced=item[Parameter.ADVANCED])

    def requireParentItem(self, name: str, parentItem: QTreeWidgetItem) -> QTreeWidgetItem:
        """Ensure all parents groups exist.

        :param name: name containing all parent groups
        :type name: str
        :param parentItem: Ensures all groups exist relative to this parent item.
        :type parentItem: QTreeWidgetItem
        :return: Valid parent item for last element of name.
        :rtype: QTreeWidgetItem
        """
        names = name.split('/')
        if len(names) > 1:  # only ensure parents are there. last widget will be created as an Setting
            for name_part in name.split('/')[:-1]:
                children = [parentItem.child(i) for i in range(parentItem.childCount())]  # list of existing children
                children_text = [child.text(0) for child in children if child]
                newParent = parentItem.child(children_text.index(name_part)) if name_part in children_text else QTreeWidgetItem(parentItem, [name_part])
                if newParent:
                    parentItem = newParent
        return parentItem

    def saveSettings(self, file: 'Path | None' = None, useDefaultFile: bool = False) -> None:  # public method  # noqa: C901, PLR0912
        """Save settings to hdf or ini file.

        :param file: File to save settings in, defaults to None
        :type file: pathlib.Path, optional
        :param useDefaultFile: Indicates if defaultFile should be used, defaults to False
        :type useDefaultFile: bool, optional
        """
        if useDefaultFile:
            file = self.defaultFile
        if not file:  # get file via dialog
            file = Path(QFileDialog.getSaveFileName(parent=self.pluginManager.mainWindow, caption=SELECTFILE,
                                                    directory=self.pluginManager.Settings.configPath.as_posix(), filter=self.FILTER_INI_H5)[0])
        if file == Path():
            return
        if file.suffix == FILE_INI:
            # load and update content. Keep settings of currently used plugins untouched as they may be needed when these plugins are enabled in the future
            config = configparser.ConfigParser()
            if file.exists():
                config.read(file)
            config[INFO] = infoDict(self.name)
            for name, defaultSetting in self.defaultSettings.items():
                if name not in {Parameter.DEFAULT.upper(), VERSION} and not self.settings[name].internal:
                    if name not in config:
                        config[name] = {}
                    config[name][Parameter.VALUE] = self.settings[name].formatValue()
                    config[name][Parameter.DEFAULT] = self.settings[name].formatValue(self.settings[name].default)
                    if defaultSetting[Parameter.PARAMETER_TYPE] in {PARAMETERTYPE.COMBO, PARAMETERTYPE.INTCOMBO, PARAMETERTYPE.FLOATCOMBO}:
                        config[name][Parameter.ITEMS] = ','.join(self.settings[name].items)
            with file.open('w', encoding=self.UTF8) as configFile:
                config.write(configFile)
        else:
            with h5py.File(file, 'w' if useDefaultFile else 'a', track_order=True) as h5file:  # will update if exist, otherwise create
                h5py.get_config().track_order = True
                self.hdfUpdateVersion(h5file)
                if self.parentPlugin.name == self.SETTINGS:
                    settings_group = self.requireGroup(h5file, self.parentPlugin.name)
                else:
                    plugin_group = self.requireGroup(h5file, self.parentPlugin.name)
                    settings_group = self.requireGroup(plugin_group, self.SETTINGS)
                for name, defaultSetting in self.defaultSettings.items():
                    if name not in {Parameter.DEFAULT.upper(), VERSION} and not self.settings[name].internal:
                        self.hdfSaveSetting(settings_group, name, defaultSetting)

    def hdfSaveSetting(self, group: h5py.Group, name: str, defaultSetting: dict[str, Any]) -> None:
        """Save Setting in hdf file.

        :param group: The group to which to add Setting.
        :type group: h5py.Group
        :param name: Setting name.
        :type name: str
        :param defaultSetting: Default dict for Setting.
        :type defaultSetting: dict[str, any]
        """
        for name_part in name.split('/'):
            group = self.requireGroup(group, name_part)
        group.attrs[Parameter.VALUE] = self.settings[name].value
        group.attrs[Parameter.DEFAULT] = self.settings[name].default
        if defaultSetting[Parameter.PARAMETER_TYPE] in {PARAMETERTYPE.COMBO, PARAMETERTYPE.INTCOMBO, PARAMETERTYPE.FLOATCOMBO}:
            group.attrs[Parameter.ITEMS] = ','.join(self.settings[name].items)


class Settings(SettingsManager):  # noqa: PLR0904
    """Edit, save, and load all general program and hardware settings.

    Settings can be edited either directly or using
    the context menu that opens on right click. Settings are stored in an
    .ini file which can be edited directly with any text editor if needed. The
    settings file that is used on startup is automatically generated if it
    does not exist. Likewise, default values are used for any missing
    parameters. Setting files can be exported or imported from the user
    interface. A change log will show which settings have changed after importing.
    In addition, the PluginManager and Console can be opened from here.
    """

    version = '1.0'
    pluginType = PLUGINTYPE.CONTROL
    name = 'Settings'
    optional = False
    showConsoleAction = None
    iconFile = 'gear.png'
    useAdvancedOptions = True

    def __init__(self, pluginManager: PluginManager, **kwargs) -> None:  # noqa: D107
        self.tree = TreeWidget()  # Note. If settings will become closable in the future, tree will need to be recreated when it reopens
        self.tree.setHeaderLabels(['Parameter', 'Value'])
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # size to content prevents manual resize
        header = self.tree.header()
        if header:
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.confINI = f'{self.name}.ini'
        self.loadGeneralSettings = f'Load {PROGRAM_NAME} settings.'
        super().__init__(parentPlugin=self, tree=self.tree, defaultFile=getValidConfigPath() / self.confINI, pluginManager=pluginManager, **kwargs)
        self.previewFileTypes = [self.confINI]

    def initDock(self) -> None:  # noqa: D102
        super().initDock()
        if self.dock:
            self.dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)  # not floatable or movable

    def initGUI(self) -> None:  # noqa: D102
        super().initGUI()
        self.addContentWidget(self.tree)
        self.loadSettingsAction = self.addAction(event=lambda: self.loadSettings(None), toolTip=f'Load {PROGRAM_NAME} Settings.',
                                                  icon=self.makeCoreIcon('blue-folder-import.png'))
        self.loadSettingsAction.setVisible(False)
        self.saveSettingsAction = self.addAction(event=lambda: self.saveSettings(None), toolTip=f'Export {PROGRAM_NAME} Settings.',
                                                  icon=self.makeCoreIcon('blue-folder-export.png'))
        self.saveSettingsAction.setVisible(False)
        self.addAction(event=self.pluginManager.managePlugins, toolTip=f'Manage {PROGRAM_NAME} Plugins.', icon=self.makeCoreIcon('block--pencil.png'))
        self.showConsoleAction = self.addStateAction(event=self.pluginManager.Console.toggleVisible,
                                                      toolTipFalse='Show Console.', iconFalse=self.makeCoreIcon('terminal.png'),
                                                 toolTipTrue='Hide Console.', iconTrue=self.makeCoreIcon('terminal--minus.png'), attr='showConsole')

    def runTestParallel(self) -> None:  # noqa: D102
        # cannot test file dialogs that require user interaction
        self.testControl(self.showConsoleAction, value=True)
        self.tree.expandAllItems()
        for setting in self.settings.values():
            if setting.name not in {DATAPATH, CONFIGPATH, PLUGINPATH, self.SESSIONPATH, DARKMODE, TESTMODE} and f'{self.SESSION}/' not in setting.fullName:
                # do not change session path unintentionally during testing
                self.testControl(setting.getWidget(), setting.value, label=f'Testing {setting.name} {setting.toolTip or "No toolTip."}')
        super().runTestParallel()

    def finalizeInit(self) -> None:  # noqa: D102
        super().finalizeInit()
        self.floatAction.deleteLater()
        del self.floatAction
        self.requiredPlugin('DeviceManager')
        self.requiredPlugin('Explorer')
        self.filterLineEdit = QLineEdit()
        self.filterLineEdit.setMaximumWidth(100)
        self.filterLineEdit.setMinimumWidth(50)
        self.filterLineEdit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.filterLineEdit.textChanged.connect(self.toggleAdvanced)
        self.filterLineEdit.setPlaceholderText('Search')
        if self.titleBar:
            self.titleBar.insertWidget(self.aboutAction, self.filterLineEdit)
        self.toggleAdvanced(advanced=False)

    def init(self) -> None:
        """Init all internal settings and those of all other plugins."""
        self.addDefaultSettings(plugin=self)  # make settings available via self.attr
        super().init()  # call first time to only load internal settings to enable validation of datapath
        for plugin in self.pluginManager.plugins:
            if hasattr(plugin, 'getDefaultSettings') and not isinstance(plugin, Scan):
                # only if plugin has specified settings that are not handled by separate settingsMgr within the plugin
                try:
                    self.addDefaultSettings(plugin=plugin)
                except Exception as e:  # noqa: BLE001
                    self.print(f'Error loading settings for {plugin.name}: {e}')
        super().init()  # call again to load all settings from all other plugins
        self.settings[f'{self.SESSION}/{self.MEASUREMENTNUMBER}']._valueChanged = False  # make sure sessionpath is not updated after restoring measurement number  # noqa: SLF001

    def toggleAdvanced(self, advanced: 'bool | None' = False) -> None:  # noqa: ARG002, D102
        self.loadSettingsAction.setVisible(self.advancedAction.state)
        self.saveSettingsAction.setVisible(self.advancedAction.state)
        search_filter = self.filterLineEdit.text()
        for setting in self.settings.values():
            if search_filter:
                if search_filter.lower() not in setting.name.lower():
                    setting.setHidden(True)
                    continue
            elif setting.advanced:
                setting.setHidden(not self.advancedAction.state)
            else:
                setting.setHidden(False)  # unhide previously hidden settings

    def loadData(self, file: Path, showPlugin: bool = False) -> None:  # noqa: ARG002, D102
        return  # nothing to do, content will be handled by Text plugin

    SESSION = 'Session'
    MEASUREMENTNUMBER = 'Measurement number'
    SESSIONPATH = 'Session path'

    def getDefaultSettings(self) -> dict[str, dict]:
        """Provide settings and corresponding default values.

        :return: Dictionary of settings
        :rtype: dict[str, dict]
        """
        # definitions for type hinting
        self.dataPath: Path
        self.configPath: Path
        self.pluginPath: Path
        self.showVideoRecorders: bool
        self.showMouseClicks: bool
        self.sessionPath: str
        self.maxLagTolerance: int
        self.lagWaitTime: int
        self.errorResetTime: int

        ds = {}
        ds[f'{GENERAL}/{DATAPATH}'] = parameterDict(value=defaultDataPath,
                                        parameterType=PARAMETERTYPE.PATH, internal=True, event=self.updateDataPath, attr='dataPath')
        ds[f'{GENERAL}/{CONFIGPATH}'] = parameterDict(value=defaultConfigPath,
                                        parameterType=PARAMETERTYPE.PATH, internal=True, event=self.updateConfigPath, attr='configPath')
        ds[f'{GENERAL}/{PLUGINPATH}'] = parameterDict(value=defaultPluginPath,
                                        parameterType=PARAMETERTYPE.PATH, internal=True, event=self.updatePluginPath, attr='pluginPath')
        # validate paths before loading settings from file
        getValidDataPath()
        self.defaultFile = getValidConfigPath() / self.confINI
        getValidPluginPath()
        # access using getDPI()
        ds[f'{GENERAL}/{DPI}'] = parameterDict(value='100', toolTip='DPI used for graphs.', internal=True, event=self.updateDPI,
                                                                items='100, 150, 200, 300', parameterType=PARAMETERTYPE.INTCOMBO)
        # access using getTestMode()
        ds[f'{GENERAL}/{DARKMODE}'] = parameterDict(value=True, toolTip='Use dark mode.', internal=True, event=self.pluginManager.updateTheme,
                                                                parameterType=PARAMETERTYPE.BOOL)
        ds[f'{GENERAL}/{CLIPBOARDTHEME}'] = parameterDict(value=False, toolTip='Use current theme when copying graphs to clipboard. Disable to always use light theme.',
                                                                internal=True, parameterType=PARAMETERTYPE.BOOL)
        ds[f'{GENERAL}/{ICONMODE}'] = parameterDict(value='Icons', toolTip='Chose if icons, labels, or both should be used in tabs.',
                                                                   event=lambda: self.pluginManager.toggleTitleBarDelayed(update=False),
                                                                internal=True, parameterType=PARAMETERTYPE.COMBO, items='Icons, Labels, Both', fixedItems=True)
        # advanced general settings at bottom of list
        ds[f'{GENERAL}/{TESTMODE}'] = parameterDict(value=True, toolTip='Devices will fake communication in Testmode!', parameterType=PARAMETERTYPE.BOOL,
                                    event=lambda: self.testModeChanged()  # pylint: disable=unnecessary-lambda  # needed to delay execution until initialized  # noqa: PLW0108
                                    , internal=True, advanced=True)
        ds[f'{GENERAL}/{MAXLAGTOLERANCE}'] = parameterDict(value=10, minimum=1, maximum=60, internal=True, advanced=True, parameterType=PARAMETERTYPE.INT, attr='maxLagTolerance',
                toolTip='If the application is lagging for this many seconds communication will be pause\nto restore responsiveness and the error count will be incremented.')
        ds[f'{GENERAL}/{LAGWAITTIME}'] = parameterDict(value=5, minimum=1, maximum=60, internal=True, advanced=True, parameterType=PARAMETERTYPE.INT, attr='lagWaitTime',
                toolTip='If the application is lagging communication will be paused for this many seconds to restore responsiveness.')
        ds[f'{GENERAL}/{ERRORRESETTIME}'] = parameterDict(value=10, minimum=1, maximum=60,
                                                            toolTip="""Errors will be reset if no additional error occurs during this many minutes.\n"""
                                    """To restore stability, communication will be stopped when too many errors occur and the user interface may have become unresponsive.\n"""
                                    """Reduce this if your want to tolerate more errors before communication gets stopped.""",
                                                          parameterType=PARAMETERTYPE.INT, internal=True, advanced=True, attr='errorResetTime', event=self.resetTimeChanged)
        ds[f'{GENERAL}/{WAKEMODE}'] = parameterDict(value=False, toolTip='Will prevent screen lock by simulating a keyboard action every 5 min', parameterType=PARAMETERTYPE.BOOL,
                                    event=self.pluginManager.DeviceManager.wake, internal=True, advanced=True)
        ds[f'{GENERAL}/{DEBUG}'] = parameterDict(value=False, toolTip='Enables additional functionality for debugging.',
                                                                   internal=True, parameterType=PARAMETERTYPE.BOOL, advanced=True)
        ds[f'{GENERAL}/{LOGLEVEL}'] = parameterDict(value='Basic', toolTip='Determine level of detail in log.',
                                                                   internal=True, parameterType=PARAMETERTYPE.COMBO, advanced=True,
                                                                   items='Basic, Debug, Verbose, Trace', fixedItems=True)
        ds[f'{GENERAL}/Show video recorders'] = parameterDict(value=False, toolTip='Show icons to record videos of plugins.',
                                                              event=self.pluginManager.toggleVideoRecorder,
                                                                internal=True, parameterType=PARAMETERTYPE.BOOL, attr='showVideoRecorders', advanced=True)
        ds[f'{GENERAL}/Highlight mouse clicks'] = parameterDict(value=False, toolTip='Highlight mouse clicks for screen cast creation.',
                                                                   internal=True, parameterType=PARAMETERTYPE.BOOL, attr='showMouseClicks', advanced=True)
        ds[f'{self.SESSION}/{self.MEASUREMENTNUMBER}'] = parameterDict(value=0, minimum=0, maximum=100000000,
                                                                       toolTip='Self incrementing measurement number. Set to 0 to start a new session.',
                                                                parameterType=PARAMETERTYPE.INT,
                                                                instantUpdate=False,  # only trigger event when changed by user!
                                                                event=lambda: self.updateSessionPath(self.measurementNumber), attr='measurementNumber')
        ds[f'{self.SESSION}/{self.SESSIONPATH}'] = parameterDict(value='', toolTip='Path for storing session data. Relative to data path.',
                                                                parameterType=PARAMETERTYPE.LABEL, attr='sessionPath')
        return ds

    def loadSettings(self, file: 'Path | None' = None, useDefaultFile: bool = False) -> None:  # noqa: D102
        if self.pluginManager.DeviceManager.initialized():
            if CloseDialog(title='Stop communication?', ok='Stop communication and load settings',
                           prompt='Communication is still running. Stop communication before loading settings!').exec():
                self.pluginManager.DeviceManager.closeCommunication()
            else:
                return
        super().loadSettings(file=file, useDefaultFile=useDefaultFile)

    def updateDataPath(self) -> None:
        """Update session path and Explorer root directory after changing data path."""
        if not self.pluginManager.loading:
            self.pluginManager.DeviceManager.closeCommunication(message='Stopping communication before changing data path.')
            self.updateSessionPath()
            self.pluginManager.Explorer.updateRoot(self.dataPath)

    def updateConfigPath(self) -> None:
        """Reload settings and plugin configuration after changing configuration path."""
        self.defaultFile = self.configPath / self.confINI
        if not self.pluginManager.loading:
            self.pluginManager.DeviceManager.closeCommunication(message='Stopping communication before changing config path.')
            self.app.splashScreen.show()
            self.loadSettings(self.defaultFile)
            self.processEvents()
            self.pluginManager.DeviceManager.restoreConfiguration()
            if self.pluginManager.logger.active:
                self.pluginManager.logger.close()  # release old log file
                self.pluginManager.logger.open()  # continue logging in new location
            self.app.splashScreen.close()

    def updatePluginPath(self) -> None:
        """Restart after changing user plugin path."""
        if CloseDialog(title='Restart now', ok='Restart now', prompt='Plugins will be updated on next restart.').exec():
            self.pluginManager.mainWindow.closeApplication(restart=True)

    def incrementMeasurementNumber(self) -> None:
        """Increment without triggering event."""
        self.measurementNumber += 1
        self.settings[f'{self.SESSION}/{self.MEASUREMENTNUMBER}']._valueChanged = False  # prevent event  # noqa: SLF001
        self.settings[f'{self.SESSION}/{self.MEASUREMENTNUMBER}'].settingEvent()  # only save new value without triggering updateSessionPath

    def updateSessionPath(self, mesNum: int = 0) -> None:
        """Update the session path based on settings. Overwrite if you want to use different fields instead.

        :param mesNum: measurement number, defaults to 0
        :type mesNum: int, optional
        """
        if not self.pluginManager.loading:
            self.sessionPath = str(self.pathInputValidation(self.buildSessionPath()))
            self.measurementNumber = mesNum
            self.print(f'Updated session path to {self.sessionPath}')

    def buildSessionPath(self) -> Path:
        """Build the session path based on timestamp. Extend to add additional elements to session path."""
        return Path(*[datetime.now().strftime('%Y-%m-%d_%H-%M')])

    def updateDPI(self) -> None:
        """Update DPI in all active matplotlib figures."""
        for plugin in self.pluginManager.plugins:
            if hasattr(plugin, 'fig') and plugin.fig:
                plugin.fig.set_dpi(getDPI())
                plugin.plot()

    def testModeChanged(self) -> None:
        """Close communication so it can be reinitialized with new test mode state."""
        if getTestMode():
            self.print('Test mode is active! Simulating Data!', flag=PRINT.WARNING)
        self.pluginManager.DeviceManager.closeCommunication(manual=False, closing=False)
        self.pluginManager.DeviceManager.clearPlot()
        self.pluginManager.DeviceManager.plot()  # replot to update test mode warning label

    def resetTimeChanged(self) -> None:
        """Update all tooltips that mention the error reset time."""
        for device in self.pluginManager.DeviceManager.getDevices():
            if isinstance(device, Device):
                device.updateErrorCountToolTip()

    def getFullSessionPath(self) -> Path:
        """Return full session path inside data path."""
        fullSessionPath = Path(*[self.dataPath, self.sessionPath])
        fullSessionPath.mkdir(parents=True, exist_ok=True)  # create if not already existing
        return fullSessionPath

    def getMeasurementFileName(self, extension: str) -> Path:
        """Get measurement file name based on session path, measurement number, and plugin specific file extension.

        :param extension: Plugin specific file extension.
        :type extension: str
        :return: Full measurement file path with measurement number and extension.
        :rtype: pathlib.Path
        """
        return self.getFullSessionPath() / f'{self.getFullSessionPath().name}_{self.measurementNumber:03d}{extension}'

    def componentInputValidation(self, component: str) -> str:
        """Validate a path component.

        :param component: Path component.
        :type component: str
        :return: Validated path component.
        :rtype: str
        """
        illegal_characters = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
        return ''.join(char if char not in illegal_characters else '_' for char in component)

    def pathInputValidation(self, path: Path) -> Path:
        """Validate path.

        :param path: The path to validate.
        :type path: pathlib.Path
        :return: Validated path.
        :rtype: pathlib.Path
        """
        return Path(*[self.componentInputValidation(part) for part in path.parts])

    # "close" not needed, settings are saved instantly when changed


class DeviceManager(Plugin):  # noqa: PLR0904
    """Bundle functionality of devices and thus allows to initialize, start, and stop data acquisition from all devices with a single click.

    In the Advanced mode it allows to
    import all channels of all devices from a single file. Ideally, plugins
    that control potentially dangerous hardware like power supplies, cryo
    coolers, or vacuum valves should add a status icon to the device
    manager, so that their status is visible at all times and they can be
    shut down quickly, even when the corresponding plugin tab is is not
    selected. Internally, the device manager also serves as a
    central interface to all data channels, independent of the devices they
    belong to, making it easy to setup collection of any number of output
    signals as a function of any number of input signals.
    """

    documentation = """The device manager, by default located below the live displays, bundles
    functionality of devices and thus allows to initialize, start, and stop
    data acquisition from all devices with a single click. In the Advanced mode it allows to
    import all channels of all devices from a single file. Ideally, plugins
    that control potentially dangerous hardware like power supplies, cryo
    coolers, or vacuum valves should add a status icon to the device
    manager, so that their status is visible at all times and they can be
    shut down quickly, even when the corresponding plugin tab is is not
    selected. Internally, the device manager also serves as a
    central interface to all data channels, independent of the devices they
    belong to, making it easy to setup collection of any output
    signals as a function of any input signals.
    """

    name = 'DeviceManager'
    version = '1.0'
    pluginType = PLUGINTYPE.DEVICEMGR
    optional = False
    iconFile = 'DeviceManager.png'
    useAdvancedOptions = True

    signalComm: 'SignalCommunicate'

    class SignalCommunicate(Plugin.SignalCommunicate):
        """Bundle pyqtSignals."""

        storeOutputDataSignal = pyqtSignal()
        """Signal that triggers storage of device data."""
        restoreOutputDataSignal = pyqtSignal()
        """Signal that triggers restoring of device data."""
        wakeSignal = pyqtSignal()
        """Signal that triggers keyboard action to prevent screen lock."""
        closeCommunicationSignal = pyqtSignal()
        """Signal that triggers stop communication."""
        exportOutputDataSignal = pyqtSignal(Path, bool)
        """Signal that triggers export of configuration."""
        loadConfigurationSignal = pyqtSignal(Path)
        """Signal that triggers loading of configuration."""

    def __init__(self, **kwargs) -> None:
        """Initialize a DeviceManager."""
        super().__init__(**kwargs)
        self.previewFileTypes = ['_combi.dat.h5']
        self.dataThread = None
        self._recording = False
        self.signalComm.storeOutputDataSignal.connect(self.storeOutputData)
        self.signalComm.restoreOutputDataSignal.connect(self.restoreOutputData)
        self.signalComm.wakeSignal.connect(self.wake)
        self.signalComm.closeCommunicationSignal.connect(self.closeCommunication)
        self.signalComm.exportOutputDataSignal.connect(self.exportOutputDataKwargs)
        self.signalComm.loadConfigurationSignal.connect(self.loadConfiguration)

    def initGUI(self) -> None:  # noqa: D102
        super().initGUI()
        self.importAction = self.addAction(event=lambda: self.loadConfiguration(file=None), toolTip='Import all device channels and values.',
                                           icon=self.makeCoreIcon('blue-folder-import.png'))
        self.importAction.setVisible(False)
        self.exportAction = self.addAction(event=lambda: self.exportOutputData(file=None, useAllHistory=False),
                                           toolTip='Save all visible history and all channels to current session.', icon=self.makeCoreIcon('database-export.png'))
        self.closeCommunicationAction = self.addAction(event=lambda: self.closeCommunication(manual=True), toolTip='Close all communication.', icon=self.makeCoreIcon('stop.png'))
        self.addAction(event=self.initializeCommunication, toolTip='Initialize all communication.', icon=self.makeCoreIcon('rocket-fly.png'))
        # lambda needed to avoid "checked" parameter passed by QAction
        self.recordingAction = self.addStateAction(event=self.toggleRecording, toolTipFalse='Start all data acquisition.',
                                                iconFalse=self.makeCoreIcon('play.png'), toolTipTrue='Stop all data acquisition.', iconTrue=self.makeCoreIcon('pause.png'))

    def initDock(self) -> None:  # noqa: D102
        super().initDock()
        if self.dock:
            self.dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)  # not floatable or movable
            self.dock.setMaximumHeight(22)  # GUI will only consist of titleBar

    def finalizeInit(self) -> None:  # noqa: D102
        self.requiredPlugin('Settings')
        self.globalUpdate(apply=True)
        if hasattr(self.pluginManager, 'Settings') and not self.pluginManager.Settings.sessionPath:  # keep existing session path when restarting
            self.pluginManager.Settings.updateSessionPath()
        super().finalizeInit()
        self.floatAction.deleteLater()
        del self.floatAction
        if hasattr(self, 'titleBarLabel') and self.titleBarLabel:
            self.titleBarLabel.deleteLater()
            self.titleBarLabel = None
        self.toggleTitleBarDelayed()  # Label not needed for DeviceManager
        self.storeTimer = QTimer()
        self.storeTimer.timeout.connect(self.storeOutputData)
        self.storeTimer.setInterval(3600000)  # every 1 hour
        self.storeTimer.start()
        self.wakeTimer = QTimer()
        self.wakeTimer.timeout.connect(self.wake)
        self.wakeTimer.setInterval(300000)  # every 5 minutes
        self.wakeTimer.start()
        self.testTimeout = 30 + 30 * len(self.pluginManager.getPluginsByClass(ChannelManager))  # Allow extra time to test other plugins

    def afterFinalizeInit(self) -> None:  # noqa: D102
        super().afterFinalizeInit()
        self.videoRecorderAction.toolTipFalse = f'Record video of {PROGRAM_NAME}.'
        self.videoRecorderAction.toolTipTrue = f'Stop and save video of {PROGRAM_NAME}.'
        self.videoRecorderAction.setToolTip(self.videoRecorderAction.toolTipFalse)
        self.videoRecorder.recordWidget = self.pluginManager.mainWindow  # record entire window

    def toggleAdvanced(self, advanced: 'bool | None' = False) -> None:  # noqa: ARG002, D102
        self.importAction.setVisible(self.advancedAction.state)

    def loadConfiguration(self, file: 'Path | None' = None) -> None:
        """Load configuration of all devices from a single file.

        :param file: File to load from. Should only be passed as argument for automated testing, defaults to None
        :type file: Path | None, optional
        """
        if self.initialized():
            if CloseDialog(title='Stop communication?', ok='Stop communication and load all configurations',
                            prompt='Communication is still running. Stop communication before loading all configurations!').exec():
                self.closeCommunication()
            else:
                return
        if file is None:
            file = Path(QFileDialog.getOpenFileName(parent=None, caption=SELECTFILE, filter=self.FILTER_INI_H5,
                    directory=self.pluginManager.Settings.getFullSessionPath().as_posix())[0])
        if file != Path():
            first = True
            self.loading = True
            for plugin in self.pluginManager.getPluginsByClass(ChannelManager):
                load = False
                with h5py.File(name=file, mode='r', track_order=True) as h5file:
                    if plugin.name in h5file:
                        load = True
                if load:
                    plugin.loadConfiguration(file=file, append=not first)
                    first = False
            self.loading = False

    def runTestParallel(self) -> None:  # noqa: D102
        self.testControl(self.recordingAction, value=True, delay=5)  # even in test mode initialization time of up to 2 seconds is simulated
        for plugin in self.pluginManager.getPluginsByClass(ChannelManager):
            if not self.testing:
                break
            if plugin.useDisplays and plugin.liveDisplay:
                initialState = plugin.toggleLiveDisplayAction.state
                self.testControl(plugin.toggleLiveDisplayAction, value=True, delay=1)
                if self.waitForCondition(condition=lambda plugin=plugin: plugin.liveDisplayActive() and hasattr(plugin.liveDisplay, 'displayTimeComboBox'),
                                         timeoutMessage=f'live display of {plugin.name}.', timeout=15):
                    self.testControl(plugin.liveDisplay.displayTimeComboBox, 1)
                    plugin.liveDisplay.runTestParallel()
                self.testControl(plugin.toggleLiveDisplayAction, initialState, 1)
            self.bufferLagging()
        for scan in self.pluginManager.getPluginsByClass(Scan):
            if not self.testing:
                break
            # has to run here while all plugins are recording
            self.print(f'Starting scan {scan.name}.')
            scan.raiseDock(showPlugin=True)
            self.testControl(scan.recordingAction, value=True)
            if self.waitForCondition(condition=lambda scan=scan: not scan.finished,
                                     timeoutMessage=f'start of {scan.name} scan.', timeout=10):
                if self.waitForCondition(condition=lambda scan=scan: scan.displayActive() and hasattr(scan.display, 'videoRecorderAction') and scan.recording,
                                     timeoutMessage=f'display of {scan.name} scan.', timeout=10):
                    time.sleep(5)  # scan for 5 seconds
                    self.print(f'Stopping scan {scan.name}.')
                    self.testControl(scan.recordingAction, value=False)
                    # wait for scan to finish and save file before starting next one to avoid scans finishing at the same time
                    self.waitForCondition(condition=lambda scan=scan: scan.finished, timeoutMessage=f'stopping {scan.name} scan.', timeout=30)
                    self.pluginManager.Explorer.activeFileFullPath = scan.file
                    scan.testPythonPlotCode(closePopup=True)
            else:
                # not scan.finished is needed to skip further test if scan did not start (e.g. due to no initialized output channels)
                self.print(f'Scan did not start. Skip testing scan {scan.name}.', flag=PRINT.WARNING)
            self.bufferLagging()
        self.testControl(self.closeCommunicationAction, value=True)
        file = self.pluginManager.Settings.getMeasurementFileName(self.previewFileTypes[0])
        self.print(f'Test export OutputData to {file.as_posix()} and storing entire device histories to default files.')
        self.signalComm.exportOutputDataSignal.emit(file, False)  # noqa: FBT003
        time.sleep(5)  # wait for export
        self.print(f'Test loading configuration from {file.as_posix()}')
        self.signalComm.loadConfigurationSignal.emit(file)
        if self.waitForCondition(condition=lambda: not self.loading,
                          timeoutMessage=f'Loading configuration from {file.as_posix()}', timeout=30):
            self.print(f'Finished test loading configuration from {file.as_posix()}')
        time.sleep(5)  # wait for import
        super().runTestParallel()

    @property
    def recording(self) -> bool:
        """Indicates if at least one device is recording."""
        return any([plugin.recording for plugin in self.pluginManager.getPluginsByClass(ChannelManager)] + [self._recording])

    @recording.setter
    def recording(self, recording: bool) -> None:
        self._recording = recording
        # allow output widgets to react to change if acquisition state
        self.recordingAction.state = recording

    def initialized(self) -> bool:
        """Indicate if communication to at least one device is initialized."""
        if self.pluginManager.loading:
            return False
        return any(plugin.initialized for plugin in self.pluginManager.getPluginsByClass(Device))

    def loadData(self, file: Path, showPlugin: bool = True) -> None:  # noqa: D102
        for device in self.getDevices():
            device.loadData(file, showPlugin)

    def channels(self, inout: INOUT = INOUT.BOTH) -> list[Channel]:
        """Get a flat list of all channels based on device type.

        :param inout: Type of device, defaults to INOUT.BOTH
        :type inout: INOUT, optional
        :return: List of channels.
        :rtype: list[:class:`~esibd.core.Channel`]
        """
        return [y for x in [device.getChannels() for device in self.getDevices(inout)] for y in x]

    def getChannelByName(self, name: str, inout: INOUT = INOUT.BOTH) -> Channel | None:
        """Get channel based on unique name and type.

        :param name: Unique channel name.
        :type name: str
        :param inout: Type of channel, defaults to :attr:`~esibd.const.INOUT.BOTH`
        :type inout: :attr:`~esibd.const.INOUT`, optional
        :return: The requested channel.
        :rtype: :class:`~esibd.core.Channel`
        """
        return next((channel for channel in self.channels(inout) if channel.name.strip().lower() == name.strip().lower()), None)

    def getDevices(self, inout: INOUT = INOUT.BOTH) -> list[ChannelManager] | list[Device]:
        """Get devices depending on device type.

        :param inout: Type of device, defaults to INOUT.BOTH
        :type inout: INOUT, optional
        :return: List of devices
        :rtype: list[:class:`~esibd.plugins.Device`]
        """
        if inout == INOUT.BOTH:
            return self.getInputDevices() + self.getOutputDevices()
        if inout == INOUT.ALL:
            return self.getInputDevices() + self.getOutputDevices() + self.getRelays()
        if inout == INOUT.IN:
            return self.getInputDevices()
        # inout == INOUT.OUT:
        return self.getOutputDevices()

    def getInputDevices(self) -> list[Device]:
        """Get all input devices."""
        return cast('list[Device]', self.pluginManager.getPluginsByType(PLUGINTYPE.INPUTDEVICE))

    def getOutputDevices(self) -> list[Device]:
        """Get all output devices."""
        return cast('list[Device]', self.pluginManager.getPluginsByType(PLUGINTYPE.OUTPUTDEVICE))

    def getRelays(self) -> list[ChannelManager]:
        """Get all channel managers. These usually do not have real channels but channels that link to device channels in other plugins."""
        return cast('list[ChannelManager]', self.pluginManager.getPluginsByType(PLUGINTYPE.CHANNELMANAGER))

    def getActiveLiveDisplays(self) -> list[LiveDisplay]:
        """Get all active liveDisplays."""
        return [plugin.liveDisplay for plugin in self.pluginManager.getPluginsByClass(ChannelManager) if plugin.liveDisplayActive() and plugin.liveDisplay]

    def getActiveStaticDisplays(self) -> list[StaticDisplay]:
        """Get all active staticDisplays."""
        return [plugin.staticDisplay for plugin in self.pluginManager.getPluginsByClass(ChannelManager) if plugin.staticDisplayActive() and plugin.staticDisplay]

    def getDefaultSettings(self) -> dict[str, dict[str, ParameterType | QTreeWidget | PARAMETERTYPE | QWidget | Callable | None]]:  # noqa: D102

        # definitions for type hinting
        self.max_display_size: int
        self.limit_display_size: bool

        defaultSettings = super().getDefaultSettings()
        defaultSettings['Acquisition/Max display points'] = parameterDict(value=2000,
                        toolTip='Maximum number of data points per channel used for plotting. Decrease if plotting is limiting performance.',
                        event=lambda: self.livePlot(apply=True), parameterType=PARAMETERTYPE.INT, minimum=100, maximum=100000, attr='max_display_size')
        defaultSettings['Acquisition/Limit display points'] = parameterDict(value=True, toolTip="Number of displayed data points will be limited to 'Max display points'",
                                                                            parameterType=PARAMETERTYPE.BOOL, event=lambda: self.livePlot(apply=True), attr='limit_display_size')
        return defaultSettings

    def restoreConfiguration(self) -> None:
        """Load configuration and settings for all devices and scans."""
        for device in self.getDevices():
            device.loadConfiguration(useDefaultFile=True)
            self.processEvents()
        for scan in self.pluginManager.getPluginsByClass(Scan):
            scan.loadSettings(useDefaultFile=True)
            self.processEvents()

    def clearPlot(self) -> None:
        """Clear plots for all plugins."""
        for plugin in self.pluginManager.getPluginsByClass(ChannelManager):
            plugin.clearPlot()

    def plot(self) -> None:
        """Clear plots for all plugins."""
        for plugin in self.pluginManager.getPluginsByClass(ChannelManager):
            plugin.plot()

    def livePlot(self, apply: bool = False) -> None:
        """Update plots for all active liveDisplays.

        :param apply: Indicates if plot curves should be recreated or just updated. Defaults to False
        :type apply: bool, optional
        """
        for liveDisplay in self.getActiveLiveDisplays():
            liveDisplay.plot(apply)

    def stopRecording(self) -> None:
        """Stop recording for all devices. Communication stays active."""
        if CloseDialog(title='Stop all recording?', ok='Stop all recording', prompt='Stop recording on all devices? Active scans will be stopped.').exec():
            self.recording = False
            for liveDisplay in self.getActiveLiveDisplays():
                liveDisplay.parentPlugin.recording = False
            self.stopScans()
        elif self.recording:
            self.recordingAction.state = self.recording

    def closeCommunication(self, manual: bool = False, closing: bool = False, message: str = 'Stopping communication.') -> None:
        """Close all communication.

        :param manual: Indicates if triggered by user and needs confirmation dialog before proceeding. Defaults to False
        :type manual: bool, optional
        :param closing: Indicate that the application is closing, defaults to False
        :type closing: bool, optional
        :param message: Message indicating that the communication is stopped. Optionally add reason. Defaults to 'Stopping communication.'
        :type message: str, optional
        """
        if not self.initialized():
            return  # already closed
        if not manual or self.testing or CloseDialog(title='Close all communication?', ok='Close all communication',
                                                      prompt='Close communication with all devices and stop all scans?').exec():
            self.print(message)
            self.recording = False
            self.stopScans(closing=closing)
            for plugin in self.pluginManager.getPluginsByClass(ChannelManager):
                plugin.closeCommunication()

    def stopScans(self, closing: bool = False) -> None:
        """Stop all scans and waits for them to finish.

        :param closing: Indicate that the application is closing and needs to wait for scans to finish. Defaults to False
        :type closing: bool, optional
        """
        scans = self.pluginManager.getPluginsByClass(Scan)
        for scan in scans:
            scan.recording = False  # stop all running scans
        if closing:
            unfinishedScans = ''
            for scan in scans:
                if not scan.finished:  # Give scan time to complete and save file. Avoid scan trying to access main GUI after it has been destroyed.
                    unfinishedScans += f'{scan.name}, '
            if unfinishedScans:
                self.waitForCondition(condition=lambda scans=scans: all(scan.finished for scan in scans),
                                       timeoutMessage=f'{unfinishedScans.strip(", ")} to complete.', timeout=30, interval=0.5)

    def exportOutputDataKwargs(self, file: 'Path | None' = None, useAllHistory: bool = False) -> None:
        """Export output data for all active LiveDisplays.

        This version is compatible with pyqtSignal (no keyword argument) and @synchronized() (works only with keyword arguments).
        :param file: The file to which the data should be added, defaults to None
        :type file: Path | None, optional
        :param useAllHistory: Export entire history and not just visible part, defaults to False
        :type useAllHistory: bool, optional
        """
        self.exportOutputData(file=file, useAllHistory=useAllHistory)

    @synchronized()
    def exportOutputData(self, file: 'Path | None' = None, useAllHistory: bool = False) -> None:
        """Export output data for all active LiveDisplays.

        :param file: The file to which the data should be added, defaults to None
        :type file: Path | None, optional
        :param useAllHistory: Export entire history and not just visible part, defaults to False
        :type useAllHistory: bool, optional
        """
        if not file:
            self.pluginManager.Settings.incrementMeasurementNumber()
            file = self.pluginManager.Settings.getMeasurementFileName(self.previewFileTypes[0])
        with h5py.File(name=file, mode=('a'), track_order=True) as h5File:
            self.hdfUpdateVersion(h5File)
            for liveDisplay in self.getActiveLiveDisplays():
                if isinstance(liveDisplay.parentPlugin, Device):
                    liveDisplay.parentPlugin.appendOutputData(h5File, useAllHistory=useAllHistory)
        self.exportConfiguration(file=file)  # save corresponding device settings in measurement file
        self.print(f'Saved {file.name}')
        self.pluginManager.Explorer.populateTree()

    def updateStaticPlot(self) -> None:
        """Update all staticPlots."""
        for staticDisplay in self.getActiveStaticDisplays():
            staticDisplay.plot()

    def exportConfiguration(self, file: 'Path | None' = None, useDefaultFile: bool = False) -> None:
        """Export configuration for multiple Devices and ChannelManagers.

        :param file: The file to add the configuration to, defaults to None
        :type file: pathlib.Path, optional
        :param useDefaultFile: Indicates if the default should be used, defaults to False
        :type useDefaultFile: bool, optional
        """
        for plugin in self.pluginManager.getPluginsByClass(ChannelManager):
            plugin.exportConfiguration(file=file, useDefaultFile=useDefaultFile)

    def initializeCommunication(self) -> None:
        """Initialize communication for all Devices and ChannelManagers."""
        for plugin in self.pluginManager.getPluginsByClass(ChannelManager):
            plugin.initializeCommunication()

    def globalUpdate(self, apply: bool = False, inout: INOUT = INOUT.BOTH) -> None:
        """Update channel values based on equations for all devices of provided type.

        :param apply: Indicates if values should be applied to devices, even if they have not changed. Defaults to False
        :type apply: bool, optional
        :param inout: Type of device to evaluate, defaults to INOUT.BOTH
        :type inout: INOUT, optional
        """
        # wait until all channels are complete before applying logic. will be called again when loading completed
        if any(device.loading for device in self.getDevices(inout)) or self.pluginManager.loading:
            return
        if inout in {INOUT.BOTH, INOUT.IN}:
            for device in self.getInputDevices():
                device.updateValues(apply=apply)
        if inout in {INOUT.BOTH, INOUT.OUT}:
            for device in self.getOutputDevices():
                device.updateValues()

    def storeOutputData(self) -> None:
        """Regularly stores device settings and data to minimize loss in the event of a program crash."""
        # * Make sure that no GUI elements are accessed when running from parallel thread!
        # * deamon=True is not used to prevent the unlikely case where the thread is terminated half way through because the program is closing.
        # * scan and plugin settings are already saved as soon as they are changing
        for device in cast('list[Device]', self.getDevices()):
            if device.recording:  # will be exported when program closes even if not recording, this is just for the regular exports while the program is running
                Thread(target=device.exportOutputData, kwargs={'useDefaultFile': True, 'useAllHistory': True}, name=f'{device.name} exportOutputDataThread').start()

    def restoreOutputData(self) -> None:
        """Restore all outputData for all Devices."""
        for device in cast('list[Device]', self.getDevices()):
            device.restoreOutputData()

    def wake(self) -> None:
        """Trigger keyboard action to prevent screen lock."""
        if getWakeMode():
            self.print('Keeping screen unlocked.', flag=PRINT.VERBOSE)
            pyautogui.press('volumedown')
            pyautogui.press('volumeup')

    @synchronized()
    def toggleRecording(self) -> None:
        """Toggle recording of data."""
        # Check for duplicate channel names before starting all devices.
        # Note that the same name can occur once as and input and once as an output even though this is discouraged.
        for inout, put in zip([INOUT.IN, INOUT.OUT], ['input', 'output'], strict=True):
            seen = set()
            dupes = [x for x in [channel.name for channel in self.channels(inout=inout)] if x in seen or seen.add(x)]
            if len(dupes) > 0:
                self.print(f"The following {put} channel names have been used more than once: {', '.join(dupes)}", flag=PRINT.WARNING)
        for plugin in self.pluginManager.getPluginsByClass(ChannelManager):
            if hasattr(plugin, 'recordingAction') and plugin.recordingAction is not None:
                plugin.toggleRecording(on=self.recordingAction.state, manual=False)

    def close(self) -> bool:  # noqa: D102
        response = super().close()
        self.storeTimer.stop()
        self.wakeTimer.stop()
        return response


class Notes(Plugin):
    """Add quick comments to a session or any other folder.

    The comments are saved in simple text files that are loaded automatically once a folder is opened again.
    They are intended to complement but not to replace a lab book.
    """

    name = 'Notes'
    pluginType = PLUGINTYPE.DISPLAY
    version = '1.0'
    iconFile = 'notes.png'

    def initGUI(self) -> None:  # noqa: D102
        super().initGUI()
        self.editor = TextEdit()
        self.editor.setFont(QFont('Courier', 10))
        self.numbers = NumberBar(parent=self.editor)
        lay = QHBoxLayout()
        lay.addWidget(self.numbers)
        lay.addWidget(self.editor)
        self.addContentLayout(lay)

    def saveData(self, file: Path, useDefaultFile: bool = False) -> None:
        """Add current notes to existing file.

        :param file: Path to save data.
        :type file: pathlib.Path
        :param useDefaultFile: Saves notes in text file in current folder or in scan file, defaults to False
        :type useDefaultFile: bool, optional
        """
        if self.initializedDock:
            if useDefaultFile:
                self.file = file / 'notes.txt'
                if self.editor.toPlainText():
                    with self.file.open('w', encoding=self.UTF8) as textFile:
                        textFile.write(self.editor.toPlainText())
            elif file.name.endswith(FILE_H5):
                with h5py.File(file, 'a', track_order=True) as h5file:
                    h5py.get_config().track_order = True
                    group = self.requireGroup(h5file, self.name)
                    group.attrs[Parameter.VALUE] = self.editor.toPlainText()

    def loadData(self, file: Path, showPlugin: bool = True) -> None:  # noqa: D102
        self.provideDock()
        self.editor.clear()
        self.file = file / 'notes.txt'
        if self.file.exists():  # load and display notes if found
            with self.file.open(encoding=self.UTF8) as dataFile:
                self.editor.insertPlainText(dataFile.read())
        verticalScrollBar = self.editor.verticalScrollBar()
        if verticalScrollBar:
            verticalScrollBar.triggerAction(QScrollBar.SliderAction.SliderToMinimum)   # scroll to top
        self.raiseDock(showPlugin)

    def updateTheme(self) -> None:  # noqa: D102
        super().updateTheme()
        self.numbers.updateTheme()


class Explorer(Plugin):  # noqa: PLR0904
    """Navigate all results and complementary data.

    All files can be accessed independently using the operating system
    file explorer, e.g., when working on a computer where *ESIBD Explorer*
    is not installed. However, the integrated explorer connects dedicated :ref:`sec:displays`
    to all files that were created with or are supported by *ESIBD
    Explorer*. All supported files are preceded with an icon that indicates
    which plugin will be used to display them. The :ref:`data path<data_path>`, current
    :ref:`session path<sec:session_settings>`, and a search bar are accessible directly from here. File system
    links or shortcuts are supported as well.

    The displays were made to simplify data analysis and documentation.
    They use dedicated and customizable views that allow saving images as
    files or sending them to the clipboard for sharing or documentation in a
    lab book. Right clicking supported files opens a context menu that allows
    to load settings and configurations directly. For example, a scan file
    does not only contain the scan data, but also allows to inspect and
    restore all experimental settings used to record it. Note that the
    context menu only allows to load device values, but the files contain
    the entire device configuration. To restore the device configuration
    based on a scan file, import the file from the device toolbar. A double
    click will open the file in the external default program.
    Use third party tools like `HDFView <https://www.hdfgroup.org/downloads/hdfview/>`_
    to view *.hdf* files independently.

    The explorer may also be useful for other applications beyond managing
    experimental data. For example, if you organize the documentation of the
    experimental setup in folders following the hierarchy of components and sub
    components, it allows you to quickly find the corresponding manuals and
    order numbers. In combination with the :ref:`sec:notes` plugin, you can add comments to
    each component that will be displayed automatically as soon as you
    enter the corresponding folder.
    """

    documentation = """The integrated file explorer is used to navigate all results and
    complementary data. All files can be accessed independently using the operating system
    file explorer, e.g., when working on a computer where ESIBD Explorer
    is not installed. However, the integrated explorer connects dedicated displays
    to all files that were created with or are supported by ESIBD
    Explorer. All supported files are preceded with an icon that indicates
    which plugin will be used to display them. The data path, current
    session_settings, and a search bar are accessible directly from here. File system
    links or shortcuts are supported as well.

    The displays were made to simplify data analysis and documentation.
    They use dedicated and customizable views that allow saving images as
    files or sending them to the clipboard for sharing or documentation in a
    lab book. Right clicking supported files opens a context menu that allows
    to load settings and configurations directly. For example, a scan file
    does not only contain the scan data, but also allows to inspect and
    restore all experimental settings used to record it. Note that the
    context menu only allows to load device values, but the files contain
    the entire device configuration. To restore the device configuration
    based on a scan file, import the file from the device toolbar. A double
    click will open the file in the external default program.
    Use third party tools like HDFView
    to view .hdf files independently.

    The explorer may also be useful for other applications beyond managing
    experimental data. For example, if you organize the documentation of the
    experimental setup in folders following the hierarchy of components and sub
    components, it allows you to quickly find the corresponding manuals and
    order numbers. In combination with the Notes plugin, you can add comments to
    each component that will be displayed automatically as soon as you
    enter the corresponding folder.
    """

    name = 'Explorer'
    version = '1.0'
    pluginType = PLUGINTYPE.CONTROL
    optional = False
    iconFile = 'folder.png'
    displayContentSignal = pyqtSignal()

    def __init__(self, **kwargs) -> None:
        """Initialize the central Explorer."""
        super().__init__(**kwargs)
        self.previewFileTypes: list[str] = ['.lnk']
        self.ICON_FOLDER = self.makeCoreIcon('folder.png')
        self.ICON_HOME = self.makeCoreIcon('home.png')
        self.ICON_SESSION = self.makeCoreIcon('book-open-bookmark.png')
        self.ICON_DOCUMENT = self.makeCoreIcon('document.png')
        self.ICON_BACKWARD = self.makeCoreIcon('arrow-180.png')
        self.ICON_FORWARD = self.makeCoreIcon('arrow.png')
        self.ICON_UP = self.makeCoreIcon('arrow-090.png')
        self.ICON_BACKWARD_GRAY = self.makeCoreIcon('arrow_gray-180.png')
        self.ICON_FORWARD_GRAY = self.makeCoreIcon('arrow_gray.png')
        self.ICON_UP_GRAY = self.makeCoreIcon('arrow_gray-090.png')
        self.ICON_REFRESH = self.makeCoreIcon('arrow-circle-315.png')
        self.ICON_BROWSE = self.makeCoreIcon('folder-horizontal-open.png')
        self.activeFileFullPath = None
        self.history = []
        self.indexHistory = 0
        self.root = None
        self.notesFile = None
        self.displayContentSignal.connect(self.displayContent)
        self.populating = False
        self.loadingContent = False

    def initGUI(self) -> None:  # noqa: D102
        super().initGUI()
        if not self.titleBar:
            return
        self.tree = TreeWidget()
        self.addContentWidget(self.tree)
        self.tree.currentItemChanged.connect(self.treeItemClicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.initExplorerContextMenu)
        self.tree.itemDoubleClicked.connect(self.treeItemDoubleClicked)
        self.tree.itemExpanded.connect(self.expandDir)
        self.tree.setHeaderHidden(True)

        self.backAction = self.addAction(event=self.backward, toolTip='Backward', icon=self.ICON_BACKWARD)
        self.forwardAction = self.addAction(event=self.forward, toolTip='Forward', icon=self.ICON_FORWARD)
        self.upAction = self.addAction(event=self.up, toolTip='Up', icon=self.ICON_UP)
        self.refreshAction = self.addAction(event=lambda: self.populateTree(clear=False), toolTip='Refresh', icon=self.ICON_REFRESH)
        self.dataPathAction = self.addAction(event=self.goToDataPath, toolTip='Go to data path.', icon=self.ICON_HOME)

        self.currentDirLineEdit = QLineEdit()
        self.currentDirLineEdit.returnPressed.connect(self.updateCurDirFromLineEdit)
        self.currentDirLineEdit.setMinimumWidth(50)
        self.currentDirLineEdit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.titleBar.addWidget(self.currentDirLineEdit)

        self.browseAction = self.addAction(event=self.browseDir, toolTip='Select folder.', icon=self.ICON_BROWSE)
        self.sessionAction = self.addAction(event=self.goToCurrentSession, toolTip='Go to current session.', icon=self.ICON_SESSION)

        self.filterLineEdit = QLineEdit()
        self.filterLineEdit.setMaximumWidth(100)
        self.filterLineEdit.setMinimumWidth(50)
        self.filterLineEdit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.searchTimer = QTimer()
        self.searchTimer.timeout.connect(lambda: self.populateTree(clear=False))
        self.searchTimer.setSingleShot(True)
        self.searchTimer.setInterval(200)
        self.filterLineEdit.textChanged.connect(self.searchTimer.start)
        self.filterLineEdit.setPlaceholderText('Search')
        self.titleBar.addWidget(self.filterLineEdit)

    def finalizeInit(self) -> None:  # noqa: D102
        # Load directory after all other plugins loaded, to allow use icons for supported files
        self.updateRoot(self.pluginManager.Settings.dataPath, addHistory=True, loading=True)  # do not trigger populate tree here, will be done when updating theme
        super().finalizeInit()
        self.stretch.deleteLater()

    def runTestParallel(self) -> None:  # noqa: D102
        for action in [self.sessionAction, self.upAction, self.backAction, self.forwardAction, self.dataPathAction, self.refreshAction]:
            if not self.testing:
                break
            self.populating = True
            self.testControl(action, value=True)
            # populateTree can take longer if there are many folders and files
            self.waitForCondition(condition=lambda: not self.populating, timeoutMessage=f'testing {action.objectName()}', timeout=15)
            # NOTE: using self.populating flag makes sure further test are only run after populating has completed. using locks and signals is more error prone
        testDir = self.pluginManager.Settings.dataPath / 'test_files'
        if testDir.exists():
            for file in testDir.iterdir():
                if not file.is_dir():
                    if not self.testing:
                        break
                    self.print(f'Loading file {shorten_text(file.name, 50)}.')
                    self.activeFileFullPath = file
                    self.displayContentSignal.emit()  # call displayContent in main thread
                    self.loadingContent = True
                    self.waitForCondition(condition=lambda: not self.loadingContent, timeoutMessage=f'displaying content of {self.activeFileFullPath.name}')
        else:
            self.print(f'Could not find {testDir.as_posix()}. Please create and fill with files that should be loaded during testing.', flag=PRINT.WARNING)
        super().runTestParallel()

    def loadData(self, file: Path, showPlugin: bool = True) -> None:  # noqa: D102
        self.provideDock()
        target = Path()
        if sys.platform == 'Linux':
            target = Path(os.path.realpath(str(file)))
        elif sys.platform == 'win32':
            shell = win32com.client.Dispatch('WScript.Shell')
            target = Path(shell.CreateShortCut(str(file)).Targetpath)
        if target.is_dir():
            self.updateRoot(target, addHistory=True)
        self.raiseDock(showPlugin)

    def LOADSETTINGS(self, plugin: Plugin) -> str:
        """Load settings message for context menus.

        :param plugin: The plugin for which settings should be loaded.
        :type plugin: esibd.plugins.Plugin
        :return: Load settings message.
        :rtype: str
        """
        if plugin.pluginType in {PLUGINTYPE.INPUTDEVICE, PLUGINTYPE.OUTPUTDEVICE}:
            # currently channels can only be loaded explicitly from the toolbar in advanced mode.
            # this is to prevent changing them by accident and make the context menu more concise as this function is rarely used.
            return f'Load {plugin.name} channels.'
        # PLUGINSCAN, ...
        return f'Load {plugin.name} settings.'

    LOADALLVALUES = 'Load all device values.'

    def initExplorerContextMenu(self, pos: QPoint) -> None:  # noqa: C901, PLR0912, PLR0914, PLR0915
        """Context menu for items in Explorer.

        :param pos: The position where the context menu should be created.
        :type pos: QPoint
        """
        item = cast('TreeWidgetItem', self.tree.itemAt(pos))
        itemFullPath = self.getItemFullPath(item)
        if not item:
            return
        openDirAction = None
        openContainingDirAction = None
        openFileAction = None
        deleteFileAction = None
        copyFileNameAction = None
        copyFullPathAction = None
        copyFolderNameAction = None
        runPythonCodeAction = None
        copyPlotCodeAction = None
        loadValuesActions = []
        loadSettingsActions = []
        explorerContextMenu = QMenu(self.tree)
        if itemFullPath and itemFullPath.is_dir():  # actions for folders
            openDirAction = explorerContextMenu.addAction('Open folder in file explorer.')
            deleteFileAction = explorerContextMenu.addAction('Move folder to recycle bin.')
            copyFolderNameAction = explorerContextMenu.addAction('Copy folder name to clipboard.')
        elif self.activeFileFullPath:
            openContainingDirAction = explorerContextMenu.addAction('Open containing folder in file explorer.')
            openFileAction = explorerContextMenu.addAction('Open with default program.')
            copyFileNameAction = explorerContextMenu.addAction('Copy file name to clipboard.')
            copyFullPathAction = explorerContextMenu.addAction('Copy full file path to clipboard.')
            deleteFileAction = explorerContextMenu.addAction('Move to recycle bin.')
            if self.activeFileFullPath.suffix == FILE_H5:
                try:
                    with h5py.File(name=self.activeFileFullPath, mode='r') as h5File:
                        loadValuesActions.extend(explorerContextMenu.addAction(device.LOADVALUES) for device
                                                 in self.pluginManager.DeviceManager.getDevices() if device.name in h5File
                                                 and device.pluginType == PLUGINTYPE.INPUTDEVICE)
                        if len(loadValuesActions) > 1:
                            loadAllValuesAction = QAction(self.LOADALLVALUES)
                            explorerContextMenu.insertAction(loadValuesActions[0], loadAllValuesAction)
                            loadValuesActions.insert(0, loadAllValuesAction)
                        # not used very frequently for devices -> only show for scans
                        loadSettingsActions.extend(explorerContextMenu.addAction(self.LOADSETTINGS(plugin)) for plugin
                                                   in self.pluginManager.getMainPlugins() if plugin.pluginType == PLUGINTYPE.SCAN
                                                   and plugin.name in h5File)
                except OSError:
                    self.print(f'Could not identify file type of {self.activeFileFullPath.name}', flag=PRINT.ERROR)

                for device in self.pluginManager.DeviceManager.getDevices():
                    if device.liveDisplay and device.liveDisplay.supportsFile(self.activeFileFullPath):
                        copyPlotCodeAction = explorerContextMenu.addAction(f'Generate {device.name} plot file.')
                        break  # only use first match
                for scan in self.pluginManager.getPluginsByClass(Scan):
                    if scan.supportsFile(self.activeFileFullPath):
                        copyPlotCodeAction = explorerContextMenu.addAction(f'Generate {scan.name} plot file.')
                        break  # only use first match
            elif self.activeFileFullPath.suffix == FILE_INI:
                confParser = configparser.ConfigParser()
                try:
                    confParser.read(self.activeFileFullPath)
                    fileType = confParser[INFO][Parameter.NAME]
                except KeyError:
                    self.print(f'Could not identify file type of {self.activeFileFullPath.name}', flag=PRINT.ERROR)
                else:  # no exception
                    if fileType == self.pluginManager.Settings.name:
                        loadSettingsActions.append(explorerContextMenu.addAction(self.pluginManager.Settings.loadGeneralSettings))
                    else:
                        loadValuesActions.extend(explorerContextMenu.addAction(device.LOADVALUES) for device
                                                 in self.pluginManager.DeviceManager.getDevices(inout=INOUT.IN) if device.name == fileType)
            elif self.activeFileFullPath.suffix == FILE_PY:
                runPythonCodeAction = explorerContextMenu.addAction('Run file in python.')
            else:
                for display in self.pluginManager.getPluginsByType(PLUGINTYPE.DISPLAY):
                    if display.supportsFile(self.activeFileFullPath) and display.generatePythonPlotCode():
                        copyPlotCodeAction = explorerContextMenu.addAction(f'Generate {display.name} plot file.')
                        break  # only use first match

        explorerContextMenuAction = explorerContextMenu.exec(self.tree.mapToGlobal(pos))
        if explorerContextMenuAction:  # noqa: PLR1702
            if explorerContextMenuAction is openDirAction and itemFullPath:
                openInDefaultApplication(itemFullPath)
            elif explorerContextMenuAction is copyFolderNameAction and itemFullPath:
                pyperclip.copy(itemFullPath.name)
            elif explorerContextMenuAction is deleteFileAction:
                send2trash(cast('TreeWidgetItem', self.tree.selectedItems()[0]).path_info)
                self.populateTree(clear=False)
            elif self.activeFileFullPath:
                if explorerContextMenuAction is runPythonCodeAction:
                    self.pluginManager.Console.execute(command=f"Module = dynamicImport('ModuleName', '{self.activeFileFullPath.as_posix()}')")
                elif explorerContextMenuAction is openContainingDirAction:
                    openInDefaultApplication(self.activeFileFullPath.parent)
                elif explorerContextMenuAction is openFileAction:
                    openInDefaultApplication(self.activeFileFullPath)
                elif explorerContextMenuAction is copyFileNameAction:
                    pyperclip.copy(self.activeFileFullPath.name)
                elif explorerContextMenuAction is copyFullPathAction:
                    pyperclip.copy(self.activeFileFullPath.as_posix())
                elif explorerContextMenuAction is copyPlotCodeAction:
                    for device in self.pluginManager.DeviceManager.getDevices():
                        if device.liveDisplay and device.staticDisplay and device.liveDisplay.supportsFile(self.activeFileFullPath):
                            with self.activeFileFullPath.with_suffix('.py').open('w', encoding=UTF8) as plotFile:
                                plotFile.write(device.staticDisplay.generatePythonPlotCode())
                            self.populateTree(clear=False)
                            break  # only use first match
                    for scan in self.pluginManager.getPluginsByClass(Scan):
                        if scan.supportsFile(self.activeFileFullPath):
                            with self.activeFileFullPath.with_suffix('.py').open('w', encoding=UTF8) as plotFile:
                                plotFile.write(scan.generatePythonPlotCode())
                            self.populateTree(clear=False)
                            break  # only use first match
                    for display in self.pluginManager.getPluginsByType(PLUGINTYPE.DISPLAY):
                        if display not in {self.pluginManager.Tree, self.pluginManager.Text} and display.supportsFile(self.activeFileFullPath) and display.generatePythonPlotCode():
                            with self.activeFileFullPath.with_suffix('.py').open('w', encoding=UTF8) as plotFile:
                                plotFile.write(display.generatePythonPlotCode())
                            self.populateTree(clear=False)
                            break  # only use first match
                elif explorerContextMenuAction in loadSettingsActions:
                    for plugin in self.pluginManager.getMainPlugins():
                        if explorerContextMenuAction.text() == self.LOADSETTINGS(plugin) and isinstance(plugin, Scan):
                            plugin.loadSettings(file=self.activeFileFullPath)
                if explorerContextMenuAction in loadValuesActions:
                    if explorerContextMenuAction.text() == self.pluginManager.Settings.loadGeneralSettings:
                        self.pluginManager.Settings.loadSettings(file=self.activeFileFullPath)
                    elif explorerContextMenuAction.text() == self.LOADALLVALUES:
                        first = True
                        with h5py.File(name=self.activeFileFullPath, mode='r') as h5File:
                            for device in self.pluginManager.DeviceManager.getDevices():
                                if device.name in h5File and device.pluginType == PLUGINTYPE.INPUTDEVICE:
                                    device.loadValues(self.activeFileFullPath, append=not first)
                                    first = False
                    else:
                        for device in self.pluginManager.DeviceManager.getDevices(inout=INOUT.IN):
                            if explorerContextMenuAction.text() == device.LOADVALUES:
                                device.loadValues(self.activeFileFullPath)

    def treeItemDoubleClicked(self, item: TreeWidgetItem, column: int) -> None:  # noqa: ARG002  # pylint: disable = missing-param-doc
        """Open dir or opens file in external default program on double click.

        :param item: The item representing a file or directory.
        :type item: QTreeWidgetItem
        """
        fullItemPath = self.getItemFullPath(item)
        if not self.populating:
            if fullItemPath and fullItemPath.is_dir():
                self.updateRoot(fullItemPath, addHistory=True)
            elif self.activeFileFullPath:
                openInDefaultApplication(self.activeFileFullPath)
        elif fullItemPath:
            self.print(f'Ignoring double click on {fullItemPath.name} while populating tree.', flag=PRINT.WARNING)

    def getItemFullPath(self, item: 'TreeWidgetItem | None') -> Path | None:
        """Return full path to item.

        :param item: The item representing a file or directory.
        :type item: QTreeWidgetItem
        :return: Corresponding full path.
        :rtype: Path
        """
        if item and self.root:
            out = item.text(0)
            parent = cast('TreeWidgetItem', item.parent())
            fullParentPath = self.getItemFullPath(parent)
            return fullParentPath / out if parent and fullParentPath else self.root / out
        return None

    def up(self) -> None:
        """Navigate to parent directory."""
        if self.root:
            newRoot = Path(self.root).parent.resolve()
            self.updateRoot(newRoot, addHistory=True)

    def forward(self) -> None:
        """Navigate forwards in directory history."""
        self.indexHistory = min(self.indexHistory + 1, len(self.history) - 1)
        self.updateRoot(self.history[self.indexHistory])

    def backward(self) -> None:
        """Navigate backwards in directory history."""
        self.indexHistory = max(self.indexHistory - 1, 0)
        self.updateRoot(self.history[self.indexHistory])

    def updateRoot(self, newRoot: Path, addHistory: bool = False, loading: bool = False) -> None:
        """Update the root directory.

        :param newRoot: The new root directory.
        :type newRoot: str, pathlib.Path
        :param addHistory: Indicates if the path will be accessible later through the navigation buttons. Defaults to False
        :type addHistory: bool, optional
        :param loading: Skip GUI updates during loading and initialization. Defaults to False
        :type loading: bool, optional
        """
        self.rootChanging(self.root, newRoot)
        self.root = Path(newRoot)
        if addHistory:
            del self.history[self.indexHistory + 1:]  # remove voided forward options
            self.history.append(self.root)
            self.indexHistory = len(self.history) - 1
        self.currentDirLineEdit.setText(self.root.as_posix())
        if not loading:
            self.populateTree(clear=True)

    @synchronized()
    def populateTree(self, clear: bool = False) -> None:  # noqa: C901, PLR0912
        """Populate or updates fileTree.

        :param clear: If True all items will be deleted and new items will be created from scratch. Defaults to False
        :type clear: bool, optional
        """
        if self.pluginManager.closing or not self.root:
            return
        self.populating = True
        for action in [self.backAction, self.forwardAction, self.upAction, self.refreshAction]:
            action.setEnabled(False)
        if clear:  # otherwise existing tree will be updated (much more efficient)
            self.tree.clear()
        # update navigation arrows
        if self.indexHistory == len(self.history) - 1:
            self.forwardAction.setIcon(self.ICON_FORWARD_GRAY)
        else:
            self.forwardAction.setIcon(self.ICON_FORWARD)
        if self.indexHistory == 0:
            self.backAction.setIcon(self.ICON_BACKWARD_GRAY)
        else:
            self.backAction.setIcon(self.ICON_BACKWARD)
        if self.root.parent == self.root:  # no parent
            self.upAction.setIcon(self.ICON_UP_GRAY)
        else:
            self.upAction.setIcon(self.ICON_UP)
        invisibleRootItem = self.tree.invisibleRootItem()
        if invisibleRootItem:
            self.load_project_structure(startPath=self.root, tree=invisibleRootItem, search_term=self.filterLineEdit.text(), clear=clear)  # populate tree widget

        it = QTreeWidgetItemIterator(self.tree, QTreeWidgetItemIterator.IteratorFlag.HasChildren)
        while it.value():
            value = cast('TreeWidgetItem', it.value())
            if value and value.isExpanded():
                # populate expanded dirs, independent of recursion depth
                self.load_project_structure(startPath=value.path_info, tree=value, search_term=self.filterLineEdit.text(), clear=clear)
            it += 1
        self.populating = False
        for action in [self.backAction, self.forwardAction, self.upAction, self.refreshAction]:
            action.setEnabled(True)

    def browseDir(self) -> None:
        """Set path selected from file dialog as new root directory."""
        if self.root:
            newPath = Path(QFileDialog.getExistingDirectory(parent=None, caption=SELECTPATH, directory=self.root.as_posix(),
                                                            options=QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks))
            if newPath != Path():
                self.updateRoot(newPath, addHistory=True)

    def goToCurrentSession(self) -> None:
        """Set current session path as new root directory."""
        self.updateRoot(self.pluginManager.Settings.getFullSessionPath(), addHistory=True)

    def goToDataPath(self) -> None:
        """Set data path as new root directory."""
        self.updateRoot(self.pluginManager.Settings.dataPath, addHistory=True)

    def updateCurDirFromLineEdit(self) -> None:
        """Update root directory after manually editing path in currentDirLineEdit."""
        path = Path(self.currentDirLineEdit.text())
        if path.exists():
            self.updateRoot(path, addHistory=True)
        else:
            self.print(f'Could not find directory: {path}', flag=PRINT.ERROR)

    def treeItemClicked(self, item: TreeWidgetItem) -> None:
        """Display content when clicking on a file.

        :param item: The item representing the file.
        :type item: QTreeWidgetItem
        """
        itemFullPath = self.getItemFullPath(item)
        if item and itemFullPath and not itemFullPath.is_dir():
            if self.loadingContent and self.activeFileFullPath:
                self.print(f'Ignoring {itemFullPath.name} while loading {self.activeFileFullPath.name}', flag=PRINT.WARNING)
                return
            self.activeFileFullPath = itemFullPath
            self.displayContent()
        # NOTE: double click to extend is implemented separately

    def displayContent(self) -> None:
        """General wrapper for handling of files with different format.

        If a file format supported by a plugin is detected (including backwards compatible formats) the data will be loaded and shown in the corresponding view.
        Handling for a few general formats is implemented as well.
        For text based formats the text is also shown in the Text tab for quick access if needed.
        The actual handling is redirected to dedicated methods.
        """
        if self.populating or not self.activeFileFullPath:  # avoid trigger display during filtering
            return
        self.loadingContent = True  # avoid changing activeFileFullPath while previous file is still loading
        handled = False
        try:
            for plugin in [plugin for plugin in self.pluginManager.plugins if plugin.supportsFile(self.activeFileFullPath)]:
                message = f'displayContent {self.activeFileFullPath.name} using {plugin.name}'
                self.print(shorten_text(message, 86), flag=PRINT.DEBUG)
                plugin.loadData(file=self.activeFileFullPath, showPlugin=not handled)  # after widget is visible to make sure it is drawn properly
                handled = True  # display first widget that supports file (others like tree or text come later and are optional)
            if not handled:
                message = f'No preview available for this type of {self.activeFileFullPath.suffix} file. Consider activating, implementing, or requesting a plugin.'
                self.print(message)
                self.pluginManager.Text.setText(message, showPlugin=True)
        finally:
            self.loadingContent = False

    def load_project_structure(self, startPath: Path, tree: QTreeWidgetItem, search_term: str = '', recursionDepth: int = 2, clear: bool = False) -> None:  # noqa: C901, PLR0912
        # based on https://stackoverflow.com/questions/5144830/how-to-create-folder-view-in-pyqt-inside-main-window
        """Recursively map the file structure into the internal Explorer.

        Data from multiple sessions can be accessed from the data path level by expanding the tree.

        :param startPath: The new root directory. The Explorer will show all directories and files contained inside it.
        :type startPath: pathlib.Path
        :param tree: The tree that will be used to display directories and files.
        :type tree: QTreeWidgetItem
        :param search_term: Content will be filtered using this.
        :type search_term: str
        :param recursionDepth: How many levels will be populated. Further levels will be populated as they are expanded.
        recursionDepth of more than 2 can lead to very long loading times. Defaults to 2
        :type recursionDepth: int, optional
        :param clear: If True, all items will be recreated from scratch, otherwise existing items will be updated. Defaults to False
        :type clear: bool, optional
        """
        self.processEvents()
        # self.tree.update() does not update GUI before completion of event loop
        if recursionDepth == 0:  # limit depth to avoid indexing entire storage (can take minutes)
            return
        recursionDepth -= 1
        if startPath.is_dir():
            # List of directories only
            dirlist = []
            for x in startPath.iterdir():
                try:
                    if (startPath / x).is_dir() and not any(x.name.startswith(sym) for sym in ['.', '$']):
                        list((startPath / x).iterdir())  # pylint: disable = expression-not-assigned  # raises PermissionError if access is denied, need to use iterator to trigger access
                        dirlist.append(x)
                except PermissionError as e:
                    self.print(f'{e}')
                    continue  # skip directories that we cannot access
            # List of files only
            filelist = [x for x in startPath.iterdir() if not (startPath / x).is_dir() and not x.name.startswith('.')]

            children = [tree.child(i) for i in range(tree.childCount())]  # list of existing children
            children_text = [child.text(0) for child in children if child]
            for element in dirlist:  # add all dirs first, then all files
                path_info = startPath / element
                if element.name in children_text:  # reuse existing
                    parent_itm = tree.child(children_text.index(element.name))
                else:  # add new
                    parent_itm = TreeWidgetItem(tree, [element.name])
                    parent_itm.path_info = path_info
                    parent_itm.setIcon(0, self.ICON_FOLDER)
                if parent_itm:
                    self.load_project_structure(startPath=path_info, tree=parent_itm, search_term=search_term, recursionDepth=recursionDepth, clear=clear)
            for element in [element for element in filelist if ((not search_term or search_term.lower() in element.name.lower()) and element.name not in children_text)]:
                # don't add files that do not match search_term and only add elements that do not exist already
                if clear:  # add all items alphabetically
                    parent_itm = TreeWidgetItem(tree, [element.name])
                else:  # insert new items at alphabetically correct position
                    parent_itm = TreeWidgetItem(None, [element.name])
                    index = next((children_text.index(child_text) for child_text in children_text if child_text > element.name), len(children_text))
                    tree.insertChild(index, parent_itm)
                    children_text.insert(index, element.name)
                if parent_itm:
                    parent_itm.path_info = startPath / element
                    parent_itm.setIcon(0, self.getFileIcon(element))
            for child in children:
                if child:
                    if not (startPath / child.text(0)).exists():
                        tree.removeChild(child)  # remove if does not exist anymore
                    if (startPath / child.text(0)).is_file() and search_term and search_term.lower() not in child.text(0).lower():
                        tree.removeChild(child)  # remove files if they do not match filter
        else:
            self.print(f'{startPath} is not a valid directory', flag=PRINT.ERROR)

    def getFileIcon(self, file: Path) -> Icon:
        """Get the icon of the plugin that will handle the file.

        :param file: The file path.
        :type file: pathlib.Path
        :return: The corresponding icon.
        :rtype: esibd.core.Icon
        """
        plugin = next((plugin for plugin in self.pluginManager.plugins if plugin.supportsFile(file)
                       if plugin not in {self.pluginManager.Tree, self.pluginManager.Text}), None)
        if not plugin:  # only use Tree or Text if no other supporting Plugin has been found
            plugin = next((plugin for plugin in {self.pluginManager.Tree, self.pluginManager.Text} if plugin.supportsFile(file)), None)
        if plugin:
            return plugin.getIcon()
        return self.ICON_DOCUMENT

    def expandDir(self, _dir: TreeWidgetItem) -> None:
        """Load directory content and expands it.

        :param _dir: The directory to be expanded.
        :type _dir: TreeWidgetItem
        """
        self.load_project_structure(startPath=_dir.path_info, tree=_dir, search_term=self.filterLineEdit.text())
        _dir.setExpanded(True)

    def rootChanging(self, oldRoot: 'Path | None', newRoot: 'Path | None') -> None:
        """Handle notes when rood directory changes.

        :param oldRoot: Old root directory.
        :type oldRoot: pathlib.Path
        :param newRoot: New root directory.
        :type newRoot: pathlib.Path
        """
        if hasattr(self.pluginManager, 'Notes'):
            # save old notes
            if oldRoot:
                self.pluginManager.Notes.saveData(oldRoot, useDefaultFile=True)
            if newRoot:  # None on program closing
                self.pluginManager.Notes.loadData(newRoot, showPlugin=False)

    def close(self) -> bool:  # noqa: D102
        response = super().close()
        self.rootChanging(self.pluginManager.Explorer.root, None)
        return response

    def updateTheme(self) -> None:  # noqa: D102
        super().updateTheme()
        self.populateTree(clear=True)


class UCM(ChannelManager):
    """Allow to specify a custom list of channels from all :class:`devices<esibd.plugins.Device>`.

    This allows to have the most relevant controls and information in one place.
    All logic remains within the corresponding device plugins. This is just an interface!
    To get started, simply add channels and name them after existing channels from other devices.
    """

    documentation = """Unified Channel Manager (UCM) allows to specify a custom list of channels from all devices.
    This allows to have the most relevant controls and information in one place.
    All logic remains within the corresponding device plugins. This is just an interface!
    To get started, simply add channels and name them after existing channels from other devices.
    """

    name = 'UCM'
    version = '1.0'
    pluginType = PLUGINTYPE.CHANNELMANAGER
    optional = True
    inout = INOUT.NONE
    maxDataPoints = 0  # UCM channels do not store data
    useMonitors = True
    iconFile = 'UCM.png'

    channels: list['UCMChannel']

    class UCMChannel(RelayChannel, Channel):
        """Minimal UI for abstract channel."""

        DEVICE = 'Device'

        def __init__(self, channelParent: ChannelManager | Scan, tree: 'QTreeWidget | None' = None) -> None:  # noqa: D107
            super().__init__(channelParent, tree)
            self.invalid_chars = ['/']  # keep . as valid character to allow referencing recorded parameters of other channels

        def connectSource(self, giveFeedback: bool = False) -> None:  # noqa: C901, PLR0912, PLR0915
            """Connect the sourceChannel.

            :param giveFeedback: Report on success of connection, defaults to False
            :type giveFeedback: bool, optional
            """
            self.removeEvents()  # free up previously used channel if applicable
            channel_name = self.name
            parameter_name = ''
            if '.' in self.name:
                channel_name = self.name.split('.')[0]
                parameter_name = self.name.split('.')[1]  # if this is defined, UCM will not connect to the value but to the specified parameter of the sourceChannel
            sources = [channel for channel in self.pluginManager.DeviceManager.channels(inout=INOUT.ALL)
                       if channel not in self.channelParent.getChannels()
                       and channel.name.strip().lower() == channel_name.strip().lower()
                       and (not parameter_name or (channel.getParameterByName(parameter_name) is not None))]
            devicePushButton = cast('QPushButton', self.getParameterByName(self.DEVICE).getWidget())
            value = self.getParameterByName(self.VALUE)
            monitor = self.getParameterByName(self.MONITOR)
            display = self.getParameterByName(self.DISPLAY)
            display.setVisible(True)

            if len(sources) > 0:
                self.sourceParameter = sources[0].getParameterByName(parameter_name) if parameter_name else None
                if self.sourceParameter and self.sourceParameter.parameterType not in {PARAMETERTYPE.INT, PARAMETERTYPE.FLOAT, PARAMETERTYPE.EXP}:
                    self.print(f'Cannot link to parameter {parameter_name} on channel {channel_name} as only parameters of type INT, FLOAT, and EXP are supported for linking.')
                    sources = []  # Do not link

            if len(sources) == 0:
                self.sourceChannel = None
                self.getValues = lambda *_, **__: None
                self.notes = f'Could not find {self.name}'
                devicePushButton.setIcon(self.channelParent.makeCoreIcon('help_large_dark.png' if getDarkMode() else 'help_large.png'))
                devicePushButton.setToolTip('Source: Unknown')
                value.setVisible(False)  # value not needed (no setValues)
                monitor.setVisible(False)  # monitor not needed
            else:
                self.sourceChannel = sources[0]
                self.sourceParameter = self.sourceChannel.getParameterByName(parameter_name) if parameter_name else None
                devicePushButton.setIcon(self.sourceChannel.getIcon())
                devicePushButton.setToolTip(f'Source: {self.sourceChannel.getDevice().name}.{parameter_name}' if parameter_name else
                                             f'Source: {self.sourceChannel.getDevice().name}')
                full_source = (f'Source: {self.sourceChannel.getDevice().name}.{self.sourceChannel.name}.{parameter_name}' if parameter_name else
                              f'Source: {self.sourceChannel.getDevice().name}.{self.sourceChannel.name}')
                self.notes = full_source
                if len(sources) > 1:
                    self.print(f'More than one match for {self.name}. Using {full_source}. Use unique names to avoid this.', flag=PRINT.WARNING)
                if self.sourceParameter and self.sourceParameter.recorded:
                    self.getValues = self.sourceParameter.getValues
                else:
                    self.getValues = lambda **kwargs: (self.sourceChannel.convertDataDisplay(self.sourceChannel.getValues(**kwargs))
                                                       if self.sourceChannel and self.sourceChannel.convertDataDisplay else None)
                device = self.sourceChannel.getDevice()
                if hasattr(self.sourceChannel, self.UNIT.lower()):
                    # NOTE: use actual units as used by device consistently in channels and relay channels. Use display unit / convertDataDisplay in liveDisplays if defined
                    self.unit = self.sourceChannel.unit  # self.sourceChannel.getDisplayUnit()
                elif isinstance(device, ChannelManager):
                    self.unit = device.unit  # device.getDisplayUnit() if isinstance(device, Device) else device.unit
                else:
                    self.unit = ''
                if self.sourceParameter:
                    self.unit = self.sourceParameter.unit
                    if not self.sourceParameter.recorded:
                        display.value = False
                    display.setVisible(self.sourceParameter.recorded)
                    if self.sourceParameter.indicator:
                        monitor.parameterType = self.sourceParameter.parameterType
                        monitor.applyWidget()
                        value.setVisible(False)  # value not needed (no setValues)
                        monitor.setVisible(True)
                    else:
                        value.parameterType = self.sourceParameter.parameterType
                        value.indicator = False
                        value.min, value.max = self.sourceParameter.min, self.sourceParameter.max
                        value.applyWidget()
                        value.setVisible(True)
                        monitor.setVisible(False)
                    self.sourceParameter.extraEvents.append(self.relayValueEvent)
                elif self.sourceChannel.useMonitors:
                    # show value and monitor
                    monitor.parameterType = self.sourceChannel.getParameterByName(self.MONITOR).parameterType
                    monitor.applyWidget()
                    if self.sourceChannel.real:
                        self.sourceChannel.getParameterByName(self.MONITOR).extraEvents.append(self.relayMonitorEvent)
                    value.parameterType = self.sourceChannel.getParameterByName(self.VALUE).parameterType
                    value.indicator = self.sourceChannel.getParameterByName(self.VALUE).indicator
                    if self.MIN in self.sourceChannel.displayedParameters:
                        value.min = cast('float | None', self.sourceChannel.getParameterByName(self.MIN).value)
                        value.max = cast('float | None', self.sourceChannel.getParameterByName(self.MAX).value)
                    value.applyWidget()
                    self.sourceChannel.getParameterByName(self.VALUE).extraEvents.append(self.relayValueEvent)
                    value.setVisible(True)
                    monitor.setVisible(self.sourceChannel.real)
                elif self.sourceChannel.inout == INOUT.OUT:
                    # only show value as monitor
                    monitor.parameterType = self.sourceChannel.getParameterByName(self.VALUE).parameterType
                    monitor.applyWidget()
                    self.sourceChannel.getParameterByName(self.VALUE).extraEvents.append(self.relayValueEvent)
                    value.setVisible(False)  # value not needed (no setValues)
                    monitor.setVisible(True)
                else:
                    value.parameterType = self.sourceChannel.getParameterByName(self.VALUE).parameterType
                    value.indicator = self.sourceChannel.getParameterByName(self.VALUE).indicator
                    if self.MIN in self.sourceChannel.displayedParameters:
                        value.min = cast('float | None', self.sourceChannel.getParameterByName(self.MIN).value)
                        value.max = cast('float | None', self.sourceChannel.getParameterByName(self.MAX).value)
                    value.applyWidget()
                    self.sourceChannel.getParameterByName(self.VALUE).extraEvents.append(self.relayValueEvent)
                    value.setVisible(True)
                    monitor.setVisible(False)  # monitor not needed

                self.getSourceChannelValues()
                for parameterName in [self.LINEWIDTH, self.LINESTYLE, self.COLOR]:
                    if parameterName in self.sourceChannel.displayedParameters:
                        self.sourceChannel.getParameterByName(parameterName).extraEvents.append(self.updateDisplay)
            self.updateColor()
            self.scalingChanged()
            if giveFeedback:
                if self.sourceChannel:
                    self.print(f'Source channel {self.name} successfully reconnected.', flag=PRINT.DEBUG)
                else:
                    self.print(f'Source channel {self.name} could not be reconnected.', flag=PRINT.ERROR)

        def getDisplayUnit(self) -> str:  # noqa: D102
            if self.sourceParameter:
                return self.sourceParameter.unit
            return super().getDisplayUnit()

        def setSourceChannelValue(self) -> None:
            """Update sourceChannel.value."""
            if self.sourceChannel:
                try:
                    if self.sourceParameter:
                        self.sourceParameter.value = self.value
                    else:
                        self.sourceChannel.value = self.value  # type: ignore # noqa: PGH003
                except RuntimeError as e:
                    self.print(f'Error on updating {self.name}: {e}', flag=PRINT.ERROR)
                    self.sourceChannel = None
                    self.connectSource()

        def relayValueEvent(self) -> None:
            """Update value when sourceChannel.value changed."""
            if self.sourceChannel:
                try:
                    if self.sourceParameter:
                        if self.sourceParameter.indicator:
                            self.monitor = cast('float |int', self.sourceParameter.value)
                        else:
                            self.value = cast('float |int', self.sourceParameter.value)
                    else:
                        value = self.sourceChannel.value
                        sourceDevice = self.sourceChannel.getDevice()
                        if value is not None and isinstance(sourceDevice, Device):
                            value = value - self.sourceChannel.background if sourceDevice.subtractBackgroundActive() else self.sourceChannel.value
                            if self.sourceChannel.inout == INOUT.OUT:
                                self.monitor = value
                            else:
                                self.value = value
                except RuntimeError:
                    self.removeEvents()

        def relayMonitorEvent(self) -> None:
            """Update monitor and monitor stylesheet when sourceChannel.monitor changed."""
            if self.sourceChannel:
                try:
                    self.monitor = self.sourceChannel.monitor
                    monitorWidget = self.getParameterByName(self.MONITOR).getWidget()
                    monitorSourceWidget = self.sourceChannel.getParameterByName(self.MONITOR).getWidget()
                    if monitorWidget and monitorSourceWidget:
                        monitorWidget.setStyleSheet(monitorSourceWidget.styleSheet())
                except RuntimeError:
                    self.removeEvents()

        def monitorChanged(self) -> None:
            """Disable internal monitor event."""

        def getSourceChannelValues(self) -> None:
            """Get value and if applicable monitor from sourceChannel."""
            if self.sourceChannel:
                if self.sourceParameter:
                    if self.sourceParameter.indicator:
                        self.monitor = cast('float |int', self.sourceParameter.value)
                    else:
                        self.value = cast('float |int', self.sourceParameter.value)
                elif self.sourceChannel.inout == INOUT.OUT:
                    self.monitor = self.sourceChannel.value
                else:
                    self.value = self.sourceChannel.value
                    if self.sourceChannel.useMonitors:
                        self.monitor = self.sourceChannel.monitor

        def onDelete(self) -> None:  # noqa: D102  # pylint: disable = missing-function-docstring
            super().onDelete()
            self.removeEvents()

        def removeEvents(self) -> None:
            """Remove extra events from sourceChannel."""
            if self.sourceChannel:
                if self.sourceParameter and self.relayValueEvent in self.sourceParameter.extraEvents:
                    self.sourceParameter.extraEvents.remove(self.relayValueEvent)
                if self.relayValueEvent in self.sourceChannel.getParameterByName(self.VALUE).extraEvents:
                    self.sourceChannel.getParameterByName(self.VALUE).extraEvents.remove(self.relayValueEvent)
                if self.sourceChannel.useMonitors and self.relayMonitorEvent in self.sourceChannel.getParameterByName(self.MONITOR).extraEvents:
                    self.sourceChannel.getParameterByName(self.MONITOR).extraEvents.remove(self.relayMonitorEvent)
                for parameterName in [self.LINEWIDTH, self.LINESTYLE, self.COLOR]:
                    if (parameterName in self.sourceChannel.displayedParameters and
                    self.updateDisplay in self.sourceChannel.getParameterByName(parameterName).extraEvents):
                        self.sourceChannel.getParameterByName(parameterName).extraEvents.remove(self.updateDisplay)

        def getDefaultChannel(self) -> dict[str, dict]:  # noqa: D102  # pylint: disable = missing-function-docstring

            # definitions for type hinting
            self.unit: str
            self.notes: str

            channel = super().getDefaultChannel()
            channel.pop(Channel.EQUATION)
            channel.pop(Channel.ACTIVE)
            channel.pop(Channel.REAL)
            channel.pop(Channel.SMOOTH)
            channel.pop(Channel.LINEWIDTH)
            channel.pop(Channel.LINESTYLE)
            channel.pop(Channel.COLOR)
            channel[self.VALUE][Parameter.HEADER] = 'Set value '  # channels can have different types of parameters and units
            channel[self.VALUE][Parameter.EVENT] = self.setSourceChannelValue
            channel[self.MONITOR][Parameter.HEADER] = 'Read value'  # channels can have different types of parameters and units
            channel[self.DEVICE] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, advanced=False,
                                                 toolTip='Source device.', header=' ')
            channel[self.UNIT] = parameterDict(value='', parameterType=PARAMETERTYPE.LABEL, advanced=False, attr='unit', header='Unit   ', indicator=True)
            channel[self.NOTES] = parameterDict(value='', parameterType=PARAMETERTYPE.LABEL, advanced=True, attr='notes', indicator=True)
            channel[self.NAME][Parameter.EVENT] = self.connectSource
            return channel

        def setDisplayedParameters(self) -> None:  # noqa: D102  # pylint: disable = missing-function-docstring
            super().setDisplayedParameters()
            self.displayedParameters.remove(self.ENABLED)
            self.displayedParameters.remove(self.EQUATION)
            self.displayedParameters.remove(self.ACTIVE)
            self.displayedParameters.remove(self.REAL)
            self.displayedParameters.remove(self.SMOOTH)
            self.displayedParameters.remove(self.LINEWIDTH)
            self.displayedParameters.remove(self.LINESTYLE)
            self.displayedParameters.remove(self.COLOR)
            self.displayedParameters.append(self.NOTES)
            self.insertDisplayedParameter(self.DEVICE, self.NAME)
            self.insertDisplayedParameter(self.UNIT, self.DISPLAY)

        def tempParameters(self) -> list[str]:  # noqa: D102  # pylint: disable = missing-function-docstring
            return [*super().tempParameters(), self.VALUE, self.NOTES, self.DEVICE, self.UNIT]

        def initGUI(self, item: dict) -> None:  # noqa: D102  # pylint: disable = missing-function-docstring
            super().initGUI(item)
            device = self.getParameterByName(self.DEVICE)
            device.widget = QPushButton()
            device.widget.setStyleSheet('QPushButton{border:none;}')
            device.applyWidget()

    channelType = UCMChannel

    def initGUI(self) -> None:  # noqa: D102
        super().initGUI()
        self.importAction.setToolTip(f'Import {self.name} channels.')
        self.exportAction.setToolTip(f'Export {self.name} channels.')
        self.recordingAction = self.addStateAction(lambda: self.toggleRecording(manual=True),
                                                   toolTipFalse=f'Start {self.name} data acquisition.', iconFalse=self.makeCoreIcon('play.png'),
                                                   toolTipTrue='Stop data acquisition.', iconTrue=self.makeCoreIcon('pause.png'))

    def afterFinalizeInit(self) -> None:  # noqa: D102
        super().afterFinalizeInit()
        self.connectAllSources(update=True)
        self.clearPlot()  # init fig after connecting sources
        if self.liveDisplay:
            self.liveDisplay.plot(apply=True)

    def getChannels(self) -> list[Channel]:  # noqa: D102
        return [channel for channel in self.channels if channel.sourceChannel]

    def duplicateChannel(self) -> None:  # noqa: D102
        newChannel = cast('UCM.UCMChannel', super().duplicateChannel())
        if newChannel:
            newChannel.connectSource()

    def toggleRecording(self, on: 'bool | None' = None, manual: bool = False) -> None:  # noqa: D102
        super().toggleRecording(on=on, manual=manual)
        if manual:
            for device in list({channel.getDevice() for channel in self.getChannels()}):
                if isinstance(device, ChannelManager):
                    device.toggleRecording(on=self.recording, manual=manual)

    def loadConfiguration(self, file: 'Path | None' = None, useDefaultFile: bool = False, append: bool = False) -> None:  # noqa: D102
        super().loadConfiguration(file, useDefaultFile, append=append)
        if not self.pluginManager.loading:
            self.connectAllSources(update=True)

    def loadData(self, file: Path, showPlugin: bool = True) -> None:  # noqa: ARG002, D102
        self.pluginManager.Text.setText(f'Import channels from {file} explicitly.', showPlugin=True)

    def moveChannel(self, up: bool) -> None:  # noqa: D102
        newChannel = cast('UCM.UCMChannel', super().moveChannel(up=up))
        if newChannel:
            newChannel.connectSource()

    def connectAllSources(self, update: bool = False) -> None:
        """Connect all available source channels.

        :param update: Indicates that all channels should be (re-)connected. Otherwise will only attempt to connect channels that are not yet connected. Defaults to False
        :type update: bool, optional
        """
        self.loading = True  # suppress plot
        for channel in self.channels:
            if not channel.sourceChannel or update:
                channel.connectSource()
            else:  # only reconnect (disconnect) if the reference has become invalid
                try:
                    channel.sourceChannel.value  # testing access to a parameter that depends on sourceChannel with no internal fallback  # noqa: B018
                except RuntimeError as e:
                    self.print(f'Source channel {channel.name} may have been lost: {e} Attempt reconnecting.', flag=PRINT.DEBUG)
                    channel.connectSource(giveFeedback=True)
        self.loading = False

    def reconnectSource(self, name: str) -> None:
        """Try to reconnect linked channels if applicable.

        This is needed e.g. after renaming, moving, or deleting channels.
        If the channel has been deleted, the reconnection attempt will fail and and the linking channel will indicated that no source has been found.

        :param name: Name of the channel to reconnect.
        :type name: str
        """
        for channel in self.channels:
            if channel.name.split('.')[0] == name:  # remove optional .parameter in UCMChannel name
                self.print(f'Source channel {channel.name} may have been lost. Attempt reconnecting.', flag=PRINT.DEBUG)
                channel.connectSource(giveFeedback=True)


class PID(ChannelManager):
    """Allow to connect an input (controlling) and output (controlled) channel via PID logic.

    Whenever the output changes, the input will be adjusted to stabilize the output to its setpoint.
    Proportional: If you're not where you want to be, get there.
    Integral: If you haven't been where you want to be for a long time, get there faster.
    Derivative: If you're getting close to where you want to be, slow down.
    """

    name = 'PID'
    version = '1.0'
    pluginType = PLUGINTYPE.CHANNELMANAGER
    optional = True
    inout = INOUT.NONE
    maxDataPoints = 0  # PID channels do not store data
    iconFile = 'PID.png'
    useOnOffLogic = True
    useDisplays = False
    useMonitors = True

    channels: list['PIDChannel']

    class PIDChannel(RelayChannel, Channel):
        """Minimal UI for abstract PID channel."""

        def __init__(self, **kwargs) -> None:  # noqa: D107
            super().__init__(**kwargs)
            self.inputChannel = None
            self.pid = None

        OUTPUT = 'Output'
        OUTPUTDEVICE = 'OutputDevice'
        INPUT = 'Input'
        INPUTDEVICE = 'InputDevice'
        PROPORTIONAL = 'Proportional'  # if you're not where you want to be, get there
        INTEGRAL = 'Integral'     # if you haven't been where you want to be for a long time, get there faster
        DERIVATIVE = 'Derivative'   # if you're getting close to where you want to be, slow down
        SAMPLETIME = 'Sampletime'

        def connectSource(self, giveFeedback: bool = False) -> None:  # noqa: C901, PLR0912
            """Connect the source and inputChannels.

            :param giveFeedback: Report on success of connection, defaults to False
            :type giveFeedback: bool, optional
            """
            self.removeEvents()
            self.sourceChannel, outNotes = self.findChannel(self.output, self.OUTPUTDEVICE)
            self.inputChannel, inNotes = self.findChannel(self.input, self.INPUTDEVICE)
            self.notes = f'Output: {outNotes}, Input: {inNotes}'
            if self.sourceChannel:
                device = self.sourceChannel.getDevice()
                if isinstance(device, ChannelManager):
                    self.unit = device.unit
                self.getValues = self.sourceChannel.getValues
            else:
                self.getValues = lambda *_, **__: None
            if self.inputChannel and (not hasattr(self.inputChannel, 'min') or not hasattr(self.inputChannel, 'max')):
                self.print(f'Source channel {self.input} cannot be used as input channel. Input channel needs to have min max limits defined.', flag=PRINT.ERROR)
                self.inputChannel = None
            if giveFeedback:
                if self.sourceChannel:
                    self.print(f'Source channel {self.output} successfully reconnected.', flag=PRINT.DEBUG)
                else:
                    self.print(f'Source channel {self.output} could not be reconnected.', flag=PRINT.ERROR)
                if self.inputChannel:
                    self.print(f'Source channel {self.input} successfully reconnected.', flag=PRINT.DEBUG)
                else:
                    self.print(f'Source channel {self.input} could not be reconnected.', flag=PRINT.ERROR)
            if not self.sourceChannel or not self.inputChannel:
                return
            if self.sourceChannel.useMonitors:
                self.sourceChannel.getParameterByName(self.MONITOR).extraEvents.append(self.stepPID)
            else:
                self.sourceChannel.getParameterByName(self.VALUE).extraEvents.append(self.stepPID)
            if self.value is not None:
                self.pid = simple_pid.PID(self.p, self.i, self.d, setpoint=self.value, sample_time=self.sample_time,
                                      output_limits=(self.inputChannel.min, self.inputChannel.max))
            self.updateColor()
            if not self.pluginManager.loading and hasattr(self.pluginManager, 'UCM'):
                self.pluginManager.UCM.reconnectSource(self.name)

        def findChannel(self, name: str, DEVICE: str) -> tuple[Channel | None, str]:
            """Find the source or inputChannel based on its name and sets its device icon.

            :param name: Channel name.
            :type name: str
            :param DEVICE: Input or Outputdevice icon parameter
            :type DEVICE: str
            :return: channel, notes
            :rtype: :class:`~esibd.core.Channel`, str
            """
            channels = [channel for channel in self.pluginManager.DeviceManager.channels() if channel.name.strip().lower() == name.strip().lower()]
            selectedChannel = None
            notes = ''
            devicePushButton = cast('QPushButton', self.getParameterByName(DEVICE).getWidget())
            if len(channels) == 0:
                notes = f'Could not find {name}'
                devicePushButton.setIcon(self.channelParent.makeCoreIcon('help_large_dark.png' if getDarkMode() else 'help_large.png'))
                devicePushButton.setToolTip('Source: Unknown')
            else:
                selectedChannel = channels[0]
                notes = f'{selectedChannel.getDevice().name}.{selectedChannel.name}'
                devicePushButton.setIcon(selectedChannel.getIcon())
                devicePushButton.setToolTip(f'Source: {selectedChannel.getDevice().name}')
                if len(channels) > 1:
                    self.print(f'More than one match for {name}. Using {selectedChannel.getDevice().name}.{selectedChannel.name}.'
                               ' Use unique names to avoid this.', flag=PRINT.WARNING)
            return selectedChannel, notes

        def stepPID(self) -> None:
            """Set inputChannel.value based on sourceChannel.value and PID state."""
            if self.sourceChannel and self.inputChannel and self.pid:
                try:
                    inputDevice = cast('Device', self.inputChannel.getDevice())
                    sourceDevice = cast('Device', self.sourceChannel.getDevice())
                    sourceChannelValue = self.sourceChannel.value
                    if sourceChannelValue is not None:
                        if self.sourceChannel.useMonitors:
                            self.monitor = self.sourceChannel.monitor
                        else:
                            self.monitor = (sourceChannelValue - self.sourceChannel.background
                                            if sourceDevice.subtractBackgroundActive() else sourceChannelValue)
                        if self.active and self.channelParent.isOn() and inputDevice.isOn():
                            if np.isnan(sourceChannelValue):
                                self.print(f'Channel {self.name} got undefined input NaN. Setting {self.inputChannel.name} to 0', flag=PRINT.WARNING)
                                response = 0  # could be replaced with a device specific safe value if needed. So far PID is only used for heaters where 0 V is safe.
                            else:
                                response = self.pid(sourceChannelValue)
                            self.print(f'Channel {self.name}, PID in {sourceChannelValue}, PID out {response}, PID components {self.pid.components}', flag=PRINT.VERBOSE)
                            if response is not None:
                                self.inputChannel.value = response
                except RuntimeError as e:
                    self.print(f'Resetting. Source channel {self.output} or {self.input} may have been lost: {e}. Attempt reconnecting.', flag=PRINT.VERBOSE)
                    self.connectSource(giveFeedback=True)

        def valueChanged(self) -> None:
            """Update PID when setpoint changed."""
            super().valueChanged()
            self.updateSetpoint()
            self.stepPID()

        def monitorChanged(self) -> None:
            """Disable internal monitor event."""

        def updateSetpoint(self) -> None:
            """Update the setpoint in the pid controller based on the channel value."""
            if self.pid and self.value is not None:
                self.pid.setpoint = self.value

        def updateSampleTime(self) -> None:
            """Update the sample time in the pid controller based on the channel value."""
            if self.pid:
                self.pid.sample_time = self.sample_time

        def updatePID(self) -> None:
            """Update P, I, and D in the pid controller based on the channel values."""
            if self.pid:
                self.pid.tunings = self.p, self.i, self.d

        def onDelete(self) -> None:  # noqa: D102  # pylint: disable = missing-function-docstring
            super().onDelete()
            self.removeEvents()

        def removeEvents(self) -> None:
            """Remove extra events from sourceChannel."""
            if self.sourceChannel:
                if self.stepPID in self.sourceChannel.getParameterByName(self.VALUE).extraEvents:
                    self.sourceChannel.getParameterByName(self.VALUE).extraEvents.remove(self.stepPID)
                if self.sourceChannel.useMonitors and self.stepPID in self.sourceChannel.getParameterByName(self.MONITOR).extraEvents:
                    self.sourceChannel.getParameterByName(self.MONITOR).extraEvents.remove(self.stepPID)

        def getDefaultChannel(self) -> dict[str, dict]:  # noqa: D102  # pylint: disable = missing-function-docstring

            # definitions for type hinting
            self.unit: str
            self.output: str
            self.input: str
            self.active: bool
            self.p: float
            self.i: float
            self.d: float
            self.sample_time: float
            self.notes: str

            channel = super().getDefaultChannel()
            channel.pop(Channel.EQUATION)
            channel.pop(Channel.ACTIVE)
            channel.pop(Channel.REAL)
            channel.pop(Channel.COLOR)
            channel[self.MIN] = parameterDict(value=-50, parameterType=PARAMETERTYPE.FLOAT, advanced=True,
                                    event=self.updateMin, attr='min', header='Min       ')
            channel[self.MAX] = parameterDict(value=+50, parameterType=PARAMETERTYPE.FLOAT, advanced=True,
                                    event=self.updateMax, attr='max', header='Max       ')
            channel[self.VALUE][Parameter.HEADER] = 'Setpoint   '  # channels can have different types of parameters and units
            channel[self.UNIT] = parameterDict(value='', parameterType=PARAMETERTYPE.LABEL, attr='unit', header='Unit   ', indicator=True)
            channel[self.OUTPUT] = parameterDict(value='Output', parameterType=PARAMETERTYPE.TEXT, attr='output', event=self.connectSource,
                                                 toolTip='Output channel', header='Controlled')
            channel[self.OUTPUTDEVICE] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, advanced=False,
                                                 toolTip='Output device.', header=' ')
            channel[self.INPUT] = parameterDict(value='Input', parameterType=PARAMETERTYPE.TEXT, attr='input', event=self.connectSource,
                                                 toolTip='Input channel', header='Controlling')
            channel[self.INPUTDEVICE] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, advanced=False,
                                                 toolTip='Input device.', header=' ')
            channel[self.ACTIVE] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, attr='active', toolTip='Activate PID control.')
            channel[self.PROPORTIONAL] = parameterDict(value=1, parameterType=PARAMETERTYPE.FLOAT, advanced=True, attr='p', header='P        ',
                                                       event=self.updatePID, toolTip='Proportional')
            channel[self.INTEGRAL] = parameterDict(value=1, parameterType=PARAMETERTYPE.FLOAT, advanced=True, attr='i', header='I        ',
                                                       event=self.updatePID, toolTip='Integral')
            channel[self.DERIVATIVE] = parameterDict(value=1, parameterType=PARAMETERTYPE.FLOAT, advanced=True, attr='d', header='D        ',
                                                       event=self.updatePID, toolTip='Derivative')
            channel[self.SAMPLETIME] = parameterDict(value=10, parameterType=PARAMETERTYPE.FLOAT, advanced=True, attr='sample_time',
                                                       header='Time   ', event=self.updateSampleTime, toolTip='Sample time in s')
            channel[self.NOTES] = parameterDict(value='', parameterType=PARAMETERTYPE.LABEL, advanced=True, attr='notes', indicator=True)
            return channel

        def setDisplayedParameters(self) -> None:  # noqa: D102  # pylint: disable = missing-function-docstring
            super().setDisplayedParameters()
            self.displayedParameters.remove(self.ENABLED)
            self.displayedParameters.remove(self.EQUATION)
            self.displayedParameters.remove(self.REAL)
            self.displayedParameters.remove(self.COLOR)
            self.insertDisplayedParameter(self.ACTIVE, before=self.NAME)
            self.insertDisplayedParameter(self.UNIT, before=self.SCALING)
            self.insertDisplayedParameter(self.OUTPUTDEVICE, before=self.SCALING)
            self.insertDisplayedParameter(self.OUTPUT, before=self.SCALING)
            self.insertDisplayedParameter(self.INPUTDEVICE, before=self.SCALING)
            self.insertDisplayedParameter(self.INPUT, before=self.SCALING)
            self.insertDisplayedParameter(self.PROPORTIONAL, before=self.SCALING)
            self.insertDisplayedParameter(self.INTEGRAL, before=self.SCALING)
            self.insertDisplayedParameter(self.DERIVATIVE, before=self.SCALING)
            self.insertDisplayedParameter(self.SAMPLETIME, before=self.SCALING)
            self.insertDisplayedParameter(self.NOTES, before=self.SCALING)
            self.insertDisplayedParameter(self.MIN, before=self.MONITOR)
            self.insertDisplayedParameter(self.MAX, before=self.MONITOR)

        def tempParameters(self) -> list[str]:  # noqa: D102  # pylint: disable = missing-function-docstring
            return [*super().tempParameters(), self.NOTES, self.OUTPUTDEVICE, self.INPUTDEVICE]

        def initGUI(self, item: dict) -> None:  # noqa: D102  # pylint: disable = missing-function-docstring
            super().initGUI(item)
            active = self.getParameterByName(self.ACTIVE)
            value = cast('bool', active.value)
            active.widget = ToolButton()
            active.applyWidget()
            if active.check:
                active.check.setMaximumHeight(active.rowHeight)  # default too high
                active.check.setText(self.ACTIVE.title())
                active.check.setMinimumWidth(5)
                active.check.setCheckable(True)
            active.value = value
            for DEVICE in [self.OUTPUTDEVICE, self.INPUTDEVICE]:
                device = self.getParameterByName(DEVICE)
                device.widget = QPushButton()
                device.widget.setStyleSheet('QPushButton{border:none;}')
                device.applyWidget()
            self.updateMin()
            self.updateMax()

    channelType = PIDChannel

    def afterFinalizeInit(self) -> None:  # noqa: D102
        super().afterFinalizeInit()
        self.connectAllSources(update=True)

    def loadConfiguration(self, file: 'Path | None' = None, useDefaultFile: bool = False, append: bool = False) -> None:  # noqa: D102
        super().loadConfiguration(file, useDefaultFile, append=append)
        if not self.pluginManager.loading:
            self.connectAllSources(update=True)

    def loadData(self, file: Path, showPlugin: bool = True) -> None:  # noqa: ARG002, D102
        self.pluginManager.Text.setText('Import channels from file explicitly.', showPlugin=True)

    def getChannels(self) -> list[Channel]:  # noqa: D102
        return [channel for channel in self.channels if channel.sourceChannel]

    def moveChannel(self, up: bool) -> None:  # noqa: D102
        newChannel = cast('PID.PIDChannel', super().moveChannel(up=up))
        if newChannel:
            newChannel.connectSource()
            self.pluginManager.reconnectSource(newChannel.name)

    def duplicateChannel(self) -> None:  # noqa: D102
        newChannel = cast('PID.PIDChannel', super().duplicateChannel())
        if newChannel:
            newChannel.connectSource()

    def connectAllSources(self, update: bool = False) -> None:
        """Connect all available source channels.

        :param update: Indicates that all channels should be (re-)connected. Otherwise will only attempt to connect channels that are not yet connected. Defaults to False
        :type update: bool, optional
        """
        for channel in self.channels:
            if not channel.sourceChannel or not channel.inputChannel or update:
                channel.connectSource()
            else:  # only reconnect (disconnect) if the reference has become invalid
                try:
                    channel.sourceChannel.value  # testing access to a Parameter that depends on sourceChannel with no internal fallback  # noqa: B018
                    channel.inputChannel.value  # testing access to a Parameter that depends on inputChannel with no internal fallback  # noqa: B018
                except RuntimeError as e:
                    self.print(f'Source channel {channel.output} or {channel.input} may have been lost: {e} Attempt reconnecting.', flag=PRINT.DEBUG)
                    channel.connectSource(giveFeedback=True)

    def reconnectSource(self, name: str) -> None:
        """Try to reconnect linked channels if applicable.

        This is needed e.g. after renaming, moving, or deleting channels.
        If the channel has been deleted, the reconnection attempt will fail and and the linking channel will indicated that no source has been found.

        :param name: Name of the channel to reconnect.
        :type name: str
        """
        for channel in self.channels:
            if name in {channel.input, channel.output}:
                self.print(f'Source channel {channel.output} or {channel.input} may have been lost. Attempt reconnecting.', flag=PRINT.DEBUG)
                channel.connectSource(giveFeedback=True)

    def updateTheme(self) -> None:  # noqa: D102
        super().updateTheme()
        if self.pluginManager.DeviceManager.titleBar:
            onToolButton = self.pluginManager.DeviceManager.titleBar.widgetForAction(self.onAction)
            if onToolButton:
                onToolButton.setStyleSheet(f"""QToolButton:checked{{background-color:{mix_hex_colors(colors.green, colors.bg, ratio=.7)};}}""")
