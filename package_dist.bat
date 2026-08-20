@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

title PUBG Streamer Helper - Package

echo ==================================================
echo PUBG Streamer Helper 1.0 fix_a29 - 배포 패키지 생성
echo ==================================================
echo.

REM 빌드 결과물 확인
if not exist "dist\pubg_streamer_helper\pubg_streamer_helper.exe" (
    echo [FAIL] dist\pubg_streamer_helper\pubg_streamer_helper.exe 가 없습니다.
    echo        먼저 build_exe.bat 을 실행하세요.
    echo.
    pause
    exit /b 1
)

REM _internal 확인
if not exist "dist\pubg_streamer_helper\_internal" (
    echo [FAIL] _internal 폴더가 없습니다. 빌드가 완전하지 않습니다.
    echo        build_exe.bat 을 다시 실행하세요.
    echo.
    pause
    exit /b 1
)

echo [OK] 빌드 결과물 확인됨
echo.

REM 날짜 기반 zip 파일명
for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "DT=%%I"
if not defined DT set "DT=release"
set "ZIPNAME=pubg_streamer_helper_fix_a29_%DT%.zip"

REM 기존 zip 제거
if exist "%ZIPNAME%" del /f /q "%ZIPNAME%"

REM PowerShell로 zip 생성
echo [1/2] 배포용 zip 생성 중: %ZIPNAME%
powershell -NoProfile -Command ^
    "Compress-Archive -Path 'dist\pubg_streamer_helper' -DestinationPath '%ZIPNAME%' -Force"
if errorlevel 1 (
    echo [FAIL] zip 생성 실패
    pause
    exit /b 1
)

echo [OK] zip 생성 완료: %ZIPNAME%
echo.

REM 크기 확인
for %%F in ("%ZIPNAME%") do set "ZIPSIZE=%%~zF"
set /a ZIPMB=%ZIPSIZE% / 1048576
echo [2/2] 파일 크기: 약 %ZIPMB% MB
echo.

echo ==================================================
echo 배포 체크리스트
echo ==================================================
echo  1. %ZIPNAME% 를 상대방에게 전달하세요.
echo  2. 압축 해제 후 pubg_streamer_helper 폴더 안의
echo     pubg_streamer_helper.exe 를 실행합니다.
echo  3. OneDrive / 바탕화면 클라우드 폴더 실행 금지.
echo     권장 경로: C:\pubg_helper 또는 D:\pubg_helper
echo  4. _internal 폴더가 exe 옆에 있어야 합니다.
echo     (폴더째 압축 해제하면 자동으로 함께 있습니다)
echo ==================================================
echo.
echo 완료! 파일 위치: %CD%\%ZIPNAME%
echo.
pause
exit /b 0
