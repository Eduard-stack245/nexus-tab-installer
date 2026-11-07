@echo off
setlocal
cd /d "%~dp0.."
py -3 -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (pause & exit /b 1)
set "PYTHONPATH=%CD%\src"
py -3 -m PyInstaller --noconfirm --clean --windowed --name NexusTabInstaller --paths src src\nexus_tab_installer\ui\flet_app.py
pause
