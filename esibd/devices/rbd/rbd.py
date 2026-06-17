# pylint: disable=[missing-module-docstring]  # see class docstrings
import time
from pathlib import Path
from typing import cast

import h5py
import numpy as np
import serial
import serial.serialutil

from esibd.core import PARAMETERTYPE, PLUGINTYPE, PRINT, Channel, CompactComboBox, DeviceController, MetaChannel, Parameter, getTestMode, parameterDict
from esibd.plugins import Device, Plugin, Scan, StaticDisplay


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [RBD]


class RBD(Device):
    """Contains a list of current channels, each corresponding to a single RBD 9103 picoammeter.

    The channels show the accumulated charge over time,
    which is proportional to the number of deposited ions. It can also
    reveal on which ion optics ions are lost.
    """

    name = 'RBD'
    version = '1.0'
    supportedVersion = '1.0'
    pluginType = PLUGINTYPE.OUTPUTDEVICE
    unit = 'pA'
    iconFile = 'RBD.png'
    useBackgrounds = True  # record backgrounds for data correction
    channels: 'list[RBDCurrentChannel]'

    class StaticDisplay(StaticDisplay):
        """A display for device data from files."""

        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.previewFileTypes.append('.cur.rec')
            self.previewFileTypes.append('.cur.h5')
            self.previewFileTypes.append('OUT.h5')

        def loadDataInternal(self, file: Path) -> bool:  # noqa: C901, PLR0912
            if file.name.endswith('.cur.rec'):  # legacy ESIBD Control file
                with file.open('r', encoding=self.UTF8) as dataFile:
                    dataFile.readline()
                    headers = dataFile.readline().split(',')  # read names from second line
                try:
                    data = np.loadtxt(file, skiprows=4, delimiter=',', unpack=True)
                except ValueError as e:
                    self.print(f'Loading from {file.name} failed: {e}', flag=PRINT.ERROR)
                    return False
                if data.shape[0] == 0:
                    self.print(f'No data found in file {file.name}.', flag=PRINT.ERROR)
                    return False
                for dat, header in zip(data, headers, strict=True):
                    self.outputChannels.append(MetaChannel(parentPlugin=self, name=header.strip(),
                                                            recordingData=np.array(dat), recordingBackground=np.zeros(dat.shape[0], dtype=np.float32), unit='pA'))
                if len(self.outputChannels) > 0:  # might be empty
                    # need to fake time axis as it was not implemented
                    outputRecordingData0 = self.outputChannels[0].getRecordingData()
                    if outputRecordingData0 is not None:
                        self.inputChannels.append(MetaChannel(parentPlugin=self, name=self.TIME,
                                                           recordingData=np.linspace(0, 120000, outputRecordingData0.shape[0])))
            elif file.name.endswith('.cur.h5'):
                with h5py.File(file, 'r') as h5file:
                    self.inputChannels.append(MetaChannel(parentPlugin=self, name=self.TIME, recordingData=cast('h5py.Dataset', h5file[self.TIME])[:]))
                    output_group = cast('h5py.Group', h5file['Current'])
                    for name, item in output_group.items():
                        if '_BG' in name:
                            self.outputChannels[-1].recordingBackground = item[:]
                        else:
                            self.outputChannels.append(MetaChannel(parentPlugin=self, name=name, recordingData=item[:], unit='pA'))
            elif file.name.endswith('OUT.h5'):  # old Output format when EBD was the only output
                with h5py.File(file, 'r') as h5file:
                    self.inputChannels.append(MetaChannel(parentPlugin=self, name=self.TIME, recordingData=cast('h5py.Dataset', h5file[Scan.INPUTCHANNELS])[self.TIME][:]))
                    output_group = cast('h5py.Group', h5file[Scan.OUTPUTCHANNELS])
                    for name, item in output_group.items():
                        if '_BG' in name:
                            self.outputChannels[-1].recordingBackground = item[:]
                        else:
                            self.outputChannels.append(MetaChannel(parentPlugin=self, name=name, recordingData=item[:], unit=item.attrs.get(Scan.UNIT, '')))
            else:
                return super().loadDataInternal(file)
            return True

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channelType = RBDCurrentChannel

    def initGUI(self) -> None:
        super().initGUI()
        self.addAction(event=self.resetCharge, toolTip=f'Reset accumulated charge for {self.name}.', icon='battery-empty.png')

    def getDefaultSettings(self) -> dict[str, dict]:
        defaultSettings = super().getDefaultSettings()
        defaultSettings[f'{self.name}/Interval'][Parameter.VALUE] = 100  # overwrite default value
        return defaultSettings

    def resetCharge(self) -> None:
        """Reset the charge of each channel."""
        for channel in self.channels:
            channel.resetCharge()


