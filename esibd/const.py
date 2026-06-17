"""Defines constants used throughout the package."""

import colorsys
import importlib.util
import math
import re
import subprocess  # noqa: S404
import sys
import traceback
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from functools import wraps
from typing import TYPE_CHECKING, TypeVar, Union, cast

import h5py
import numpy as np
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QColor
from scipy import signal

from esibd.config import *  # pylint: disable = wildcard-import, unused-wildcard-import  # noqa: F403

if TYPE_CHECKING:
    from types import ModuleType

    from esibd.core import Channel, Parameter
    from esibd.plugins import Plugin, SettingsManager

ParameterType = Union[str, Path, int, float, QColor, bool]  # str | Path | int | float | QColor | bool not compatible with sphinx # noqa: UP007
T = TypeVar('T')

PROGRAM = 'Program'
VERSION = 'Version'
NAME = 'Name'
PLUGIN = 'Plugin'
INFO = 'Info'
TIMESTAMP = 'Time'
GENERAL = 'General'
DATAPATH = 'Data path'
CONFIGPATH = 'Config path'
PLUGINPATH = 'Plugin path'
DEBUG = 'Debug mode'
LOGLEVEL = 'Log level'
DARKMODE = 'Dark mode'
CLIPBOARDTHEME = 'Clipboard theme'
DPI = 'DPI'
TESTMODE = 'Test mode'
MAXLAGTOLERANCE = 'Max lag tolerance'
LAGWAITTIME = 'Lag wait time'
ERRORRESETTIME = 'Error reset time'
WAKEMODE = 'Wake mode'
ICONMODE = 'Icon mode'
GEOMETRY = 'GEOMETRY'
SETTINGSWIDTH = 'SettingsWidth'
SETTINGSHEIGHT = 'SettingsHeight'
CONSOLEHEIGHT = 'ConsoleHeight'
INPUTCHANNELS = 'Input Channels'
OUTPUTCHANNELS = 'Output Channels'
UNIT = 'Unit'
SELECTFILE = 'Select File'
SELECTPATH = 'Select Path'

# * default paths should not be in software folder as this might not have write access after installation
defaultDataPath = Path.home() / PROGRAM_NAME / 'data/'
defaultConfigPath = Path.home() / PROGRAM_NAME / 'conf/'
defaultPluginPath = Path.home() / PROGRAM_NAME / 'plugins/'

# file types
FILE_INI = '.ini'
FILE_H5 = '.h5'
FILE_PDF = '.pdf'
FILE_PY = '.py'
FILE_LOG = '.log'

# other
UTF8 = 'utf-8'
ANSI = 'ANSI'
valid_chars = r'^[a-zA-Z0-9\s\-_\(\)\[\]\{\}\.*;:" \'<>^?=\+,~!@#$%&/]*$'

qSet = QSettings(COMPANY_NAME, PROGRAM_NAME)


class Colors:
    """Provides dark mode dependent default colors."""

    fg_dark = '#e4e7eb'
    fg_light = '#000000'

    @property
    def fg(self) -> str:
        """Foreground color."""
        return self.fg_dark if getDarkMode() else self.fg_light

    bg_dark = '#202124'
    bg_light = '#ffffff'

    @property
    def bg(self) -> str:
        """Background color."""
        return self.bg_dark if getDarkMode() else self.bg_light

    @property
    def bgAlt1(self) -> str:
        """First alternative background color."""
        return QColor(self.bg).lighter(160).name() if getDarkMode() else QColor(self.bg).darker(105).name()

    @property
    def bgAlt2(self) -> str:
        """Second alternative background color."""
        return QColor(self.bg).lighter(200).name() if getDarkMode() else QColor(self.bg).darker(110).name()

    @property
    def highlight(self) -> str:
        """Highlight color."""
        return '#8ab4f7'

    @property
    def green(self) -> str:
        """ESIBD green."""
        return '#a9d18e'

    @property
    def blue(self) -> str:
        """ESIBD blue."""
        return '#8faadc'

    @property
    def red(self) -> str:
        """ESIBD red."""
        return '#f4b183'

    @property
    def yellow(self) -> str:
        """ESIBD yellow."""
        return '#ffd966'


colors = Colors()


def hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    """Convert a hex color to rgb.

    :param hex_color: hex color string
    :type hex_color: str
    :return: converted rbg color
    :rtype: tuple[float, float, float]
    """
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0
    return r, g, b


def rgb_to_hex(rgba: tuple[float, float, float, float]) -> str:
    """Convert colors from rgb to hex.

    :param rgba: RGBA color tuple.
    :type rgba: tuple
    :return: Hex color string.
    :rtype: str
    """
    return f'#{int(rgba[0] * 255):02x}{int(rgba[1] * 255):02x}{int(rgba[2] * 255):02x}'


