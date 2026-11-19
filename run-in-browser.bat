@echo off
rem Opens RaidBriefer in your web browser instead of the program window.
rem Close this window (or press Ctrl+C) to stop the program.
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
".venv\Scripts\python.exe" -m app.server --open
pause
