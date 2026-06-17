.. include:: icons.rst

Changelog
---------

Version 1.0.1 2026-04-20
========================

Added
~~~~~
- There is now a search field in |settings| :ref:`sec:settings`.
- Added a new minimal |webcam| :ref:`sec:webcam` plugin.
- Plugins in the |pluginDialog| plugin dialog are now sorted by name and enabled state and user can change sorting.
- Added wake mode in |advanced| advanced mode of |settings| :ref:`sec:settings` to prevent screen lock and sleep. In some situations screen lock can not be disabled. In some situations screen lock and sleep could limit file access and cause program instability.
- |omnicontrol| :ref:`sec:omnicontrol` plugin overhaul: Can now also control Pfeiffer turbo pumps.
- In Test Mode there are now warning labels to make sure users are aware of simulated data.
- The plugin source code file path is now shown in the plugin about dialog.
- Introduced option to record additional parameters just like values or backgrounds.
  Set recorded=True in for parameters that should be recorded. If applicable, also set the parameter unit and logY mode here.
  The recorded values for these parameters will be saved and restored.
  In the |ucm| :ref:`sec:ucm`, these and any other parameters can be linked directly by using ChannelName.ParameterName.
  Note that linking parameters can used in the same way for Scans.
  In the device specific live displays they can be shown using the context menu of the display parameter of the corresponding channel.
  The parameter will inherit most plot settings from the associated channel but the color will be altered slightly.
  See |omnicontrol| :ref:`sec:omnicontrol`, |rspd3303c| :ref:`sec:rspd3303c`, and |keithley| :ref:`sec:keithley` for example use cases.
- Added new device plugin |spa| :ref:`sec:spa` for electron plus picoammeters.
- ChannelName.ParameterName can now also be used in equations.
- Added Min Max limits for the Setpoint in |pid| :ref:`sec:pid`.

Changed
~~~~~~~
- Defined scaling of channels more gradually.
- Preview of hdf5 files in the |tree| :ref:`sec:tree` plugin now also previews the content of datasets as a tooltip.
- In |beam| :ref:`sec:beam` scan the color scale will be inverted for negative scans.
- |depo| :ref:`sec:depo` is now able to display all current and charge traces.
- |pico| :ref:`sec:pico` now hides data channel, data type and number of wires for virtual channels.
- |rbd| :ref:`sec:rbd` now uses np.nan instead of 0 if there was an error parsing the current.
- Made dashes shorter so they are easier to find in pyqtgraph legend.
- Allowed use of % as unit.
- Sending Settings, Channels, and Parameters to the console is now always enabled and not just in Debug Mode.
- Prevented dragging cursors in scans while scan is running.
- |rspd3303c| :ref:`sec:rspd3303c`: Improved shutdown timer messages.
- Made "Error reset time", " Max lag tolerance", and "Lag wait time" accessible in the General section of |settings| :ref:`sec:settings` in |advanced| advanced mode. See tooltips for documentation.
- Made "max error count" accessible in the Device settings in |advanced| advanced mode. See tooltip for documentation.
- Plugins are now linked to their documentation in the changelog. Using plugin icons in documentation.
- Using channel specific icons for linked channels.
- Channel names can no longer use the . character as . has a special meaning in the context of ChannelName.ParameterName, but it can still be allowed for individual devices if necessary.
- Allowed all channels to reference all other channels in equations. Previously Input devices could only reference channels from other input devices.
  Make sure all channels have unique names!
- For devices that use getDisplayUnit and convertDataDisplay to change the display unit, these definitions will also be used in the liveDisplay of |ucm| :ref:`sec:ucm`.
  Note that in the list of channels the original unit will always be used consistently.

