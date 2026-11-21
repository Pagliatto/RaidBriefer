@echo off
rem 업데이트 도우미: 원본 백업은 새 버전 기동 또는 검증된 복구까지 보존한다.
setlocal
set "PID=%~1"
set "NEW=%~2"
set "DEST=%~3"
set "BAK=%~4"
set "WORK=%~dp0"
set "INTERNAL_MOVED="
set "EXE_MOVED="
set "REASON=wait-timeout"
set /a N=0
:wait
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if errorlevel 1 goto closed
set /a N+=1
if %N% GEQ 30 goto fail_old
ping -n 2 127.0.0.1 >nul
goto wait

:closed
set "REASON=backup-create-failed"
if exist "%BAK%" goto rollback_failed
mkdir "%BAK%" 2>nul
if errorlevel 1 goto rollback_failed
set "REASON=backup-internal-failed"
if not exist "%DEST%\_internal" goto fail_old
move "%DEST%\_internal" "%BAK%\_internal" >nul
if errorlevel 1 goto rollback_failed
set "INTERNAL_MOVED=1"
set "REASON=backup-exe-failed"
if not exist "%DEST%\RaidBriefer.exe" goto restore
move "%DEST%\RaidBriefer.exe" "%BAK%\RaidBriefer.exe" >nul
if errorlevel 1 goto restore
set "EXE_MOVED=1"

set "REASON=install-copy-failed"
robocopy "%NEW%" "%DEST%" /E /XD user_data cache _update /NFL /NDL /NJH /NJS /NP /R:2 /W:1 >nul
if errorlevel 8 goto restore
if not exist "%DEST%\RaidBriefer.exe" goto restore
if not exist "%DEST%\_internal" goto restore
> "%WORK%installed.ok" echo installed
if errorlevel 1 goto restore
goto done

:restore
rem 복구는 복사로 진행하여 중간 실패에도 백업 원본이 남게 한다.
if not defined INTERNAL_MOVED goto restore_exe
set "REASON=rollback-internal-missing"
if not exist "%BAK%\_internal" goto rollback_failed
set "REASON=rollback-internal-remove-failed"
if exist "%DEST%\_internal" rmdir /s /q "%DEST%\_internal"
if exist "%DEST%\_internal" goto rollback_failed
set "REASON=rollback-internal-copy-failed"
robocopy "%BAK%\_internal" "%DEST%\_internal" /E /NFL /NDL /NJH /NJS /NP /R:2 /W:1 >nul
if errorlevel 8 goto rollback_failed
if not exist "%DEST%\_internal" goto rollback_failed
:restore_exe
if not defined EXE_MOVED goto verify_restore
set "REASON=rollback-exe-missing"
if not exist "%BAK%\RaidBriefer.exe" goto rollback_failed
set "REASON=rollback-exe-copy-failed"
copy /y "%BAK%\RaidBriefer.exe" "%DEST%\RaidBriefer.exe" >nul
if errorlevel 1 goto rollback_failed
fc /b "%BAK%\RaidBriefer.exe" "%DEST%\RaidBriefer.exe" >nul
if errorlevel 1 goto rollback_failed
:verify_restore
set "REASON=rollback-incomplete"
if not exist "%DEST%\RaidBriefer.exe" goto rollback_failed
if not exist "%DEST%\_internal" goto rollback_failed
> "%WORK%rollback.ok" echo verified
if errorlevel 1 goto rollback_failed
set "REASON=install-failed-rollback-verified"

:fail_old
if not exist "%DEST%\user_data" mkdir "%DEST%\user_data" 2>nul
> "%DEST%\user_data\update_failed.txt" echo %REASON%
goto start_old

:rollback_failed
rem 검증되지 않은 조합은 실행하지 않는다. 백업과 실패 이유를 남긴다.
if not exist "%DEST%\user_data" mkdir "%DEST%\user_data" 2>nul
> "%DEST%\user_data\update_failed.txt" echo %REASON%
exit /b 2

:done
cd /d "%DEST%"
start "" /D "%DEST%" "%DEST%\RaidBriefer.exe"
exit /b 0

:start_old
cd /d "%DEST%"
start "" /D "%DEST%" "%DEST%\RaidBriefer.exe"
exit /b 1
