@echo off
rem Double-click to start RaidBriefer in its own program window.
rem Closing the window stops the whole program. No black console window stays open.
chcp 65001 > nul
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Please run install.bat first. - 먼저 install.bat 을 실행하세요
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m app.window