Fixed
~~~~~
- Made sure that liveDisplays are updated after dragging even if devices are not recording.
- Ensured devices like |keithley| :ref:`sec:keithley` where each channel has a controller turn on properly if started from on button without initializing communication first.
- Added validation for closing |ls335| :ref:`sec:ls335`.
- Hidden internal text visible when selecting plugins in |pluginDialog| plugin dialog.
- Fixed displayed number of maximal errors for devices in the settings tooltip.
- Resetting error count of virtual channels when reinitializing communication.
- Fixed estimation of required storage to keep device restore files within the specified maximum storage.
- Fixed crash due to inconsistent channel values and channel background. Instead showing warning and restoring consistency.
- Fixed issue on |omnicontrol| :ref:`sec:omnicontrol` with loading values with mixed channel data type. Make sure that the default channel data type is suitable for exporting and restoring data of all other types. E.g. if you mix float (pump speed) and exp (pressure), use exp as the default data type.
- Fixed loss of history after loading configuration by storing before and loading after loadConfiguration.
- Fixed an issue where scans were left in the loading state when failing to load data from a file.
- Preventing empty channel names and names with spaces at the start or end.

Developer Notes
~~~~~~~~~~~~~~~
- Added closing flag for DeviceController to prevent recursion if lock can not be acquired while closing communication.
- |omnicontrol| :ref:`sec:omnicontrol`: Removed dependence on pfeiffer-vacuum-protocol package.
- Added testing loading entire configuration to the DeviceManager tests.
- Added IntelliSense support for dynamically imported dependencies (e.g. for |ga| :ref:`sec:genetic_algorithm`).
- Introduced ChannelManager.defaultChannel which is initialized in the main_thread and can be used as a reference e.g. for saving data from a parallel thread.
- DeviceManager is no longer waiting for scans that did not initialize successfully in automated testing.
- Added extraContextActions for parameters and used it to add acknowledge error, set standby speed, and change address options to corresponding parameters in |omnicontrol| :ref:`sec:omnicontrol`.
- Renamed getUnit to getDisplayUnit to emphasize where it should be used.

Version 0.8.2 2025-06-23
========================

Added
~~~~~
- Added highlighting to easier see which devices are turned on.
- |ls335| :ref:`sec:ls335` is only showing PID controls for active channels. If the channel is only used for reading a temperature PID controls can be ignored.
- CustomPlugin now demonstrates how to interact with and even extend other plugins including internal plugins.

Fixed
~~~~~
- |pid| :ref:`sec:pid` reacts instantly when setpoint changed.
- Fixed some settings that were not set correctly on new PCs.
- Fixed setting of range and average for |rbd| :ref:`sec:rbd`.
- Fixed file used when exporting scan plots.
- Preserving new lines from documentation on the about dialog.

Removed
~~~~~~~
- The Pressure plugin has been removed. Use |tic| :ref:`sec:tic` and |maxigauge| :ref:`sec:maxigauge` instead.

Version 0.8.1 2025-06-22
========================

Added
~~~~~
- The |tree| :ref:`sec:tree` plugin can now filter content based on a search field.
- In Debug mode, items from the |tree| :ref:`sec:tree` can be added to the |console| :ref:`sec:console`.
- Added option to liveDisplays to toggle between |scaleX_auto| automatically or |scaleX_manual| manually scaling the x axis.
- Integrated generating and running python plot code for tests of devices, scans, and displays.
- Added log level setting to determine level of information in logs.
- Introduced log level 'Trace' which shows detailed device communication info if implemented by device.
- Tests now write a log to a dedicated file in the current session path.
- In |advanced| advanced mode of |settings| :ref:`sec:settings`, the error count for controllers of devices and channels is now shown.
- Input channels are now shown in the channel list of scans.
- Added icons to change log and documentation.
- Input and output messages from |console| :ref:`sec:console` will now appear in terminal and log file.
- Added |ls335| :ref:`sec:ls335` plugin.
- Added advanced option invalidWhileWaiting to Scans to allow to ignore values while signal is stabilizing.
- Added |customDevice2| :ref:`sec:customDevice2` as an example of how to create a custom plugin that replaces an internal plugin while using the same files.
- In Debug mode, plugins will be added to |console| :ref:`sec:console` when accessing the about dialog.

