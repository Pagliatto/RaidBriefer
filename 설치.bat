@echo off
rem RaidBriefer 설치: 처음 한 번만 더블클릭하세요.
rem 1) 파이썬 확인  2) 가상환경(.venv) 만들기  3) 필요한 부품 설치
chcp 65001 > nul
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo [!] 파이썬이 없어요. https://www.python.org/downloads/ 에서 Python 3.12 이상을 설치해 주세요.
  echo     설치할 때 "Add python.exe to PATH" 를 꼭 체크하세요.
  pause
  exit /b 1
)

echo [1/2] 가상환경 만드는 중...
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
if errorlevel 1 (
  echo [!] 가상환경을 만들지 못했어요.
  pause
  exit /b 1
)

echo [2/2] 필요한 부품 설치 중...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [!] 설치에 실패했어요. 인터넷 연결을 확인해 주세요.
  pause
  exit /b 1
)

echo.
echo 설치 완료! 이제 "프로그램_실행.bat" 을 더블클릭하세요.
pause
