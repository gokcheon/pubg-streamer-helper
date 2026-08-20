@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF12=1"
cd /d "%~dp0"

title PUBG Streamer Helper - Build

if not exist build_logs mkdir build_logs
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%I"
if not defined TS set "TS=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "LOGFILE=build_logs\build_%TS%.log"

echo ================================================== 
echo PUBG Streamer Helper 1.0 fix_a28 - Build
echo ==================================================
echo Build log: %LOGFILE%
> "%LOGFILE%" echo [START] Build started at %DATE% %TIME%
>> "%LOGFILE%" echo [INFO] Project dir: %CD%

echo.
echo [1/6] Check Python launcher
where py >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [FAIL] Python launcher ^(py^) was not found.
    >> "%LOGFILE%" echo [FAIL] Python launcher ^(py^) was not found.
    goto :fail
)
py -V >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [FAIL] Python launcher exists but did not run correctly.
    >> "%LOGFILE%" echo [FAIL] Python launcher exists but did not run correctly.
    goto :fail
)
py -V

echo.
echo [2/6] Install requirements
py -m pip install -r requirements.txt >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [FAIL] Failed to install requirements.
    >> "%LOGFILE%" echo [FAIL] Failed to install requirements.
    goto :fail
)
echo [OK] Requirements installed

echo.
echo [3/6] Check PyInstaller
py -m PyInstaller --version >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [INFO] PyInstaller not ready. Installing now...
    >> "%LOGFILE%" echo [INFO] PyInstaller not ready. Installing now...
    py -m pip install pyinstaller >> "%LOGFILE%" 2>&1
    if errorlevel 1 (
        echo [FAIL] Failed to install PyInstaller.
        >> "%LOGFILE%" echo [FAIL] Failed to install PyInstaller.
        goto :fail
    )
)
for /f "delims=" %%I in ('py -m PyInstaller --version 2^>nul') do set "PYI_VER=%%I"
if defined PYI_VER echo [OK] PyInstaller version: !PYI_VER!
if defined PYI_VER >> "%LOGFILE%" echo [INFO] PyInstaller version: !PYI_VER!

echo.
echo [4/6] Clean old build folders
if exist build rmdir /s /q build >> "%LOGFILE%" 2>&1
if exist dist rmdir /s /q dist >> "%LOGFILE%" 2>&1
if exist pubg_streamer_helper.spec del /f /q pubg_streamer_helper.spec >> "%LOGFILE%" 2>&1
echo [OK] Old build folders cleaned

echo.
echo [5/6] Run PyInstaller
py -m PyInstaller --noconfirm --clean --onedir --noconsole --name pubg_streamer_helper --icon app.ico --add-data "config.default.json;." --add-data "app.ico;." --add-data "digit_templates_strip.png;." --add-data "digit_templates_strip_solo.png;." --add-data "digit_templates_strip_duo.png;." --add-data "ui;ui" --add-data "메뉴얼.html;." --add-data "업데이트주소.txt;." --hidden-import pystray._win32 --hidden-import PIL.Image main.py >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [FAIL] PyInstaller build failed.
    >> "%LOGFILE%" echo [FAIL] PyInstaller build failed.
    goto :fail
)

echo.
echo [6/6] Build done
echo Output folder: dist\pubg_streamer_helper
>> "%LOGFILE%" echo [DONE] Output folder: dist\pubg_streamer_helper
echo [INFO] Dist template file check
dir /b "dist\pubg_streamer_helper" >> "%LOGFILE%" 2>&1
dir /b "dist\pubg_streamer_helper\_internal" >> "%LOGFILE%" 2>&1
echo.
echo Build completed successfully.
echo Log file: %LOGFILE%
echo Check these entries in dist if needed:
echo   dist\pubg_streamer_helper\_internal\digit_templates_strip.png
echo   dist\pubg_streamer_helper\_internal\digit_templates_strip_solo.png
echo   dist\pubg_streamer_helper\_internal\digit_templates_strip_duo.png
echo.
pause
exit /b 0

:fail
echo.
echo ===== BUILD FAILED =====
echo Log file: %LOGFILE%
echo Showing last lines from the log:
echo.
powershell -NoProfile -Command "Get-Content -Path '%LOGFILE%' -Tail 80" 2>nul
if errorlevel 1 type "%LOGFILE%"
echo.
pause
exit /b 1
