@echo off
rem RaidBriefer setup (run from source): double-click once.
rem 1) check Python  2) create the virtual environment (.venv)  3) install packages
chcp 65001 > nul
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo [!] Python not found. Install Python 3.12+ from https://www.python.org/downloads/
  echo     and check "Add python.exe to PATH" in the installer.
  echo     파이썬이 없어요. Python 3.12 이상을 설치하고 "Add python.exe to PATH" 를 체크하세요.
  pause
  exit /b 1
)

echo [1/2] Creating the virtual environment... (가상환경 만드는 중)
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
if errorlevel 1 (
  echo [!] Could not create the virtual environment. - 가상환경을 만들지 못했어요
  pause
  exit /b 1
)

echo [2/2] Installing packages... (필요한 부품 설치 중)
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [!] Installation failed. Check your internet connection. - 설치 실패, 인터넷 연결 확인
  pause
  exit /b 1
)

echo.
echo Done! Now double-click "run.bat". (설치 완료! 이제 run.bat 을 더블클릭하세요)
pause
