@echo off
cd /d "%~dp0.."
py -3 -m pytest --version >nul 2>&1
if errorlevel 1 py -3 -m pip install -r requirements-dev.txt
py -3 -m pytest -q
pause