def mix_hex_colors(color1: str, color2: str, ratio: float) -> str:
    """Mix two hex colors by ratio.

    :param color1: First color, e.g. "#FF0000"
    :param color2: Second color, e.g. "#0000FF"
    :param ratio: 0.0 (all color1) to 1.0 (all color2)
    :return: Mixed hex color as string
    """
    c1 = [int(color1[i:i + 2], 16) for i in (1, 3, 5)]
    c2 = [int(color2[i:i + 2], 16) for i in (1, 3, 5)]

    mixed = [round(c1[i] * (1 - ratio) + c2[i] * ratio) for i in range(3)]
    return f'#{mixed[0]:02X}{mixed[1]:02X}{mixed[2]:02X}'


def color_distance(c1: tuple[float, float, float], c2: tuple[float, float, float]) -> float:
    """Measure color distance.

    :param c1: first color
    :type c1: rgb tuple[float, float, float]
    :param c2: second color
    :type c2: rgb tuple[float, float, float]
    :return: color distance
    :rtype: float
    """
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(c1, c2, strict=True)))


def similar_but_distinct_color(color_hex: str, index: int) -> str:
    """Return a similar but distinct color.

    This is used for related plots.
    The color should be close enough so it is clear that the plots are related,
    but distinct enough that they can be identified in the plot legend.

    :param color_hex: initial hex color.
    :type color_hex: str
    :param index: increase index if you need additional colors.
    :type index: int
    :return: similar but distinct hex color.
    :rtype: str
    """
    min_dist: float = 0.20
    max_dist: float = 0.90
    base_rgb = hex_to_rgb(color_hex)
    h, s, v = colorsys.rgb_to_hsv(*base_rgb)

    candidates = []
    # Sweep nearby hues and small sat/value changes
    for dh in [-0.14, -0.10, -0.06, -0.03, 0.03, 0.06, 0.10, 0.14]:
        for ds in [-0.08, 0.00, 0.08]:
            for dv in [-0.08, 0.00, 0.08]:
                nh = (h + dh) % 1.0
                ns = min(1.0, max(0.25, s + ds))
                nv = min(1.0, max(0.25, v + dv))
                rgb = colorsys.hsv_to_rgb(nh, ns, nv)
                candidates.append(rgb)
    candidates.sort(key=lambda color: color_distance(color, base_rgb))
    selected = []
    for candidate in candidates:
        if color_distance(candidate, base_rgb) < min_dist:
            continue
        if all(color_distance(candidate, prev) >= min_dist and color_distance(candidate, prev) <= max_dist for prev in selected):
            selected.append(candidate)
        if len(selected) >= index:
            break

    return rgb_to_hex(selected[-1])


class INOUT(Enum):
    """Specify if a function affects only input, only output, or all Channels."""

    IN = 0
    """Input"""
    OUT = 1
    """Output"""
    BOTH = 2
    """Both input and output."""
    NONE = 3
    """Neither input nor output."""
    ALL = 4
    """Input and output and all others."""


class PRINT(Enum):
    """Specify type of message in Plugin.print."""

    EXPLORER = 0
    """Key messages by Explorer"""
    MESSAGE = 1
    """A standard message."""
    WARNING = 2
    """Tag message as warning."""
    ERROR = 3
    """Tag message as error."""
    CONSOLE = 4
    """Tag message as Console input or output."""
    DEBUG = 5
    """Only show if debug flag is enabled."""
    VERBOSE = 6
    """Additional details that would be distracting if not needed for specific test."""
    TRACE = 7
    """All messages, including detailed trace of instrument communication."""


class PLUGINTYPE(Enum):
    """Each plugin must be of one of the following types to define its location and behavior."""

    CONSOLE = 'Console'
    """The internal Console."""
    CONTROL = 'Generic Control'
    """Any control plugin, will be placed next to Settings, Explorer, Devices, and Scans."""
    INPUTDEVICE = 'Input Device'
    """Device plugin sending user input to hardware."""
    OUTPUTDEVICE = 'Output Device'
    """Device plugin sending hardware output to user."""
    CHANNELMANAGER = 'Channel Manager'
    """A plugin that manages channels which are neither inputs or outputs."""
    DISPLAY = 'Display'
    """Any display plugin, will be places next to scan displays and static displays."""
    LIVEDISPLAY = 'LiveDisplay'
    """Live display associated with a device."""
    SCAN = 'Scan'
    """Scan plugin, will be placed with other controls."""
    DEVICEMGR = 'DeviceManager'
    """Device manager, will be placed below live displays."""
    INTERNAL = 'Internal'
    """A plugin without user interface."""


