Welcome to ESIBD Explorer's documentation!
==========================================

The *ESIBD Explorer* controls various devices that are used in experimental setups,
such as power supplies, temperature sensors, pressure sensors, current meters, lasers and many more.
Originally developed for :ref:`Electrospray Ion-Beam
Deposition<sec:ESIBD>` (ESIBD), it has been used to optimize and
characterize ion-beam energy, beam intensity, and beam size,
as well as monitoring deposited charge.

Thanks to a plugin system and templates it is now possible
to add support for custom devices, scan modes, and more using only
a few lines of code. *ESIBD Explorer* can therefore be used for a broad range of
experiments beyond ESIBD.

*ESIBD Explorer* follows the core philosophy that the user should only see and interact with physically relevant experimental parameters.
Core information and features should be accessible with as little clicks as possible.
This is to allow researchers to focus on the science, instead of getting distracted by hardware specific implementations and repetitive manual tasks.

It **does** make it easy to generate
a consistent user interface for your custom hardware and data, and takes
care of saving and loading data, metadata, and settings.
Generated files allow to restore all relevant settings to reproduce an experiment.

It **does not** write device specific code for you. It is recommended to create and test a
standalone script for all required device communication before creating
your custom plugin. It **does not** eliminate the need to learn basic
Python and PyQt independently if you want to create more advanced plugins.
That said, the existing plugins can be a great resource to get started and in many cases it is sufficient to inherit from an existing plugin and make minor modifications.

Most features should be self-evident from the user interface allowing to use *ESIBD Explorer* intuitively and without memorizing a lengthy manual.
(see tooltips and integrated plugin documentation!). However, there is a :ref:`User’s Guide<sec:UsersGuide>` that covers the main functionality
in more detail to make sure you can make the most use of it. Please get
in touch if you have any `feature requests <ioneater.dev@gmail.com>`_ or `bug reports <https://github.com/ioneater/ESIBD-Explorer/issues>`_.

*ESIBD Explorer* is implemented based on Python 3.11 and PyQt6. It is
running on Microsoft Windows 10 or higher. Use on Linux and MacOS is possible
but not extensively tested. *ESIBD Explorer* is distributed under the
GNU General Public License. There is no guarantee in case of any hardware damage or data
loss due to malfunction of *ESIBD Explorer*. Users are responsible for
backing up their own data and configuration files.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   pages/install
   pages/esibd
   pages/users-guide
   pages/plugin-development
   library
   pages/changelog
   pages/acknowledgments
   pages/references

Links
=====

* :ref:`genindex`
* Online Documentation: https://esibd-explorer.readthedocs.io/en/stable/index.html
* Source code: https://github.com/ioneater/ESIBD-Explorer
* PyPI: https://pypi.org/project/esibd-explorer
* Contact: ioneater.dev@gmail.com