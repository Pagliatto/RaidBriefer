@echo off
rem Double-click to start RaidBriefer in its own program window.
rem Closing the window stops the whole program. No black console window stays open.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Please run the install file first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m app.window