Changed
~~~~~~~
- Added confirmation prompt before deleting all device history.
- Added some pauses during testing if GUI gets too busy.
- Simplified instructions for offline installation. No conda or python installation needed on offline PC!
- Disallowed use of / in channel names.
- In liveDisplays, the mouse enabled mode of subplots is now linked.
- Data files are now only saved while closing if new data has been recorded since starting the program.
- While testing, images are written to files instead of clipboard for later inspection and to not spam the clipboard.
- Instead of showing the last valid value after stopping acquisition, NAN is displayed to indicate that the current value is unknown.
- Changed smoothing algorithm to preserve sections with less datapoint than smoothing window.

Fixed
~~~~~
- Fixed error when canceling loading a file from DeviceManager.
- Changing channel limits will now also update referencing channels (e.g. in |ucm| :ref:`sec:ucm` or |pid| :ref:`sec:pid`).
- Fixed liveDisplay copyClipboard function.
- Scans will now append session notes to scan notes. Used to be notes of current directory in explorer.
- Fixed update of colors of in linked channels.
- Fixed issue with scans starting even through output channels were not recording.
- Fixed an issue with uninitialized scan plots after changing scan channels.
- Fixed update of channel values in interactive mode of |omni| :ref:`sec:omni`.
- Fixed issue where channel equations tried to access backgrounds for channels that do not use backgrounds.
- Preventing error when changing directories to fast by ignoring double click while loading.
- Fixed an issue where the data type of channel values would be changed to float64 when extending the dynamic numpy array.
- Instead of reloading plugins, the application performs a clean restart. This is to avoid memory leaks from incompletely closed plugins.
- Fixed |tic| :ref:`sec:tic`, |iseg| :ref:`sec:iseg`, and |temperature| :ref:`sec:temperature` after testing with hardware.
- Fixed testing of internal displays.
- Splash screen is only on top of ESIBD Explorer but not on top of all other applications.

Removed
~~~~~~~
- Removed option to turn of logging.

Developer Notes
~~~~~~~~~~~~~~~
- Simplified plugins by introducing default implementations of DeviceController.values, DeviceController.applyValue, DeviceController.updateValues,
  DeviceController.initComplete, DeviceController.toggleOn, Channel.applyValue, Device.closeCommunication, and Device.setOn.
  Custom plugins need to be adjusted by deleting or simplifying functions to use default implementation!
- Fixed sequence for extending DeviceController.closeCommunication. Adjust custom plugins based on documentation and examples!
- Changed implementation of initScan. Extend addInputChannels instead.
- Abandoned use of :meta private: where it was overwriting the docstring of a parent method.
- Added documentation for many methods.
- generatePythonPlotCode method now only returns text. When running generated code from the context menu, other plots are no longer changed into popups.
- Removed unused getInitializedChannels method.
- Restructured project structure to separate scans and displays. Using lower case convention for project folders and module files. Enabling simplified import statements,
  e.g.: from esibd.scans import Omni, from esibd.devices import ISEG, from esibd.displays import PDB.
- Introduced restore option for parameters and settings and used it to not restore values for lagging, errorCount, and interval measured.
- Removed option to edit indicators through the context menu.
- Enabled context menu for LedIndicators.
- Added type hints and improved consistency of formatting.
- Replaced EsibdConst and EsibdCore with esibd.const and esibd.core
- Completed type hinting and implemented multiple improvements suggested by ruff.
- Removed aboutFunc argument from finalizeInit.
- Renamed _parent to more descriptive name in DeviceController, TimeoutLock, PlotItem, PlotWidget, Parameter, Setting.
- Renamed _min, _max with minimum, maximum for Setting, Parameter, and DynamicNp.
- Renamed _from and to in scans to start and stop. Change does not affect user interface and files!
- Renamed PluginManager.TYPE to PLUGINTYPE.
- Renamed Parameter.TYPE to PARAMETERTYPE and widgetType to parameterType.
- Renamed outputs to outputChannels and inputs to inputChannels for scans and staticDisplays.
- Renamed scan.run to scan.runScan
- Removed acquiring argument in DeviceController.runAcquisition. Use self.acquiring instead.
- Renamed QLabviewSpinBox, QLabviewDoubleSpinBox, QLabviewSciSpinBox to LabviewSpinBox, LabviewDoubleSpinBox, LabviewSciSpinBox.
- Merged deviceController.device and deviceController.channel into deviceController.controllerParent.
- Renamed channel.device to channel.channelParent.
- Added type hints and refactored code based on type checking.
- Reimplemented MultiStateAction labels with Enums to enable type checking.
- Changed Device.initialized() to Device.initialized.
- Removed runAcquisition in DeviceControllers where obsolete.
- Fixed handling of NaN for LabviewSpinBox, LabviewDoubleSpinBox, and LabviewSciSpinBox.

