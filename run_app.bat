@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
cd /d "%~dp0"

title PUBG Streamer Helper - Run

if not exist logs mkdir logs
if not exist debug mkdir debug
if not exist screenshot mkdir screenshot

echo ==================================================
echo PUBG Streamer Helper 1.0 fix_a30 - Run
echo ==================================================
echo [1/4] Project dir
echo %CD%
echo.

echo [2/4] Check Python launcher
where py
if errorlevel 1 (
    echo.
    echo [FAIL] Python launcher ^(py^) was not found.
    echo Install Python and enable the launcher, then try again.
    pause
    exit /b 1
)

py -V
if errorlevel 1 (
    echo.
    echo [FAIL] Python launcher exists but did not run correctly.
    pause
    exit /b 1
)

echo.
echo [3/4] Start app
echo The app writes logs to:
echo   logs\events.log
echo   logs\errors.log
echo.
py main.py
set "EXITCODE=%ERRORLEVEL%"

echo.
echo [4/4] App finished with exit code: %EXITCODE%
if not "%EXITCODE%"=="0" (
    echo [FAIL] The app ended with an error.
    echo Check these files:
    echo   logs\events.log
    echo   logs\errors.log
    if exist logs\errors.log (
        echo.
        echo ----- last lines from logs\errors.log -----
        powershell -NoProfile -Command "Get-Content -Path 'logs\errors.log' -Tail 40" 2>nul
        if errorlevel 1 type logs\errors.log
        echo -------------------------------------------
    )
)

echo.
pause
exit /b %EXITCODE%
