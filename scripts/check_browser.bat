@echo off
rem Runs the real extension in a real browser against a local imitation of the site.
rem Usage: check_browser.bat edge   (or chrome, opera-gx)
rem The browser gets a temporary profile; the real site and your profile are not touched.
cd /d "%~dp0.."
set NEXUS_BROWSER=%~1
if "%NEXUS_BROWSER%"=="" set NEXUS_BROWSER=edge
py -3 -m pytest tests/browser_check -v
pause
