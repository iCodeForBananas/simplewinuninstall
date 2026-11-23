@echo off
rem Double-click wrapper around build.ps1.
rem
rem Exists so the build can be launched without typing a command: it runs
rem build.ps1 with the execution policy bypassed, captures everything
rem (stdout and stderr) to build-log.txt next to this file, and leaves the
rem window open so the result is visible.

setlocal
cd /d "%~dp0"
set "LOG=%~dp0build-log.txt"

echo ======================================================================
echo  SimpleWinUninstall - build and verify
echo ======================================================================
echo.
echo  Full output is being written to:
echo    %LOG%
echo.
echo  This takes a couple of minutes. PyInstaller is quiet while it works,
echo  and near the end a window will open by itself for a few seconds -
echo  that is the launch test confirming the GUI renders. Leave it alone.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" > "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo. >> "%LOG%"
echo BUILD_SCRIPT_EXIT_CODE=%RC% >> "%LOG%"

type "%LOG%"

echo.
echo ======================================================================
if "%RC%"=="0" (
    echo  SUCCESS - the executable is at:
    echo    %~dp0dist\SimpleWinUninstall.exe
) else (
    echo  FAILED - exit code %RC%. The reason is in the BLOCKED/FIX lines above.
)
echo  Log file: %LOG%
echo ======================================================================
echo.
pause
