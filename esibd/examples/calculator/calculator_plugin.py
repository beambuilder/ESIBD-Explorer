from esibd.core import PLUGINTYPE, PRINT
from esibd.examples.calculator.calculator_standalone import Calculator as CalculatorWidget
from esibd.plugins import Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [Calculator]


class Calculator(Plugin):
    """Demonstrate how to integrate an external PyQt6 program as a plugin and interact with other plugins."""

    name = 'Calculator'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.CONTROL
    iconFile = 'calculator.png'

    useExtendedCalculator = True

    def initGUI(self) -> None:
        """Initialize your custom user interface."""
        super().initGUI()
        if self.useExtendedCalculator:
            self.calculatorWidget = ExtendedCalculatorWidget(parentPlugin=self)  # use this to import calculator with interface to other plugins
        else:
            self.calculatorWidget = CalculatorWidget()  # use this to import calculator as is
        self.addContentWidget(self.calculatorWidget)


class ExtendedCalculatorWidget(CalculatorWidget):
    """Optionally extend the calculator widget to allow interfacing with other plugins."""

    def __init__(self, parentPlugin) -> None:
        self.parentPlugin = parentPlugin
        super().__init__()

    def evaluate(self) -> None:
        channels = self.parentPlugin.pluginManager.DeviceManager.channels()
        channelNames = [channel.name for channel in channels if channel.name]
        channelNames.sort(reverse=True, key=len)  # avoid replacing a subset of a longer name with a matching shorter name of another channel
        equ = self.display.text()
        for name in channelNames:
            if name in equ:
                channel_equ = next((channel for channel in channels if channel.name == name), None)
                if channel_equ:
                    self.parentPlugin.print(f'Replacing channel name {name} with value '
                                        f'{channel_equ.value - channel_equ.background if channel_equ.useBackgrounds else channel_equ.value}.', flag=PRINT.MESSAGE)
                    equ = equ.replace(channel_equ.name, f'{channel_equ.value - channel_equ.background if channel_equ.useBackgrounds else channel_equ.value}')
        self.display.setText(equ)
        super().evaluate()
