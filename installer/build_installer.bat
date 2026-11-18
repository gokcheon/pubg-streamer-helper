@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

title PUBG Streamer Helper - Installer Build

echo ==================================================
echo PUBG Streamer Helper - Installer Build
echo ==================================================
echo.

REM Step 0: Locate main app folder (parent folder, since this kit is in installer/ subfolder)
REM fix_a17: installer kit is now embedded inside the app folder under installer/
set "APP_DIR=.."
if not exist "%APP_DIR%\main.py" (
    echo [FAIL] Main app folder not found: %APP_DIR%
    echo.
    echo This installer kit must remain inside the app folder under installer/
    echo Expected layout:
    echo.
    echo   pubg_streamer_helper_1.0_fix_a17\
    echo     main.py
    echo     installer\           ^(this folder^)
    echo       build_installer.bat
    echo       installer.iss
    echo.
    pause
    exit /b 1
)
echo [OK] Main app folder: %APP_DIR%
echo.

REM Step 1: Run PyInstaller via main app's build_exe.bat
echo [1/3] Running PyInstaller build for main app
echo --------------------------------------------------
pushd "%APP_DIR%"
call build_exe.bat
if errorlevel 1 (
    echo [FAIL] PyInstaller build failed
    popd
    pause
    exit /b 1
)
popd
echo.

REM Step 2: Verify dist output
echo [2/3] Verifying PyInstaller dist output
if not exist "%APP_DIR%\dist\pubg_streamer_helper\pubg_streamer_helper.exe" (
    echo [FAIL] dist\pubg_streamer_helper\pubg_streamer_helper.exe is missing
    pause
    exit /b 1
)
if not exist "%APP_DIR%\dist\pubg_streamer_helper\_internal" (
    echo [FAIL] dist\pubg_streamer_helper\_internal folder is missing
    pause
    exit /b 1
)
echo [OK] PyInstaller dist verified
echo.

REM Step 3: Locate Inno Setup compiler (ISCC)
echo [3/3] Locating Inno Setup compiler ^(ISCC^)
set "ISCC="
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files (x86)\Inno Setup 5\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 5\ISCC.exe"

if not defined ISCC (
    where ISCC.exe >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%I in ('where ISCC.exe') do set "ISCC=%%I"
    )
)

if not defined ISCC (
    echo [FAIL] Inno Setup 6 ^(ISCC.exe^) not found.
    echo.
    echo Install Inno Setup from:
    echo   https://jrsoftware.org/isdl.php
    echo.
    pause
    exit /b 1
)
echo [OK] ISCC: %ISCC%
echo.

if not exist "..\dist_installer" mkdir "..\dist_installer"

echo --------------------------------------------------
echo Compiling installer with Inno Setup
echo --------------------------------------------------
rem fix_a26: 버전은 version.py 단일 진실에서 읽어 주입
set "APPVER="
for /f %%v in ('py ..\version.py') do set "APPVER=%%v"
if not defined APPVER set "APPVER=1.0"
echo [INFO] AppVersion: %APPVER%
"%ISCC%" /DAppVer=%APPVER% "installer.iss"
if errorlevel 1 (
    echo.
    echo [FAIL] Inno Setup compile failed
    pause
    exit /b 1
)
echo.

if exist "..\dist_installer\pubg_streamer_helper_setup.exe" (
    echo ==================================================
    echo Installer build completed
    echo ==================================================
    for %%F in ("..\dist_installer\pubg_streamer_helper_setup.exe") do (
        set "EXESIZE=%%~zF"
        set /a EXEMB=!EXESIZE! / 1048576
        echo Output: %%~fF
        echo Size  : about !EXEMB! MB
    )
    echo.
    echo Send this file to your friend:
    echo   ..\dist_installer\pubg_streamer_helper_setup.exe
    echo.
) else (
    echo [FAIL] Setup file was not generated. Check the log.
    pause
    exit /b 1
)

pause
exit /b 0
