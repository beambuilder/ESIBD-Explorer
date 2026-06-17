REM This file can be used to bundle ESIBD-Explorer and all dependencies for use on a computer without internet connection
REM After running this, extract the content of esibd.tar.gz as a local environment (e.g. to \AppData\Local\miniconda3\envs\esibd or any other location)
REM Then start the program using start_esibd_offline.bat

call conda activate base
call conda install -y -c conda-forge conda-pack
call conda create -y -n "esibd-offline" python=3.11
call conda activate esibd-offline
call pip install esibd-explorer
REM typically version does not need to be specified
REM call pip install esibd-explorer==1.0.1
call conda activate base
REM Note: add any additional custom requirements here
REM call conda install your-requirements
call conda pack --force -n esibd-offline -o esibd.tar.gz
REM Make sure files are copied to local drive before unpacking
call cmd /k REM keep batch open to inspect success