Performance
~~~~~~~~~~~
- Improved GUI responsiveness handling: Data is still appended but plots are skipped if needed.
- Scans and devices will pause if the GUI gets too busy. Scans will wait for previous scan step to be processed if necessary.
- Increased performance by delaying update of matplotlib figures while resizing.
- Increased performance by suspending plotting in liveDisplays while resizing.
- Increased performance by suspending update in liveDisplays while resizing.


Version 0.7.3 2025-03-24
========================

Added
~~~~~

- Added icon legend in about dialogs |help|.
- Added |calculator| :ref:`sec:calculator` plugin as an example of embedding external GUI applications as plugins.
- Running scans will be indicated by icons in the DeviceManager.
- Added lagging indicator in advanced mode of device settings.
- Added option to filter warning and error messages in the |console| :ref:`sec:console`.

Changed
~~~~~~~

- Improved the definition of the channel properties active and real in the documentation.
- Treating file types not case sensitive.
- New icons for msScan and MS.
- Simplified TIC plugin implementation by inheriting from OMNICONTROL.
- Made layout more stable by preventing floating of |console| :ref:`sec:console`, |settings| :ref:`sec:settings`, and |browser| :ref:`sec:browser`.
- Some development specific settings are now hidden by default and only visible in the advanced mode |advanced| of the |settings| :ref:`sec:settings`.
- Channel backgrounds are only displayed and used for channels that are enabled, active, and real.
- Scans will warn and not start if scan limits exceed limits of scan channels.

Fixed
~~~~~

- Fixed assignment of values to virtual channels in test mode for several internal plugins.
- Improved colors of displays when copied to clipboard or saved as pdf.
- Renaming of settings is handled like any other setting change, triggering appropriate events.
- Restoring backgrounds after moving channels.

Performance
~~~~~~~~~~~
- Tolerating more device interval deviation and skipping plotting and data recording if needed to make application more responsive and stable when using close to maximum resources.

Version 0.7.2 2025-03-02
========================

Added
~~~~~

- Added update information on starting screen.
- Added option to show an overview of all icons (enter Tree.iconOverview() in |console| :ref:`sec:console`).
- Added Error count setting for devices to stop communication after multiple consecutive errors.
- Added option to record |video| videos of plugins.
- Added option to highlight mouse clicks.
- Added offline installation instructions.
- Added option to run python files from explorer context menu.
- Added options to load all device values from file context menu instead of one device at a time.
- Added option |blue-folder-import| to load all device channels and values from |advanced| advanced mode of |deviceManager| :ref:`sec:deviceManager` instead of one device at a time.
- Added simple video example to the documentation.

Changed
~~~~~~~
- Using plugin names more consistently in tooltips.
- Using explicit tooltips for linked channels in |ucm| :ref:`sec:ucm`, |pid| :ref:`sec:pid`, and scans.
- Reintroduced start recording and subtractBackground actions for live displays (linked to corresponding action in parent Plugin).

Fixed
~~~~~
- Fixed scaling when copy image to clipboard in live displays.
- Updating linked channels when renaming source channels.
- Dialogs stay on top of all windows.
- Fixed help dialog for displays.

