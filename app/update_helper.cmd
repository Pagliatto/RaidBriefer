@echo off
rem RaidBriefer update helper (started by app/updater.py, runs while the program is closed)
rem args: 1=program PID  2=new files folder  3=install folder  4=backup folder
rem Only program files are replaced. user_data and cache are never touched.
setlocal
set "PID=%~1"
set "NEW=%~2"
set "DEST=%~3"
set "BAK=%~4"

rem 1) wait until the program has closed (max ~30s)
set /a N=0
:wait
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 goto closed
set /a N+=1
if %N% GEQ 30 goto start_old
timeout /t 1 /nobreak >nul
goto wait

:closed
rem 2) move current program files to backup
mkdir "%BAK%" 2>nul
if exist "%DEST%\_internal" move "%DEST%\_internal" "%BAK%\_internal" >nul || goto start_old
if exist "%DEST%\RaidBriefer.exe" move "%DEST%\RaidBriefer.exe" "%BAK%\RaidBriefer.exe" >nul || goto restore

rem 3) copy new program files (never into user_data / cache)
robocopy "%NEW%" "%DEST%" /E /XD user_data cache _update /NFL /NDL /NJH /NJS /NP /R:2 /W:1 >nul
if errorlevel 8 goto restore
if not exist "%DEST%\RaidBriefer.exe" goto restore
if not exist "%DEST%\_internal" goto restore
goto done

:restore
if exist "%DEST%\_internal" rmdir /s /q "%DEST%\_internal"
if exist "%BAK%\_internal" move "%BAK%\_internal" "%DEST%\_internal" >nul
if exist "%BAK%\RaidBriefer.exe" move /y "%BAK%\RaidBriefer.exe" "%DEST%\RaidBriefer.exe" >nul
mkdir "%DEST%\user_data" 2>nul
echo failed> "%DEST%\user_data\update_failed.txt"
goto start_old

:done
rem the new program deletes the _update work folder when it starts
cd /d "%DEST%"
start "" /D "%DEST%" "%DEST%\RaidBriefer.exe"
exit /b 0

:start_old
cd /d "%DEST%"
start "" /D "%DEST%" "%DEST%\RaidBriefer.exe"
exit /b 1
