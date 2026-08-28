@echo off
REM Build a distributable folder. See tools/build.py for details.
python "%~dp0tools\build.py" %*
