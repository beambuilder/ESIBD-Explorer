# pylint: disable=[missing-module-docstring]  # see class docstrings

from typing import TYPE_CHECKING

from esibd.core import PRINT
from esibd.examples import CustomDevice

if TYPE_CHECKING:
    from esibd.plugins import Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [CustomDevice2]


class CustomDevice2(CustomDevice):
    """Custom Device that demonstrates how to replace an internal plugin.

    By inheriting from an existing plugin you can focus on the small changes and benefit from all future bug fixes and additions of the original plugin.
    Also compare the TIC plugin in esibd.devices.TIC

    No two plugins are allowed to have the same name.
    Still you may want to replace an internal plugin with a custom version and keep using previously generated files.
    CustomDevice2 demonstrates how to do this by explicitly defining the file types used by the original plugin.
    When used, the original plugin should first be disabled in the Plugin Manager to make sure that they do not try to access the same files at the same time.

    NOTE: In a real use case, you would save this file in your custom plugin folder and not touch any files in the installation folder.
    """

    name = 'CustomDevice2'

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.confINI = f'{CustomDevice.name}.ini'  # not a file extension, but complete filename to save and restore configurations
        self.confh5 = f'_{CustomDevice.name.lower()}.h5'
        self.previewFileTypes = [self.confINI, self.confh5]
        parentDocumentation = super().documentation or super().__doc__
        if self.__doc__ and parentDocumentation:
            self.documentation = self.__doc__ + '\nDocumentation of the parent plugin below:\n' + parentDocumentation

    def initGUI(self) -> None:
        """Initialize your custom user interface."""
        super().initGUI()
        # a base UI is provided by parent class, it can be extended like this if required
        self.customAction2 = self.addAction(event=self.customActionEvent2, toolTip='Custom tooltip for additional action.', icon=self.makeIcon('cookies.png'))
        if hasattr(self.pluginManager, CustomDevice.name):
            self.print(f'Deactivate {CustomDevice.name}. It should not be active at the same time as {self.name}!', flag=PRINT.WARNING)

    def customActionEvent2(self) -> None:
        """Implement additional functionality."""
        if not self.testing or self.pluginManager.closing:
            self.messageBox.setWindowTitle('Additional Custom Dialog')
            self.messageBox.setWindowIcon(self.getIcon())
            self.messageBox.setText('This could run different custom code to extend the functionality of the original CustomDevice.')
            self.messageBox.open()
            self.messageBox.raise_()