class RBDCurrentChannel(Channel):
    """UI for picoammeter with integrated functionality."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.controller = RBDCurrentController(controllerParent=self)
        self.preciseCharge = 0  # store independent of spin box precision to avoid rounding errors

    CHARGE = 'Charge'
    COM = 'COM'
    DEVICENAME = 'Devicename'
    RANGE = 'Range'
    AVERAGE = 'Average'
    BIAS = 'Bias'
    OUTOFRANGE = 'OutOfRange'
    UNSTABLE = 'Unstable'
    ERROR = 'Error'
    channelParent: RBD
    controller: 'RBDCurrentController'

    def getDefaultChannel(self) -> dict[str, dict]:

        # definitions for type hinting
        self.charge: float
        self.com: str
        self.devicename: str
        self.range: str
        self.average: str
        self.bias: bool
        self.outOfRange: bool
        self.unstable: bool
        self.error: str

        channel = super().getDefaultChannel()
        channel[self.VALUE][Parameter.HEADER] = 'I (pA)'
        channel[self.VALUE][Parameter.UNIT] = 'pA'
        channel[self.BACKGROUND][Parameter.HEADER] = 'BG (pA)'
        channel[self.BACKGROUND][Parameter.UNIT] = 'pA'
        channel[self.CHARGE] = parameterDict(value=0, parameterType=PARAMETERTYPE.FLOAT, advanced=False, header='C (pAh)', indicator=True,
                                             attr='charge', unit='pAh')
        channel[self.COM] = parameterDict(value='COM1', parameterType=PARAMETERTYPE.COMBO, advanced=True, toolTip='COM port',
                                        items=','.join([f'COM{x}' for x in range(1, 25)]), header='COM', attr='com')
        channel[self.DEVICENAME] = parameterDict(value='smurf', parameterType=PARAMETERTYPE.LABEL, advanced=True, attr='devicename')
        channel[self.RANGE] = parameterDict(value='auto', parameterType=PARAMETERTYPE.COMBO, advanced=True,
                                        items='auto, 2 nA, 20 nA, 200 nA, 2 µA, 20 µA, 200 µA, 2 mA', attr='range',  # noqa: RUF001
                                        event=self.updateRange, toolTip='Sample range. Defines resolution.')
        channel[self.AVERAGE] = parameterDict(value='off', parameterType=PARAMETERTYPE.COMBO, advanced=True,
                                        items='off, 2, 4, 8, 16, 32', attr='average',
                                        event=self.updateAverage, toolTip='Running average on hardware side.')
        channel[self.BIAS] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, advanced=True,
                                        toolTip='Apply internal bias.', attr='bias', event=self.updateBias)
        channel[self.OUTOFRANGE] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, advanced=False, indicator=True,
                                        header='OoR', toolTip='Indicates if signal is out of range.', attr='outOfRange', restore=False)
        channel[self.UNSTABLE] = parameterDict(value=False, parameterType=PARAMETERTYPE.BOOL, advanced=False, indicator=True,
                                        header='U', toolTip='Indicates if signal is unstable.', attr='unstable', restore=False)
        channel[self.ERROR] = parameterDict(value='', parameterType=PARAMETERTYPE.TEXT, advanced=False, attr='error', indicator=True, restore=False)
        return channel

    def setDisplayedParameters(self) -> None:
        super().setDisplayedParameters()
        self.insertDisplayedParameter(self.CHARGE, before=self.DISPLAY)
        self.displayedParameters.append(self.COM)
        self.displayedParameters.append(self.DEVICENAME)
        self.displayedParameters.append(self.RANGE)
        self.displayedParameters.append(self.AVERAGE)
        self.displayedParameters.append(self.BIAS)
        self.displayedParameters.append(self.OUTOFRANGE)
        self.displayedParameters.append(self.UNSTABLE)
        self.displayedParameters.append(self.ERROR)

    def initGUI(self, item: dict) -> None:
        super().initGUI(item)
        self.getParameterByName(self.ERROR).line.max_width = 600

    def tempParameters(self) -> list[str]:
        return [*super().tempParameters(), self.CHARGE, self.OUTOFRANGE, self.UNSTABLE, self.ERROR]

    def enabledChanged(self) -> None:
        super().enabledChanged()
        if self.controller.initialized:
            if self.enabled:
                self.controller.initializeCommunication()
            elif self.controller.acquiring:
                self.controller.stopAcquisition()

    def appendValue(self, lenT, nan=False) -> None:
        # calculate deposited charge in last time step for all channels
        # this does not only measure the deposition current but also on what lenses current is lost
        # make sure that the data interval is the same as used in data acquisition
        super().appendValue(lenT, nan=nan)
        if not nan and not np.isnan(self.value) and not np.isinf(self.value):
            chargeIncrement = (self.value - self.background) * self.channelParent.interval / 1000 / 3600 if self.values.size > 1 else 0
            self.preciseCharge += chargeIncrement  # display accumulated charge  # don't use np.sum(self.charges) to allow
            self.charge = self.preciseCharge  # pylint: disable=[attribute-defined-outside-init]  # attribute defined dynamically

    def clearHistory(self) -> None:
        super().clearHistory()
        self.resetCharge()

    def resetCharge(self) -> None:
        """Reset the charge."""
        self.charge = 0  # pylint: disable=[attribute-defined-outside-init]  # attribute defined dynamically
        self.preciseCharge = 0

    def realChanged(self) -> None:
        self.getParameterByName(self.COM).setVisible(self.real)
        self.getParameterByName(self.DEVICENAME).setVisible(self.real)
        self.getParameterByName(self.RANGE).setVisible(self.real)
        self.getParameterByName(self.AVERAGE).setVisible(self.real)
        self.getParameterByName(self.BIAS).setVisible(self.real)
        self.getParameterByName(self.OUTOFRANGE).setVisible(self.real)
        self.getParameterByName(self.UNSTABLE).setVisible(self.real)
        if self.channelParent.recording:
            self.controller.initializeCommunication()
        super().realChanged()

    def activeChanged(self) -> None:
        if self.channelParent.recording:
            self.controller.initializeCommunication()
        return super().activeChanged()

    def updateAverage(self) -> None:
        """Set flag to trigger update of average."""
        if self.controller and self.controller.acquiring:
            self.controller.updateAverageFlag = True

    def updateRange(self) -> None:
        """Set flag to trigger update of range."""
        if self.controller and self.controller.acquiring:
            self.controller.updateRangeFlag = True

    def updateBias(self) -> None:
        """Set flag to trigger update of bias."""
        if self.controller and self.controller.acquiring:
            self.controller.updateBiasFlag = True


class RBDCurrentController(DeviceController):  # noqa: PLR0904

    controllerParent: RBDCurrentChannel

    def __init__(self, controllerParent: RBDCurrentChannel) -> None:
        self.outOfRange = False
        self.unstable = False
        self.error = ''
        self.deviceName = ''
        super().__init__(controllerParent=controllerParent)
        self.port = None
        self.updateAverageFlag = False
        self.updateRangeFlag = False
        self.updateBiasFlag = False
        self.phase = self.rng.random() * 10  # used in test mode
        self.omega = self.rng.random()  # used in test mode
        self.offset = self.rng.random() * 10  # used in test mode

    def initializeCommunication(self) -> None:
        if self.controllerParent.enabled and self.controllerParent.active and self.controllerParent.real:
            super().initializeCommunication()
        else:
            self.stopAcquisition()  # as this is a channel controller it should only stop acquisition but not recording

    def runInitialization(self) -> None:
        try:
            self.port = serial.Serial(
                f'{self.controllerParent.com}',
                baudrate=57600,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                xonxoff=False,
                timeout=3)
            self.setRange()
            self.setAverage()
            self.setGrounding()
            self.setBias()
            name = self.getName()
            if not name:
                self.setValuesAndUpdate(0, False, False, f'Device at port {self.controllerParent.com} did not provide a name. Abort initialization.')  # noqa: FBT003
                return
            self.setValuesAndUpdate(0, False, False, f'{name} initialized at {self.controllerParent.com}', deviceName=name)  # noqa: FBT003
            self.signalComm.initCompleteSignal.emit()
        except serial.serialutil.PortNotOpenError as e:
            self.setValuesAndUpdate(0, False, False, f'Port {self.controllerParent.com} is not open: {e}')  # noqa: FBT003
        except serial.serialutil.SerialException as e:
            self.setValuesAndUpdate(0, False, False, f'9103 not found at {self.controllerParent.com}: {e}')  # noqa: FBT003
        finally:
            self.initializing = False

    def startAcquisition(self) -> None:
        if self.controllerParent.active and self.controllerParent.real:
            if not getTestMode():
                # start sampling with given interval (implement high speed communication if available)
                self.RBDWriteRead(message=f'I{self.controllerParent.channelParent.interval:04d}')
            super().startAcquisition()

    def readNumbers(self) -> None:
        if not self.controllerParent.pluginManager.closing and self.controllerParent.enabled and self.controllerParent.active and self.controllerParent.real:
            msg = ''
            msg = self.RBDRead()
            if not self.acquiring:  # may have changed while waiting on message
                return
            parsed = self.parse_message_for_sample(msg)
            if any(sym in parsed for sym in ['<', '>']):
                self.setValuesAndUpdate(np.nan, True, False, parsed)  # noqa: FBT003
            elif '*' in parsed:
                self.setValuesAndUpdate(np.nan, False, True, parsed)  # noqa: FBT003
            elif not parsed:
                self.setValuesAndUpdate(np.nan, False, False, 'got empty message')  # noqa: FBT003
            else:
                self.setValuesAndUpdate(self.readingToNum(parsed), False, False, '')  # noqa: FBT003
            if self.port:
                self.clearBuffer(self.port)  # make sure messages cannot accumulate at port in case readout rate is slower than sample rate
        self.updateParameters()

    def fakeNumbers(self) -> None:
        if not self.controllerParent.pluginManager.closing and self.controllerParent.enabled and self.controllerParent.active and self.controllerParent.real:
            self.setValuesAndUpdate(np.sin(self.omega * time.time() / 5 + self.phase) * 10 + self.rng.random() + self.offset, False, False, '')  # noqa: FBT003

    def updateValues(self) -> None:
        if self.values is not None:
            self.controllerParent.value = np.nan if self.controllerParent.waitToStabilize else self.values[0]
        self.controllerParent.outOfRange = self.outOfRange
        self.controllerParent.unstable = self.unstable
        if self.controllerParent.error != self.error:
            self.controllerParent.error = self.error
        if self.controllerParent.error:
            self.print(self.controllerParent.error, flag=PRINT.ERROR if self.controllerParent.channelParent.log else PRINT.VERBOSE)
        if self.deviceName:
            self.controllerParent.devicename = self.deviceName

    def setValuesAndUpdate(self, value: float, outOfRange: bool, unstable: bool, error: str = '', deviceName: str = '') -> None:
        """Set value and additional parameters before calling update in main thread.

        :param value: New value.
        :type value: float
        :param outOfRange: Indicates if signal is out of range.
        :type outOfRange: bool
        :param unstable: Indicates if measurement is unstable.
        :type unstable: bool
        :param error: Error message provided by device, defaults to ''
        :type error: str, optional
        :param deviceName: Name set on device, defaults to ''
        :type deviceName: str, optional
        """
        if self.values is not None:
            self.values[0] = value
        self.outOfRange = outOfRange
        self.unstable = unstable
        self.error = error
        self.deviceName = deviceName
        self.signalComm.updateValuesSignal.emit()

    def setRange(self) -> None:
        """Set the range. Typically autorange is sufficient."""
        rangeWidget = cast('CompactComboBox', self.controllerParent.getParameterByName(self.controllerParent.RANGE).getWidget())
        if rangeWidget:
            self.RBDWriteRead(message=f'R{rangeWidget.currentIndex()}', already_acquired=True)  # set range
        self.updateRangeFlag = False

    def setAverage(self) -> None:
        """Set the averaging filter."""
        averageWidget = cast('CompactComboBox', self.controllerParent.getParameterByName(self.controllerParent.AVERAGE).getWidget())
        if averageWidget:
            average_filter = averageWidget.currentIndex()
            average_filter = 2**average_filter if average_filter > 0 else 0
            self.RBDWriteRead(message=f'F0{average_filter:02}', already_acquired=True)  # set filter
        self.updateAverageFlag = False

    def setBias(self) -> None:
        """Set the bias voltage on or off."""
        self.RBDWriteRead(message=f'B{int(self.controllerParent.bias)}', already_acquired=True)  # set bias, convert from bool to int
        self.updateBiasFlag = False

    def setGrounding(self) -> None:
        """Set grounding off."""
        self.RBDWriteRead(message='G0')  # input grounding off

    def getName(self) -> str:
        """Get the name set on the device."""
        name = self.RBDWriteRead(message='P') if not getTestMode() else 'UNREALSMURF'  # get channel name
        if '=' in name:
            return name.split('=')[1]
        return ''

    def updateParameters(self) -> None:
        """Update Range, Average, and Bias."""
        # call from runAcquisition to make sure there are no race conditions
        if self.updateRangeFlag:
            self.setRange()
        if self.updateAverageFlag:
            self.setAverage()
        if self.updateBiasFlag:
            self.setBias()

    def command_identify(self) -> None:
        """Query and read identification and status."""
        with self.lock:
            self.RBDWrite('Q')  # put in autorange
            for _ in range(13):
                message = self.RBDRead()
                self.print(message)
        # self.print(self.RBDRead())  # -> b'RBD Instruments: PicoAmmeter\r\n'  # noqa: ERA001
        # self.print(self.RBDRead())  # -> b'Firmware Version: 02.09\r\n'  # noqa: ERA001
        # self.print(self.RBDRead())  # -> b'Build: 1-25-18\r\n'  # noqa: ERA001
        # self.print(self.RBDRead())  # -> b'R, Range=AutoR\r\n'  # noqa: ERA001
        # self.print(self.RBDRead())  # -> b'I, sample Interval=0000 mSec\r\n'  # noqa: ERA001
        # self.print(self.RBDRead())  # -> b'L, Chart Log Update Interval=0200 mSec\r\n'  # noqa: ERA001
        # self.print(self.RBDRead())  # -> b'F, Filter=032\r\n'  # noqa: ERA001
        # self.print(self.RBDRead())  # -> b'B, BIAS=OFF\r\n'  # noqa: ERA001
        # self.print(self.RBDRead())  # -> b'V, FormatLen=5\r\n'  # noqa: ERA001
        # self.print(self.RBDRead())  # -> b'G, AutoGrounding=DISABLED\r\n'  # noqa: ERA001
        # self.print(self.RBDRead())  # -> b'Q, State=MEASURE\r\n'  # noqa: ERA001
        # self.print(self.RBDRead())  # -> b'P, PID=TRACKSMURF\r\n'  # noqa: ERA001
        # self.print(self.RBDRead())  # -> b'P, PID=TRACKSMURF\r\n'  # noqa: ERA001

    # Single sample (standard speed) message parsing
    def parse_message_for_sample(self, msg) -> str:
        """Only returns response if it contains a sample.

        :param msg: Original message.
        :type msg: str
        :return: Sample string if found.
        :rtype: str
        """
        if '&S' in msg:
            return msg.strip('&')
        return ''

    def readingToNum(self, parsed) -> float:  # convert to pA
        """Convert string to float value of pA based on unit.

        :param parsed: Parsed current response from RBD.
        :type parsed: str
        :return: Current as number.
        :rtype: float
        """
        try:
            _, _, x, unit = parsed.split(',')
            x = float(x)
        except ValueError as e:
            self.print(f'Error while parsing current; {parsed}, Error: {e}', flag=PRINT.ERROR)
            self.errorCount += 1
            return self.controllerParent.value  # keep last valid value
        match unit:
            case 'mA':
                return x * 1E9
            case 'uA':
                return x * 1E6
            case 'nA':
                return x * 1E3
            case 'pA':
                return x * 1
            case _:
                self.print(f'Error: No handler for unit {unit} implemented!', flag=PRINT.ERROR)
                return self.controllerParent.value  # keep last valid value

    def closeCommunication(self) -> None:
        super().closeCommunication()
        if self.port:
            with self.lock.acquire_timeout(1, timeoutMessage=f'Could not acquire lock before closing port of {self.controllerParent.devicename}.') as lock_acquired:
                if self.initialized and lock_acquired:  # pylint: disable=[access-member-before-definition]  # defined in DeviceController class
                    self.RBDWriteRead('I0000', already_acquired=lock_acquired)  # stop sampling
                self.port.close()
                self.port = None
        self.initialized = False
        self.closing = False

    def RBDWrite(self, message) -> None:
        """RBD specific serial write.

        :param message: The serial message to be send.
        :type message: str
        """
        if self.port:
            self.serialWrite(self.port, f'&{message}\n')

    def RBDRead(self) -> str:
        """RBD specific serial read."""
        if self.port:
            return self.serialRead(self.port)
        return ''

    def RBDWriteRead(self, message, already_acquired=False) -> str:
        """RBD specific serial write and read.

        :param message: The serial message to be send.
        :type message: str
        :param already_acquired: Indicates if the lock has already been acquired, defaults to False
        :type already_acquired: bool, optional
        :return: The serial response received.
        :rtype: str
        """
        response = ''
        if not getTestMode():
            with self.lock.acquire_timeout(1, timeoutMessage=f'Cannot acquire lock for message: {message}.', already_acquired=already_acquired) as lock_acquired:
                if lock_acquired:
                    self.RBDWrite(message)  # get channel name
                    response = self.RBDRead()
        return response
