"""Define program specific constants.

Replace when using the tools provided by ESIBD Explorer to create a separate software.
"""

from pathlib import Path

from packaging import version

COMPANY_NAME = 'ESIBD LAB'
PROGRAM_NAME = 'ESIBD Explorer'
ABOUTHTML = f"""<p>{PROGRAM_NAME} controls all aspects of an ESIBD experiment, including ion beam guiding and steering, beam energy analysis,
                    deposition monitoring, and data analysis.<br> Using the build-in plugin system, it can be extended to support additional
                    hardware as well as custom controls for data acquisition, analysis, and visualization.<br> Read the docs online at
                    <a href='https://esibd-explorer.rtfd.io/'>http://esibd-explorer.rtfd.io/</a> or <a href='{(Path(__file__).parent / 'docs/index.html').resolve()}'>offline</a>
                    for more details.<br><br>
                    Github: <a href='https://github.com/ioneater/ESIBD-Explorer'>https://github.com/ioneater/ESIBD-Explorer</a><br>
                    Rauschenbach Lab: <a href='https://esibd.web.ox.ac.uk/home'>https://esibd.web.ox.ac.uk/home</a><br>
                    Present implementation in Python/PyQt: ioneater <a href='mailto:ioneater.dev@gmail.com'>ioneater.dev@gmail.com</a><br>
                    Original implementation in LabView: rauschi2000 <a href='mailto:stephan.rauschenbach@chem.ox.ac.uk'>stephan.rauschenbach@chem.ox.ac.uk</a><br></p>"""
PROGRAM_VERSION = version.parse('1.0.1')  # major.minor.patch/micro
esibdPath = Path(__file__).parent
internalMediaPath = esibdPath / 'media'
PROGRAM_ICON = internalMediaPath / 'ESIBD_Explorer.png'
SPLASHIMAGE = [internalMediaPath / f'ESIBD_Explorer_Splash{i + 1}.png' for i in range(4)]