class PARAMETERTYPE(Enum):
    """Specifies what type of widget should be used to represent the Parameter in the user interface."""

    LABEL = 'LABEL'
    """A label that displays information."""
    PATH = 'PATH'
    """A path to a file or directory."""
    COMBO = 'COMBO'
    """A combobox providing text options."""
    INTCOMBO = 'INTCOMBO'
    """A combobox providing integer options."""
    FLOATCOMBO = 'FLOATCOMBO'
    """A combobox providing floating point options."""
    TEXT = 'TEXT'
    """An editable text field."""
    COLOR = 'COLOR'
    """A ColorButton that allows to select a color."""
    BOOL = 'BOOL'
    """A boolean, represented by a checkbox."""
    INT = 'INT'
    """An integer spinbox."""
    FLOAT = 'FLOAT'
    """A floating point spinbox."""
    EXP = 'EXP'
    """A spinbox with scientific format."""


def pluginSupported(pluginVersion: str) -> bool:
    """Test if given version is supported by comparing to current program version.

    :param pluginVersion: Version that the plugin supports.
    :type pluginVersion: str
    :return: True if the plugin is supported.
    :rtype: bool
    """
    return version.parse(pluginVersion).major == PROGRAM_VERSION.major and version.parse(pluginVersion).minor == PROGRAM_VERSION.minor


def makeSettingWrapper(name: str, settingsMgr: 'SettingsManager', docstring: str = '') -> property:
    """Neutral Setting wrapper for convenient access to the value of a Setting.

    If you need to handle events on value change, link these directly to the events of the corresponding control.

    :param name: The Setting name.
    :type name: str
    :param settingsMgr: The SettingsManager of the Setting.
    :type settingsMgr: esibd.plugins.SettingsManager
    :param docstring: The docstring used for the attribute, defaults to None
    :type docstring: str, optional
    """
    def getter(self: 'Plugin') -> 'ParameterType | None':  # pylint: disable=[unused-argument]  # self will be passed on when used in class  # noqa: ARG001
        return settingsMgr.settings[name].value

    def setter(self: 'Plugin', value: ParameterType) -> None:  # pylint: disable=[unused-argument]  # self will be passed on when used in class  # noqa: ARG001
        settingsMgr.settings[name].value = value
    return property(getter, setter, doc=docstring)


def makeWrapper(name: str, docstring: str = '') -> property:
    """Neutral property wrapper for convenient access to the value of a Parameter inside a Channel.

    If you need to handle events on value change, link these directly to the events of the corresponding control in the finalizeInit method.

    :param name: The Parameter name.
    :type name: str
    :param docstring: The docstring used for the attribute, defaults to None
    :type docstring: str, optional
    """
    def getter(self: 'Channel') -> 'ParameterType | None':
        return self.getParameterByName(name).value

    def setter(self: 'Channel', value: ParameterType) -> None:
        self.getParameterByName(name).value = value
    return property(getter, setter, doc=docstring)


def dynamicImport(module: str, path: Path | str) -> 'ModuleType | None':
    """Import a module from the given path at runtime.

    This is more general and robust than importing modules relative to plugin path.

    :param module: module name
    :type module: str
    :param path: module path
    :type path: Path | str
    :return: Module
    :rtype: ModuleType
    """
    spec = importlib.util.spec_from_file_location(module, path)
    if spec and spec.loader:
        Module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(Module)
        return Module
    return None


def getValidConfigPath() -> Path:
    """Get validated configuration path.

    :return: Validated path and indication if path has changed during validation.
    :rtype: pathlib.Path, bool
    """
    path, changed = validatePath(qSet.value(f'{GENERAL}/{CONFIGPATH}', defaultConfigPath), defaultConfigPath)
    if changed:
        qSet.setValue(f'{GENERAL}/{CONFIGPATH}', path)
    return path


def getValidDataPath() -> Path:
    """Get validated data path.

    :return: Validated path and indication if path has changed during validation.
    :rtype: pathlib.Path, bool
    """
    path, changed = validatePath(qSet.value(f'{GENERAL}/{DATAPATH}', defaultDataPath), defaultDataPath)
    if changed:
        qSet.setValue(f'{GENERAL}/{DATAPATH}', path)
    return path


def getValidPluginPath() -> Path:
    """Get validated plugin path.

    :return: Validated path and indication if path has changed during validation.
    :rtype: pathlib.Path, bool
    """
    path, changed = validatePath(qSet.value(f'{GENERAL}/{PLUGINPATH}', defaultPluginPath), defaultPluginPath)
    if changed:
        qSet.setValue(f'{GENERAL}/{PLUGINPATH}', path)
    return path


