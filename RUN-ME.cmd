@echo off
rem Double-click to start SimpleWinUninstall. Uses the built exe if there is
rem one, otherwise runs from source with the installed Python.

setlocal
cd /d "%~dp0"

if exist "%~dp0dist\SimpleWinUninstall.exe" (
    start "" "%~dp0dist\SimpleWinUninstall.exe"
    exit /b 0
)

set "PYW="
for %%P in (pythonw.exe) do if not defined PYW set "PYW=%%~$PATH:P"
if defined PYW (
    start "" "%PYW%" "%~dp0run.py"
    exit /b 0
)

echo Could not find Python on this PC. Install it with:
echo     winget install --id Python.Python.3.12 --source winget
pause