Removed
~~~~~~~
- Removed display time control for device manager (use |ucm| :ref:`sec:ucm` to see multiple channels with consistent time).

Version 0.7.1 2025-02-08
========================

Added
~~~~~

- Added new icons for |ucm| :ref:`sec:ucm`, Line |line|, and |deviceManager| :ref:`sec:deviceManager`.
- Added change log to readthedocs.
- Added Linux installation instructions.
- Generalized opening files and storing settings to work on Linux.
- Limiting valid characters for channel names.
- Adding messages emitted during initialization to |console| :ref:`sec:console`.
- Added itemFile and itemFileDark to simplify specification of icons and show icons in PluginManager.
- Added program info to plugins.ini

Changed
~~~~~~~

- Removed checkbox for non optional plugins in |pluginDialog| plugin dialog.
- Prevent DeviceManager from moving or floating.
- Disable navigation icons in explorer while loading directory to avoid inconsistent behavior.
- |pluginDialog| Plugin dialog can now be accessed while devices are communicating. Communication will only be stopped when reloading plugins.
- Improved formatting of values in .ini files and change logs.
- Prevent loading of channels while recording.
- |pico| :ref:`sec:pico` only loads SDK if user has explicitly enabled this plugin.
- Using dark mode background instead of black for scans.
- Devices only store data regularly if they are actively recording new data.
- Waiting for scans to finish when closing.
- |ucm| :ref:`sec:ucm`, |pid| :ref:`sec:pid`, and Scan channels show background corrected values if applicable.
- |depo| :ref:`sec:depo` is using color of selected current channel.

Fixed
~~~~~

- Reconnecting source channels after loading device configuration.
- Fixed error caused by missing console history file.
- Fixed |clipboardImage| copyClipboard in light theme
- Fixed repeated plotting when loading scans.
- Fixed scan channel initialization for |depo| :ref:`sec:depo`.
- Using display parameter for |omni| :ref:`sec:omni`.
- Removed display parameter for other scans.
- Implemented proper file handling for |ucm| :ref:`sec:ucm` and |pid| :ref:`sec:pid`.
- Fixed incomplete status messages
- RSPD3303C only sets values for enabled channels.

Version 0.7.0 2025-01-19
========================

This version brings multiple improvements to the user interface and messages.
The main new features include the Universal Channel Manager (|ucm| :ref:`sec:ucm`), |pid| :ref:`sec:pid` Plugin, and a channel interface for scans.
The data and config file formats remain unchanged. The plugin file format is significantly simplified but requires adjustments (see below) to use old plugins with the current version!

Added
~~~~~

- Universal Channel Manager (|ucm| :ref:`sec:ucm`) plugin: This plugin allows to reference arbitrary channels from other devices to create a central list of the most important information.
  All referenced channels can be controlled from here. The corresponding display allows to see recorded data from multiple devices in one central location.
  For most users this should result in improved performance and less complexity as the individual device tabs and corresponding displays will rarely be needed once the channels in the |ucm| :ref:`sec:ucm` are configured.
- |pid| :ref:`sec:pid`: Allows to establish a PID control loop between two arbitrary channels.
- Added option to |collapse| collapse channels of same color to focus on the most relevant channels.
- Channels now allow to change Line Style and Group for plotting.
- Channels now allow to change Scaling to highlight important channels (and to see them from the other side of the lab!).
- Added channel interface for scans.
- |depo| :ref:`sec:depo` can now record data from arbitrary additional channels.
- Added option to inspect the object currently in the Console input.
- Option to use icons instead of labels in tab bars (active by default).
- Live Displays allow to sort plot curves by device, unit, or group (new channel parameter), and arrange them horizontally, vertically, or stacked.
- Added plugins: |keithley| :ref:`sec:keithley`, |mips| :ref:`sec:mips`, |ni9263| :ref:`sec:ni9263`, |omnicontrol| :ref:`sec:omnicontrol`, |rspd3303c| :ref:`sec:rspd3303c`, and |pico| :ref:`sec:pico`.
- Added option to generate plot files for displays including |ms| :ref:`sec:ms`, |line| :ref:`sec:line`, |pdb| :ref:`sec:pdb`, |holo| :ref:`sec:holo`.
- Plugin Manager now shows the supported version of plugins and highlights if they are compatible with the current program or not.

