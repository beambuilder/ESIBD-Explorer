# pylint: disable=[missing-module-docstring]  # see class docstrings

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QGridLayout, QLabel, QPushButton, QSizePolicy

from esibd.core import PLUGINTYPE
from esibd.plugins import Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Indicate that this module provides plugins. Returns list of provided plugins."""
    return [CustomPlugin]


class CustomPlugin(Plugin):
    """The minimal code in "examples/custom_plugin/custom_plugin.py" demonstrates how to integrate your own custom elements to the ESIBD Explorer.

    It also demonstrates how to interact with and even extend other plugins including internal plugins.
    See :ref:`sec:plugin_system` for more information.
    """

    documentation = """The minimal code in examples/custom_plugin/custom_plugin.py demonstrates how to integrate your own
    custom elements to the ESIBD Explorer.
    It also demonstrates how to interact with and even extend other plugins including internal plugins.
    """

    name = 'CustomControl'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.CONTROL
    iconFile = 'cookie.png'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # TODO initialize any custom variables

    def initGUI(self) -> None:
        """Initialize your custom user interface."""
        super().initGUI()
        lay = QGridLayout()
        self.btn = QPushButton()
        lay.addWidget(self.btn)
        self.btn.setText('Click Me!')
        self.btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.btn.clicked.connect(self.onClick)
        self.addContentLayout(lay)

    def onClick(self) -> None:
        """Execute your custom code."""
        dlg = QDialog(self, Qt.WindowType.WindowStaysOnTopHint)
        dlg.setWindowTitle('Custom Dialog')
        lbl = QLabel('This could run your custom code.')
        lay = QGridLayout()
        lay.addWidget(lbl)
        dlg.setLayout(lay)
        dlg.exec()

    def afterFinalizeInit(self) -> None:
        super().afterFinalizeInit()
        # NOTE: the next line demonstrated that you can even use a custom plugin to modify any of the build-in plugins.
        # This may make debugging and maintaining the code harder, but it could also be cleaner and easier to implement
        # compared to overwriting the internal plugin with a custom version.
        self.pluginManager.Console.addAction(event=self.onClick, toolTip=f'Action added by {self.name}.', icon=self.makeIcon('cookie.png'),
                                              before=self.pluginManager.Console.errorFilterAction)
