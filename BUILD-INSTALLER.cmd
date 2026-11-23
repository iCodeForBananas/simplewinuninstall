@echo off
rem Double-click to build the installer: dist\SimpleWinUninstallSetup.exe
rem
rem Same as BUILD-ME.cmd but with the -Installer step on the end. Needs Inno
rem Setup; if it is missing the log tells you the one winget command to run.

setlocal
cd /d "%~dp0"
set "LOG=%~dp0build-log.txt"

echo ======================================================================
echo  SimpleWinUninstall - build app + installer
echo ======================================================================
echo.
echo  Full output is being written to:
echo    %LOG%
echo.
echo  Takes a few minutes. A window will open by itself near the end for a
echo  few seconds - that is the launch test. Leave it alone.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" -Installer > "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo. >> "%LOG%"
echo BUILD_SCRIPT_EXIT_CODE=%RC% >> "%LOG%"

type "%LOG%"

echo.
echo ======================================================================
if "%RC%"=="0" (
    echo  SUCCESS - run this to install it:
    echo    %~dp0dist\SimpleWinUninstallSetup.exe
) else (
    echo  FAILED - exit code %RC%. The reason is in the BLOCKED/FIX lines above.
)
echo  Log file: %LOG%
echo ======================================================================
echo.
pause