Changed
~~~~~~~

- Plugin format: Much of the functionally has been moved to the base class allowing developers to use standard functionality by using a flag (useMonitors, useDisplays, useBackgrounds, useOnOffLogic)
  instead of implementing it in the specific plugin file. Some functions have been renamed to be more descriptive and consistent.
  Most important examples are: stop -> closeCommunication, init -> initializeCommunication, apply -> applyValues.
  Make sure to compare to build in examples and test your plugins when updating your custom plugins for the current version.
  Documentation in |customDevice| :ref:`sec:customDevice` has been improved to demonstrate and explain the current plugin format.
- Logging is now enabled by default. More informative status, warning, and error messages. A lot more messages in debug mode.
- Using icons for messages, warnings, and errors in status bar, log file, and Console
- Old logs are regularly removed from the log file
- Reorganized internal device plugins in dedicated folders
- Temporary parameters like monitors or other device states are now saved but not restored.
- Parameters that are undefined before communication to the corresponding device is established are set to NaN to emphasize that there is no up to date value available.

Deprecated
~~~~~~~~~~

- Splitting Pressure plugin into dedicated TIC and MAXIGAUGE plugins. If necessary channels can be combined using |ucm| :ref:`sec:ucm`. Pressure plugin is now deprecated and will be removed in the future.

Fixed
~~~~~

- Replaced deprecated :code:`numpy.bool8` with :code:`numpy.bool_`
- Various minor bug fixes

Performance
~~~~~~~~~~~

- Various performance improvements
- Improved speed and stability of tests. Time is logged during testing if in debug mode.

Version 0.6.18 2024-06-10
=========================

Added
~~~~~

- Tree.inspect now shows values if applicable
- Console restores history of used commands after restart
- Introduced setDisplayDecimals to customize display of floats including scientific notation
- Added variable aspect ratio option for |beam| :ref:`sec:beam` (varAxesAspect in autogenerated plot file)
- Added new scan mode |spectra| :ref:`sec:spectra` for a series of 1D spectra based on |beam| :ref:`sec:beam`
- Added new UI tool MultiStateAction
- Added dedicated channelPlot to display channel data instead of using Line plugin
- Added msSpectra mode for simple mass spectra
- Added PluginManager.testing flag to avoid interaction of parallel testing thread with UI dialog boxes leading to rare crash during testing

Changed
~~~~~~~

- Communication has to be stopped before channels can be moved (increase stability)
- Default ini files are scanned for changes when closing and only overwritten if necessary
- Using last 10 s instead of last 10 data points to define background signal
- Scans now respect the subtractBackground states defined by the devices of the relevant channels
- Virtual channels do not need to be active to be included in scans

Fixed
~~~~~

- Update to pyqtgraph==0.13.7 after replacing deprecated api

Performance
~~~~~~~~~~~

- Plotting performance improved by reusing figures (figure recreation still needed if theme is changing)

Version 0.6.17 2024-03-18
=========================

Added
~~~~~

- Added popup to show errors while importing plugins (before the Console plugin is ready to display those errors.)
- Added Smooth parameter to all channels to reduce noise using running average.

Changed
~~~~~~~

- Live displays are visible by default
- Test mode active by default
- Stop all communication and recording from DeviceManager, now requires explicit confirmation
- Added warnings for output channels that are not enabled, or their device is not initialized or not recording
- Pressure plugin: init TIC and TPG decoupled so you can use it with only one of them or both.

Removed
~~~~~~~

- Removed explicit save of settings on program termination (settings are saved in real time)

Version 0.6.16 2023-12-17
=========================

First stable release on pipy

Added
~~~~~