def getDebugMode() -> bool:
    """Get the debug mode from :ref:`sec:settings`.

    :return: Debug mode
    :rtype: bool
    """
    return qSet.value(f'{GENERAL}/{DEBUG}', defaultValue=True, type=bool)


def getLogLevel(asString: bool = False) -> int | str:
    """Get the log level from :ref:`sec:settings`.

    :param asString: Indicate if the log level should be returned as a string.
    :type asString: str
    :return: Log level
    :rtype: int | str
    """
    if asString:
        return qSet.value(f'{GENERAL}/{LOGLEVEL}', defaultValue='Basic')
    match qSet.value(f'{GENERAL}/{LOGLEVEL}', defaultValue='Basic'):
        case 'Basic':
            return 0
        case 'Debug':
            return 1
        case 'Verbose':
            return 2
        case _:
            return 3  # Trace


def getDarkMode() -> bool:
    """Get the dark mode from :ref:`sec:settings`.

    :return: Dark mode
    :rtype: bool
    """
    return qSet.value(f'{GENERAL}/{DARKMODE}', defaultValue=True, type=bool)


def setDarkMode(darkMode: bool) -> None:
    """Set the dark mode from :ref:`sec:settings`.

    :param darkMode: True if dark mode active.
    :type darkMode: bool
    """
    qSet.setValue(f'{GENERAL}/{DARKMODE}', darkMode)


def getClipboardTheme() -> bool:
    """Get the dark clipboard mode from :ref:`sec:settings`.

    :return: Dark clipboard mode
    :rtype: bool
    """
    return qSet.value(f'{GENERAL}/{CLIPBOARDTHEME}', defaultValue=True, type=bool)


def getDPI() -> int:
    """Get the DPI from :ref:`sec:settings`.

    :return: DPI
    :rtype: int
    """
    return qSet.value(f'{GENERAL}/{DPI}', 100, type=int)


def getIconMode() -> str:
    """Get the icon mode from :ref:`sec:settings`.

    :return: Icon mode
    :rtype: str
    """
    return qSet.value(f'{GENERAL}/{ICONMODE}', 'Icons')


def getTestMode() -> bool:
    """Get the test mode from :ref:`sec:settings`.

    :return: Test mode
    :rtype: bool
    """
    return qSet.value(f'{GENERAL}/{TESTMODE}', defaultValue=False, type=bool)


def getWakeMode() -> bool:
    """Get the wake mode from :ref:`sec:settings`.

    :return: Wake mode
    :rtype: bool
    """
    return qSet.value(f'{GENERAL}/{WAKEMODE}', defaultValue=False, type=bool)


def infoDict(name: str) -> dict[str, str]:
    """Return a dictionary with general information, usually used to add this information to exported files.

    :param name: Usually the name of the plugin requesting the infoDict.
    :type name: str
    :return: infoDict
    :rtype: dict
    """
    return {PROGRAM: PROGRAM_NAME, VERSION: str(PROGRAM_VERSION), PLUGIN: name, TIMESTAMP: datetime.now().strftime('%Y-%m-%d %H:%M')}


def validatePath(path: 'Path | str | None', default: Path) -> 'tuple[Path, bool]':
    """Return a valid path. If the path does not exist, falling back to default.

    If default does not exist it will be created.

    :param path: Valid path
    :type path: pathlib.Path
    :param default: Default path that is returned if path is not valid.
    :type default: pathlib.Path
    :return: Validated path and indication if path has changed during validation.
    :rtype: pathlib.Path, bool
    """
    if path and isinstance(path, str):
        path = Path(path)
    if not path or not path.exists():
        default = Path(default)
        if path == default:
            print(f'Creating {default.as_posix()}.')  # noqa: T201
        else:
            print(f'Could not find path {path.as_posix() if path else "None"}. Defaulting to {default.as_posix()}.')  # noqa: T201
        default.mkdir(parents=True, exist_ok=True)
        return default, True
    return path, False


def validateText(printParent: 'Plugin | Parameter', valid_chars: str, text: str) -> str:
    """Validate the text and remove invalid characters.

    :param printParent: A parent that provides access to the internal print function.
    :type printParent: Plugin | Parameter
    :param valid_chars: A regular expression containing valid characters
    :type valid_chars: str
    :param text: The text to be validated.
    :type text: str
    :return: validated text
    :rtype: str
    """
    # Remove any character that doesn't match the valid_chars regex
    if not re.match(valid_chars, text):
        # Filter the text, keeping only valid characters
        valid_text = ''.join([character for character in text if re.match(valid_chars, character)])
        _ = [printParent.print(f'Removing invalid character {character} from {text}',
                                         flag=PRINT.WARNING) for character in text if not re.match(valid_chars, character)]
        return valid_text
    return text


