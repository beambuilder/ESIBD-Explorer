REM Start script for windows
REM based on https://www.pythonguis.com/tutorials/packaging-pyqt5-pyside2-applications-windows-pyinstaller/

do not run this as a script
run individual blocks manually and only proceed if successful
exit

::::::::::::::
REM Formatting
::::::::::::::

use Open Multiple Files extension with **/*.py to open all python files and make sure linter is analyzing all of them

typically regex is not needed if Ruff is configured correctly
use the following regex (Math case and Use Regular Expression enabled) to fix common formating errors
REM ,[a-zA-Z0-9_]| \n|\n\n\n|[^'],'| [b-hk-w] |as f:|lambda :|true|false|parameter[\. ,]|channel[\. ,]|setting[\. ,]
REM files to exclude: *.html,*.js,*.css,*.log,*.gitignore,*.bib,*.tex,*.prof,*.rst,*.txt,*.svg,*.sh,LICENSE,Makefile
temporary enable all pylint checking

::::::::::::::
REM Change Log
::::::::::::::
update changelog in changelog.rst (ideally update before each commit)
update change log title with version and release date
REM MAJOR version when you make incompatible API changes
REM MINOR version when you add functionality in a backward compatible manner
REM PATCH version when you make backward compatible bug fixes
Often, writing the change log inspires some last minute changes!
Content: start bullet points with capitals and dot at the end
- hyphens will be replaced by bullet points on github

REM Added 			for new features.
REM Changed 		for changes in existing functionality.
REM Deprecated 		for soon-to-be removed features.
REM Removed 		for now removed features.
REM Fixed 			for any bug fixes.
REM Security 		in case of vulnerabilities.
REM Performance 	for speed improvements
REM Developer Notes separate changed only relevant for developers from other sections

:::::::::::
REM Testing
:::::::::::
Make sure all test pass in development environment
If testing with all plugins enabled, consider switching to basic log messages, as very frequent debug and verboose messages can slow down testing and cause additional warnings and errors. These are not a shortcomming of the sofware but just a symptom of running with a very large amount of channels that would not occur in a real world application. Each software will reach that limit at some point.
Test with all/no plugins enabled
Test with hardware
Test after running python -m esibd.reset to simulate installation on a PC where it never ran before
Test changing config, plugin, data paths

:::::::::::::::::::::
REM Environment setup
:::::::::::::::::::::

If applicable perform clean install of virtual environment
REM start from ESIBD Explorer folder
cd setup
call create_env.bat REM make sure no other environments (including VSCode) are active during this step
cd ..
call activate esibd

If no change to the environment since last deployment following is sufficient.
call conda update -y -n base -c conda-forge conda

:::::::::::::::::::::::::::
REM Bump version
:::::::::::::::::::::::::::

use find and replace to manually update all version references
ATTENTION: do not find and replace all, as this will also overwrite the last versions in the change log!
REM update version in pyproject.toml
REM update Product Version in EsibdExplorer.ifp in the General tab
REM update PROGRAM_VERSION in config.py
REM if applicable update year in license file
REM update copyright year and release version also in docs/conf.py

REM Note that the program has to access the version during development and after deployment to test for plugin compatibility
REM Neither reading the version from pyproject.toml or from installed package using importlib.metadata.version covers both use cases,
REM requiring to update the version in both files
REM bumpversion / bump-my-version seems overkill

:::::::::::::::::::::::::::
REM Sphinx -> read the docs
:::::::::::::::::::::::::::

REM use sphinx-quickstart to generate initial configuration
REM then edit docs/conf.py to customize
call rmdir /q /s docs\_build REM delete docs/_build to generate clean documentation
call rmdir /q /s esibd\docs REM delete docs/_build to generate clean documentation
call rm -r docs\_build REM works in powershell
call rm -r esibd\docs REM works in powershell
REM -M coverage
REM update autodoc_mock_imports and correspondingly pyinstaller_hooks
REM use conda activate esibd if esibd is not already the active environment.
call sphinx-build docs docs\_build
call sphinx-build -vvv docs docs\_build REM use this to debug build errors
REM NOTE disable script blocker to properly test documentation offline
REM offline version for in app documentation (instrument computers often have no internet access)
call xcopy /i /y /e docs\_build esibd\docs
REM call docs\make.bat html

:::::::
REM git
:::::::

REM git config --global user.email "XXX@XXX.com"  # setup email
REM git config --global user.name "ioneater"  # setup user name

REM git-init  # (re)initialize current folder as git repository
REM git remote add origin https://github.com/ioneater/ESIBD-Explorer

REM git add .
REM git status
REM git commit -a -m "message"
REM git push origin main

::::::::
REM PyPI
::::::::

call rmdir /q /s dist
call rmdir /q /s esibd_explorer.egg-info
call rm -r dist REM works in powershell
call rm -r esibd_explorer.egg-info REM works in powershell

python -m build

REM pip install . REM test installation locally
REM python -m esibd.explorer  # start gui using module

twine check dist/*
REM safer to use normal terminal instead of vscode to avoid issues when pasting token
twine upload -r testpypi dist/*
REM https://test.pypi.org/project/esibd-explorer/

REM test on pypitest
conda create -y -n "estest" python=3.11 REM make sure no other environments (including VSCode) are active during this step
conda activate estest
pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ esibd-explorer
REM ==0.8.3 NOTE latest will be used if no version specified  # extra-index-url specifies pypi dependencies that are not present on testpypi
REM python -m esibd.reset  # clear registry settings to emulate fresh install
python -m esibd.explorer
REM activate all plugins for testing!
REM test software using PluginManager.test() in testmode
REM test software using PluginManager.test() with hardware!
REM Make sure VSCode or any other instance accessing the environment are not running while testing
If you need to fix something and reupload you are forced to increment the version number. To not need to update everything it is enough to temporarily append a fourth number to your version: X.Y.Z -> X.Y.Z.1 in pyproject.toml. You can drop this in your next release.

REM only upload on real pypi after testing!
REM safer to use normal terminal instead of vscode to avoid issues when pasting token
twine upload dist/*
REM https://pypi.org/project/esibd-explorer/


::::::::::::::::::::::::::::
REM create offline installer
::::::::::::::::::::::::::::
NOTE: it may take a few minutes before the new version will be used by pypi, if in doubt specify version in create_esibd_offline.bat
create_esibd_offline.bat
unpack and test using start_esibd_offline.bat

:::::::::::::::
REM pyinstaller
:::::::::::::::
open CMD in workspace folder
call rmdir /q /s pyinstaller_build
call rmdir /q /s pyinstaller_dist
call rm -r pyinstaller_build
call rm -r pyinstaller_dist
REM make sure both folders have been deleted!
conda activate esibd-offline
pip install pyinstaller
REM Run the following line to create initial spec file and pyinstaller_dist and pyinstaller_build
REM ATTENTION: Check absolute paths in Files, Shortcuts, and Build! relative paths using <InstallPath> did not work
pyinstaller start.py -n "ESIBD Explorer" --noconsole --clean --icon=esibd/media/ESIBD_Explorer.ico --add-data="esibd;esibd" --copy-metadata nidaqmx --copy-metadata nitypes --noconfirm --additional-hooks-dir=./pyinstaller_hooks --distpath ./pyinstaller_dist --workpath ./pyinstaller_build
REM --noconsole  # console can be useful for debugging. start .exe from command window to keep errors visible after crash
REM --additional-hooks-dir=./pyinstaller_hooks -> add any modules that plugins may require at run time but are not imported at packaging time: e.g. modules only imported in plugins. modules added here should likely also be added to autodoc_mock_imports in docs/conf.py
REM --onefile meant for release to make sure all dependencies are included in the exe but extracting everything from one exe on every start is unacceptably slow. For debugging use --onedir (default) Use this option only when you are sure that it does not limit performance or complicates debugging
REM --copy-metadata nidaqmx is needed to avoid "No package metadata was found for nidaqmx"
REM --copy-metadata nitypes is needed to avoid "No package metadata was found for nitypes"
REM do not modify spec file, will be overwritten

::::::::::::::::
REM InstallForge
::::::::::::::::

REM Next, create setup.exe using InstallForge
REM use EsibdExplorer.ifp and adjust absolute file paths for dependencies and setup file if applicable and update "Product Version" and update year in license section!
REM files (only change if filepaths have been changed, e.g. when releasing from different computer):
REM pyinstaller_dist\ESIBD Explorer\_internal
REM pyinstaller_dist\ESIBD Explorer\ESIBD Explorer.exe

REM NOTE without certificate users will see "publisher unknown" message during installation. $300 per year for certificate -> only if number of clients increases
REM NOTE https://installforge.net/support1/docs/setting-up-visual-update-express/ -> for small user groups installing from downloaded exe acceptable and less error prone (e.g. if online links should change).
added the following as custom shell comment to remove programfolde first to get clean installation every time: rmdir /s /q "<InstallPath>"
REM Previously manual uninstall was needed to get clean installation.

rename ESIBD-Explorer-setup.exe to ESIBD-Explorer-setup_v1.0.1.exe in pyinstaller_build

Test installation from exe before continuing

::::::::::::::::
REM git release
::::::::::::::::

REM create tag used for releasing exe later
git commit -a -m "Prepare realeasing version v1.0.1"
git push origin main
test successful build of docs -> only then continue with tags

git commit -a -m "Realeasing version v1.0.1"
git tag -a v1.0.1 -m "Realeasing version v1.0.1"
git push origin main --tags REM to include tags (otherwise tags are ignored)
REM Note that only increments in the first three digits (major.minor.patch) are considered stable releases

check read the docs build on https://app.readthedocs.org/projects/esibd-explorer/

create release on github with changelog based on commits and following sections (have to be signed in!)
select tag
Title: Version v0.8.3
Copy change log from changelog.rst (remove inline icons if applicable)
attach ESIBD-Explorer-setup_v0.8.3.exe from pyinstaller_build to release
attach esibd.tar.gz to release (do not rename or the final folder will have a different name as well)
Source code (zip) and Source code (tar.gz) will be automatically attached, even though they are not visible before clicking on Publish release


Consider saving snapshot of workspace independent of git
