.. include:: icons.rst

.. _`sec:plugin_system`:

Plugin development
==================

If you are a user of *ESIBD Explorer* you can skip this section. Please
read on if you want to extend the functionality by adding plugins or
adapt the software for a different type of experiment.

The main code does not need to be modified to add support for additional
devices and other features. Instead, you can add :class:`~esibd.plugins.Plugin` that will be
automatically integrated into the user interface. In fact, everything
the user sees in *ESIBD Explorer* is a :class:`~esibd.plugins.Plugin`. This section will discuss
the underlying framework that enables implementation of and communication between different
:class:`devices<esibd.plugins.Device>`, :class:`scans<esibd.plugins.Scan>`, and other :class:`plugins<esibd.plugins.Plugin>`.

To add plugins, all you need to do is prepare a plugin file inside a
sub folder of the user defined :ref:`plugin path<plugin_path>`. A plugin file is a python
script that defines a :meth:`~esibd.provide_plugins.providePlugins` function, which returns one or
multiple :class:`plugins<esibd.plugins.Plugin>`.
:class:`Plugins<esibd.plugins.Plugin>` can be enabled in the plugin dialog |pluginDialog|
found in :ref:`sec:Settings` |Settings| after restarting the software. It is
recommended that your custom plugin classes inherit directly from
:class:`~esibd.plugins.Plugin`, :class:`~esibd.plugins.Device`, :class:`~esibd.plugins.Scan`, or from one of the other built-in plugins.
All built-in :class:`plugins<esibd.plugins.Plugin>` can be imported from :mod:`esibd.plugins` and the corresponding modules in the devices, displays, scans, and examples folders.
Many other helpful classes and methods can be imported from :mod:`esibd.core`.

If you need to extend the internal plugins just give them another name and make sure to unselect the original one in the plugin manager dialog.
Core plugins, like :ref:`sec:explorer`, :ref:`sec:settings`, :ref:`sec:console`, :ref:`sec:browser`, etc., can also be extended using *extended.py* and *provide_plugins.py*.

Custom plugins can be shared with other users in a `separate repository <https://github.com/ioneater/ESIBD-Explorer-plugins>`_.

If you want to do something completely different to the already
implemented functionality, get in touch and see if we can implement a
base class that can be reused for similar projects in the future and keeps your custom plugin code minimal.

Plugin
------

.. automodule:: esibd.plugins.Plugin
   :noindex:

PluginManager
-------------

.. automodule:: esibd.core.PluginManager
   :noindex:

Devices
-------

.. automodule:: esibd.plugins.Device
   :noindex:

Channels
--------

.. automodule:: esibd.core.Channel
   :noindex:

Scan
----

.. automodule:: esibd.plugins.Scan
   :noindex:

DeviceController
----------------

.. automodule:: esibd.core.DeviceController
   :noindex:
