@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem fix_a27: 어떤 이유로든 창이 바로 닫히지 않게 — 본문은 :main 서브루틴, 끝나면 항상 pause
if "%~1"=="" ( cmd /c ""%~f0" run" & echo. & echo  [창이 닫히지 않게 붙잡는 중 - 위 내용을 확인한 뒤 아무 키나 누르세요] & pause >nul & exit /b )
title PUBG 스트리머 헬퍼 - 새 버전 올리기 (완전 자동 배포)
echo.
echo  [PUBG 스트리머 헬퍼] 새 버전을 GitHub에 올려요.
echo  올리고 나면 깃허브 서버가 알아서 빌드하고 릴리스까지 발행해요 (5~10분).
echo  곡천님 컴에서 빌드할 필요 없음 - 이 창 하나면 끝!
echo.

rem ── 준비물 확인: git
git --version >nul 2>nul
if errorlevel 1 goto :nogit

rem ── 준비물 확인: python (py 런처 우선, 없으면 python)
set "PY=py"
py -c "pass" >nul 2>nul
if errorlevel 1 set "PY=python"
%PY% -c "pass" >nul 2>nul
if errorlevel 1 goto :nopy

rem ── 저장소 주소 (업데이트주소.txt — 자동 업데이트와 같은 파일을 같은 방식으로 읽음)
set "REPO="
for /f %%a in ('%PY% updater.py') do set "REPO=%%a"
if not defined REPO goto :norepo

rem ── 버전 태그 (version.py 단일 진실)
set "TAG="
for /f %%v in ('%PY% version.py label') do set "TAG=%%v"
if not defined TAG goto :nover

rem ── 회귀 테스트 캡처 52장 준비 (zip 용량 제한으로 빠져 있으면 이전 버전 폴더에서 자동 복사)
if exist "tests\regression\captures\1.png" goto :caps_ok
set "CAPSRC="
if exist "..\pubg_streamer_helper_1.0_fix_a28\tests\regression\captures\1.png" set "CAPSRC=..\pubg_streamer_helper_1.0_fix_a28\tests\regression\captures"
if not defined CAPSRC if exist "..\pubg_streamer_helper_1.0_fix_a27\tests\regression\captures\1.png" set "CAPSRC=..\pubg_streamer_helper_1.0_fix_a27\tests\regression\captures"
if not defined CAPSRC goto :caps_missing
echo  회귀 캡처 52장을 이전 버전 폴더에서 복사해요... %CAPSRC%
xcopy /E /I /Y /Q "%CAPSRC%" "tests\regression\captures" >nul
goto :caps_ok
:caps_missing
echo  [주의] tests\regression\captures 가 비어 있어요. 회귀 캡처 없이 올라가요 - 앱 동작엔 영향 없음.
:caps_ok

rem ── 안전장치: 개인 정보 파일이 올라가지 않는지 확인 (.gitignore 가 막지만 이중 확인)
if exist "chat_tokens.json" echo  [안내] chat_tokens.json 은 .gitignore 로 제외돼요 (업로드 안 됨).
if exist "config.json" echo  [안내] config.json 은 .gitignore 로 제외돼요 (config.default.json 만 올라감).

echo  저장소 : https://github.com/%REPO%
echo  새 버전: %TAG%
echo.

rem ── 이 폴더를 새 스냅샷으로 올림 (버전마다 폴더가 새로 오므로 매번 새로 초기화)
if exist ".git" rmdir /s /q ".git"
git init -b main >nul 2>nul
git -C . config user.name "pubg-streamer-helper" >nul
git -C . config user.email "pubg-streamer-helper@local" >nul
git add -A >nul
rem ── fix_a28: 업로드 직전 안전 검사 — 개인 정보 파일이 스테이징에 들어 있으면 중단
git diff --cached --name-only > "%TEMP%\psh_upload_list.txt"
findstr /I /R "config\.json config\.backup\.json chat_tokens history\.json \.json\.tmp" "%TEMP%\psh_upload_list.txt" >nul
if not errorlevel 1 goto :secretfound
for /f %%c in ('find /c /v "" ^< "%TEMP%\psh_upload_list.txt"') do set "NFILES=%%c"
echo  올라갈 파일 수: %NFILES%개 - config.json / 토큰 / 기록 파일 없음 확인
git commit -m "%TAG%" >nul
git remote add origin "https://github.com/%REPO%.git"
git tag %TAG% >nul 2>nul

echo  GitHub에 올리는 중... (처음 한 번은 브라우저 로그인 창이 떠요 - 로그인만 해주세요)
echo.
git push --force origin main
if errorlevel 1 goto :pusherr
git push origin %TAG%
if errorlevel 1 goto :tagerr

echo.
echo  ════════════════════════════════════════════════
echo   업로드 완료! 깃허브가 지금부터 알아서 빌드해요 (5~10분)
echo   진행 보기: https://github.com/%REPO%/actions
echo   끝나면 설치판 앱에 자동으로 "새 버전" 알림이 떠요!
echo  ════════════════════════════════════════════════
echo.
pause
exit /b 0

:nogit
echo  Git이 설치되어 있지 않아요. 지금 다운로드 페이지를 열게요.
echo  설치 후(기본 옵션 그대로 다음다음) 이 파일을 다시 실행해 주세요.
start https://git-scm.com/download/win
pause
exit /b 1

:nopy
echo  파이썬이 없어요. Python 3.11 설치 후 다시 실행해 주세요.
pause
exit /b 1

:norepo
echo  업데이트주소.txt에 저장소 주소가 없어요.
echo  메모장으로 열어 맨 아래 줄에 "아이디/저장소이름"을 적어 주세요. (예: gokcheon/pubg-streamer-helper)
echo  처음이라면 자동업데이트_안내.txt 를 참고!
pause
exit /b 1

:nover
echo  버전을 읽지 못했어요. 이 파일이 앱 폴더(main.py 옆) 안에 있는지 확인해 주세요.
pause
exit /b 1

:secretfound
echo.
echo  [중단] 개인 정보 파일이 업로드 목록에 들어 있어요:
findstr /I /R "config\.json config\.backup\.json chat_tokens history\.json \.json\.tmp" "%TEMP%\psh_upload_list.txt"
echo  .gitignore 가 깨졌거나 파일 이름이 바뀐 경우예요. 이 화면을 캡처해서 Claude 에게 보여주세요.
if exist ".git" rmdir /s /q ".git"
pause
exit /b 1

:pusherr
echo.
echo  업로드에 실패했어요. 흔한 원인:
echo   - 저장소가 아직 없음 → github.com 에서 %REPO% 저장소를 먼저 만들어 주세요 (Public)
echo   - 로그인 창을 닫았음 → 다시 실행해서 로그인해 주세요
echo   - 인터넷 연결 확인
pause
exit /b 1

:tagerr
echo.
echo  태그 %TAG% 업로드에 실패했어요. 같은 버전을 두 번 올리려 한 건 아닌지 확인해 주세요.
echo  (같은 fix 번호는 다시 못 올려요 - 다음 번호 zip으로 올려 주세요)
pause
exit /b 1
