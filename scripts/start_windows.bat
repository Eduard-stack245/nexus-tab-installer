@echo off
setlocal
cd /d "%~dp0.."
rem The portable build carries its own Python: nothing is installed on this computer.
if exist "runtime\python\pythonw.exe" (
  start "" "runtime\python\pythonw.exe" -m nexus_tab_installer.ui.flet_app
  exit /b 0
)
rem Working from the source code: uses the Python of this computer.
where py >nul 2>&1
if errorlevel 1 (
  echo Python 3.10 or newer is required.
  echo Download it from https://www.python.org/downloads/ and install it,
  echo then run START_WINDOWS.bat again.
  pause
  exit /b 1
)
rem First launch provisions the Python dependencies automatically.
py -3 -c "import flet, flet_desktop, py7zr, rarfile, pywinauto, psutil" >nul 2>&1
if errorlevel 1 (
  echo First start: installing required Python packages, this takes a few minutes...
  py -3 -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Could not install the packages. Check your internet connection and Python.
    pause
    exit /b 1
  )
)
set "PYTHONPATH=%CD%\src"
py -3 -m nexus_tab_installer.ui.flet_app
if errorlevel 1 pause