def smooth(data: np.typing.NDArray[np.float32], smooth: int) -> np.typing.NDArray[np.float32]:
    """Smooth a 1D array while keeping edges meaningful.

    This method is robust if array contains np.nan.

    :param data: Array to be smoothed.
    :type data: np.typing.NDArray[np.float32]
    :param smooth: With of box used for smoothing.
    :type smooth: int
    :return: convolvedArray
    :rtype: np.typing.NDArray[np.float32]
    """
    if len(data) < smooth:
        return data
    smooth = int(np.ceil(smooth / 2.) * 2)  # make even
    window = signal.windows.boxcar(smooth)  # type: ignore  # noqa: PGH003

    is_valid = np.isfinite(data).astype(float)
    # Smoothed sum and valid count
    smoothed = signal.convolve(data, window, mode='same', method='direct')
    valid_count = signal.convolve(is_valid, window, mode='same', method='direct')

    # Normalize
    with np.errstate(invalid='ignore', divide='ignore'):
        convolvedArray = smoothed / valid_count

    # Overwrite result where not enough valid data
    convolvedArray[valid_count < smooth] = data[valid_count < smooth]
    return convolvedArray.astype(np.float32)


def shorten_text(text: str, max_length: int = 100) -> str:
    """Shorten text e.g. for concise and consistent log file format.

    :param text: Original text.
    :type text: str
    :param max_length: Length after shortening. Defaults to 100
    :type max_length: int, optional
    :return: shortened text
    :rtype: str
    """
    keep_chars = (max_length - 1) // 2
    text = text.replace('\n', '')
    return text if len(text) < max_length else f'{text[:keep_chars]}…{text[-keep_chars:]}'


def synchronized(timeout: int = 5) -> Callable:
    """Decorate to add thread-safety using a lock from the instance.

    NOTE: Only works with keyword arguments. Positional arguments apart form self will not be passed on.

    Use with @synchronized() or @synchronized(timeout=5).

    :param timeout: Will wait this long for lock to become available. Defaults to 5
    :type timeout: int, optional
    :return: decorator
    :rtype: decorator
    """
    # avoid calling QApplication.processEvents() inside func as it may cause deadlocks
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self: 'Plugin', **kwargs) -> 'Callable | None':
            timeoutMessage = f'Cannot acquire lock for {func.__name__}.'
            if cast('int', getLogLevel()) > 0:  # DEBUG, VERBOSE, TRACE
                timeoutMessage += f'\nStack: {"".join(traceback.format_stack()[:-1])}'
            with self.lock.acquire_timeout(timeout=timeout, timeoutMessage=timeoutMessage) as lock_acquired:
                if lock_acquired:
                    self.print(f'Acquired lock for {func.__name__}.', flag=PRINT.VERBOSE)
                    return func(self, **kwargs)
                return None
        return wrapper
    return decorator


def plotting(func: Callable) -> Callable:
    """Decorate to check for and sets the plotting flag to make sure func is not executed before previous call is processed.

    Only use within a class that contains the plotting flag.
    This is intended for Scans, but might be used elsewhere.

    :param func: function to add the decorator to
    :type func: Callable
    :return: decorated function
    :rtype: Callable
    """
    @wraps(func)
    def wrapper(self, *args, **kwargs) -> 'Callable | None':  # noqa: ANN001
        if self.plotting:
            self.print('Skipping plotting as previous request is still being processed.', flag=PRINT.VERBOSE)
            if hasattr(self, 'measureInterval'):
                self.measureInterval(reset=False)  # do not reset but keep track of unresponsiveness
            return None
        self.plotting = True
        try:
            return func(self, *args, **kwargs)
        finally:
            self.plotting = False
    return wrapper


def openInDefaultApplication(file: str | Path) -> None:
    """Open file in system default application for file type.

    :param file: Path to the file to open.
    :type file: str / pathlib.Path
    """
    if sys.platform == 'win32':
        subprocess.Popen(f'explorer {file}')  # noqa: S603
    else:
        subprocess.Popen(['xdg-open', file])  # noqa: S603, S607


def datasetToStrList(dataset: h5py.Dataset) -> list[str]:
    """Convert a h5py string dataset into a list of str.

    :param dataset: The dataset.
    :type dataset: h5py.Dataset
    :return: The list.
    :rtype: list[str]
    """
    return [str(k) for k in dataset.asstr()]