- the deposition plugin now shows a checklist for validation before it starts recording
- added option to use dark or light theme when copying graphs to clipboard
- added getting started section in docs
- added PluginManager.showThreads() function for debugging
- added minimal support to restore plugin dimensions after restart
- added exponential temperature change for Temperature plugin in test mode
- added option to reset local settings using python -m esibd.reset

Changed
~~~~~~~

- channels can now only be enabled and disabled in advanced mode
- live displays are now visible by default after initial installation

Fixed
~~~~~

- acquisition is no longer stopped when loading scan or device settings
- added input validation of session path
- fixed issue with autoscaling in static displays

Performance
~~~~~~~~~~~

- increased speed of TIC pressure communication by using correct EOL character

Version 0.6.14 2023-11-07
=========================

First release public on PyPi

Version 0.6
===========

This is the first public version. All changes relative to this version will be documented in future releases.
Note, the file formats have not been altered or altered in a backwards compatible way that automatically adds default values for parameters that do not exist in old files.

Added
~~~~~

- standalone installer available for windows

Changed
~~~~~~~

- internal plugins are kept in the program folder and may be affected by updates.
  Users can specify a plugin path and add custom plugins, but are responsible to maintain them.
- Complete overhaul of user interface and docking system.

Version 0.5
===========

Update notes: For a save update, backup and remove all files from your config folder.
These files will be automatically generated in the correct format.
You may then adjust them as necessary based on your backed up files, while sticking to the new format.
All device and display code is now organized as plugins.

Version 0.4
===========

Update notes: You need to delete settings.ini and config.h5 from your settings folder.
Updated versions will be generated on the first start after the update. You can then manually restore your settings.
In the future, file format changes during updates will be automated.
In this release, the definition of lens and name in the current tab were changed to name and deviceName to increase consistency.
Please adjust your settings accordingly after the first start.

Added
~~~~~

- Added "Depo" tab to display and document deposition.
- Added "Move to Recycle Bin" feature to explorer items.
- The device configuration can be exported directly to the current session.
- The most common current plot functions are now directly attached to the current plot and always accessible.
  In addition, the voltage button moved here to indicate the voltage status at all times and allow quick access in case of an emergency.
- The position on the |beam| :ref:`sec:beam` is indicated by a cursor.
- The manual can be accessed from the Help menu.
- The optimization progress is shown in a dedicated tab while running.
- Color bar for |beam| :ref:`sec:beam` is labeled.
- Added custom device tab example. See new section in documentation for details.
- The Testmode can now be activated directly from the settings menu and stays active if the application is restarted
- All device settings are saved together with the measurement data in a single .h5 file. Values can be loaded by right clicking the file or importing from the corresponding device tab.

Changed
~~~~~~~

- Result of mass analysis is shown in plot.
- The initial signal is used instead of 0 to initialize |beam| :ref:`sec:beam`, making to displayed color range more useful.
- Background subtraction is active by default, though there is no effect until backgrounds are defined.
- More content is dockable.
- Devices will now generate default .ini files if no file is found. This can be useful to make sure the files are valid before populating them with additional channels.
- Settings are saved directly, not just when exiting program.
- The performance and error messages of the equation evaluation routine have been improved. Standard math functions can be used and there are no special requirements for spaces or brackets.

Fixed
~~~~~

- Combo boxes can no longer change accidentally by mouse wheel scrolling.
- Fixed live update of min max limits for voltage channels.
- Scans cannot be started before the previous scan has been saved.
- Made sure that potentials are reset to initial values before scans are saved.
- Displays and channels are no longer overwritten when loading scan settings. Instead, only non existing items from the file are added to the present list.
- Current channels are reset and removed from plot when disabled by the user.
- The file labels on top of figures are scaled to the figure width to be always visible.
- Changing the display channel during and after scanning is now more reliable.
- Mass spec data display interpolates data before down sampling for more accurate display.

Performance
~~~~~~~~~~~
- Explorer will only index files as the tree is expanded.
- If possible, scan plots only update data to improve performance.

Version 0.1 2022-03-08
======================

Fist version used in the ESIBD lab.