@echo off
rem 업데이트 도우미: 원본 백업은 새 버전 기동 또는 검증된 복구까지 보존한다.
setlocal
set "PID=%~1"
set "NEW=%~2"
set "DEST=%~3"
set "BAK=%~4"
set "WORK=%~dp0"
rem PATH 에 다른 find 등이 먼저 있어도 Windows 것을 쓰도록 전체 경로로 부른다.
set "SYS=%SystemRoot%\System32"
set "INTERNAL_MOVED="
set "EXE_MOVED="
set "REASON=wait-timeout"
set /a N=0
:wait
"%SYS%\tasklist.exe" /FI "PID eq %PID%" 2>nul | "%SYS%\find.exe" "%PID%" >nul
if errorlevel 1 goto closed
set /a N+=1
if %N% GEQ 30 goto fail_old
"%SYS%\ping.exe" -n 2 127.0.0.1 >nul
goto wait

:closed
set "REASON=backup-create-failed"
if exist "%BAK%" goto rollback_failed
mkdir "%BAK%" 2>nul
if errorlevel 1 goto fail_old
set "REASON=backup-internal-failed"
if not exist "%DEST%\_internal" goto fail_old
move "%DEST%\_internal" "%BAK%\_internal" >nul
if errorlevel 1 goto internal_not_moved
set "INTERNAL_MOVED=1"
set "REASON=backup-exe-failed"
if not exist "%DEST%\RaidBriefer.exe" goto restore
move "%DEST%\RaidBriefer.exe" "%BAK%\RaidBriefer.exe" >nul
if errorlevel 1 goto restore
set "EXE_MOVED=1"

set "REASON=install-copy-failed"
"%SYS%\robocopy.exe" "%NEW%" "%DEST%" /E /XD user_data cache _update /NFL /NDL /NJH /NJS /NP /R:2 /W:1 >nul
if errorlevel 8 goto restore
if not exist "%DEST%\RaidBriefer.exe" goto restore
if not exist "%DEST%\_internal" goto restore
rem echo 는 errorlevel 을 바꾸지 않아 robocopy 의 성공 값(1)이 남아 있음 → 표식 파일이 실제로 생겼는지로 판단
> "%WORK%installed.ok" echo installed
if not exist "%WORK%installed.ok" goto restore
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
"%SYS%\robocopy.exe" "%BAK%\_internal" "%DEST%\_internal" /E /NFL /NDL /NJH /NJS /NP /R:2 /W:1 >nul
if errorlevel 8 goto rollback_failed
if not exist "%DEST%\_internal" goto rollback_failed
:restore_exe
if not defined EXE_MOVED goto verify_restore
set "REASON=rollback-exe-missing"
if not exist "%BAK%\RaidBriefer.exe" goto rollback_failed
set "REASON=rollback-exe-copy-failed"
copy /y "%BAK%\RaidBriefer.exe" "%DEST%\RaidBriefer.exe" >nul
if errorlevel 1 goto rollback_failed
"%SYS%\fc.exe" /b "%BAK%\RaidBriefer.exe" "%DEST%\RaidBriefer.exe" >nul
if errorlevel 1 goto rollback_failed
:verify_restore
set "REASON=rollback-incomplete"
if not exist "%DEST%\RaidBriefer.exe" goto rollback_failed
if not exist "%DEST%\_internal" goto rollback_failed
> "%WORK%rollback.ok" echo verified
if not exist "%WORK%rollback.ok" goto rollback_failed
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

:internal_not_moved
rem 옮기기 실패: 원본이 제자리에 그대로면 바뀐 것이 없으므로 예전 프로그램을 다시 켠다.
if exist "%BAK%\_internal" goto rollback_failed
if not exist "%DEST%\_internal" goto rollback_failed
goto fail_old

:done
cd /d "%DEST%"
start "" /D "%DEST%" "%DEST%\RaidBriefer.exe"
exit /b 0

:start_old
cd /d "%DEST%"
start "" /D "%DEST%" "%DEST%\RaidBriefer.exe"
exit /b 1
