; PUBG Streamer Helper - Inno Setup 설치 스크립트
; ====================================================
; 이 스크립트는 build_installer.bat 에서 자동으로 컴파일됩니다.
; Inno Setup 6.x 이상에서 작동.
;
; 사용 흐름:
;   1) PyInstaller 빌드 결과 (dist\pubg_streamer_helper\) 가 준비됨
;   2) ISCC.exe installer.iss 로 컴파일
;   3) dist_installer\pubg_streamer_helper_setup.exe 가 생성됨
;
; WebView2 처리:
;   - 설치 시 시스템에 WebView2 Runtime 이 없으면 부트스트래퍼를 다운받아 자동 설치.
;   - 인터넷 연결이 필요. 오프라인 PC 라면 사전에 WebView2 를 따로 설치해두면 됨.

#define MyAppName "PUBG Streamer Helper"
; fix_a26: 버전은 build.yml / build_installer.bat 이 version.py 에서 읽어 /DAppVer= 로 넣어준다 (직접 수정 금지)
#ifndef AppVer
  #define AppVer "1.0.30"
#endif
#define MyAppVersion AppVer
#define MyAppPublisher "Gokcheon"
#define MyAppExeName "pubg_streamer_helper.exe"
#define MyAppId "{{B5E2A0A1-7D14-4B86-9F7F-3C4A0B5DDA9C}"

; PyInstaller 빌드 결과물 위치 (build_exe.bat 실행 후 생성되는 폴더)
; fix_a17: installer/ 서브폴더로 이동했으므로 한 단계 위(앱 루트)의 dist 참조
#define SourceDir "..\dist\pubg_streamer_helper"

; 출력 경로 - 앱 폴더 루트의 dist_installer/ 로 (찾기 쉽게)
#define OutputDir "..\dist_installer"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=no
OutputDir={#OutputDir}
OutputBaseFilename=pubg_streamer_helper_setup
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
; 64비트 Windows 에서 64비트 모드로 설치 (Program Files vs Program Files (x86))
ArchitecturesInstallIn64BitMode=x64compatible
; 관리자 권한 요구 (Program Files 경로에 쓰기 위해)
PrivilegesRequired=admin
; 한글 메시지 사용
ShowLanguageDialog=auto
; 이전 버전 자동 제거 (같은 AppId)
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; 바탕화면 아이콘은 자동 생성 (체크박스 안 보임, 무조건 생성)
; → 사용자 결정에 따라 desktopicon Task 를 기본 체크 상태로 둠
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; 메인 실행 파일과 PyInstaller 가 만든 _internal 폴더 전체
Source: "{#SourceDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

; WebView2 부트스트래퍼는 설치 시 다운로드 (아래 [Code] 섹션 참조)
; 이 zip 에 포함된 작은 부트스트래퍼 파일 사용도 가능하지만 사용자 선택대로 다운로드 방식 사용

[Icons]
; fix_a29: 관리자 권한 플래그 제거. 앱 데이터가 %LOCALAPPDATA%\PUBG Streamer Helper 로 옮겨져 Program Files 에 쓰지 않는다.
; (a11~a28 은 바로가기에 '관리자 권한으로 실행' 을 걸었음 — 매 실행 UAC, 관리자 프로세스의 브라우저 열기 불안정, 토큰이 Program Files 에 잔존)
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 설치 완료 화면에서 "지금 실행하기" 옵션 제공
; 설치 마법사가 관리자 권한으로 실행 중이므로 runascurrentuser 플래그로 권한 유지
; fix_a29: 설치마법사는 관리자지만 앱은 일반 사용자 권한으로 실행 (runasoriginaluser)
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent unchecked runasoriginaluser

[UninstallDelete]
; a28 이하가 exe 옆에 남긴 개인 파일(토큰·설정·기록)은 언인스톨 때 정리 (fix_a29 부터는 LOCALAPPDATA 로 이동됨)
Type: files; Name: "{app}\.running.lock"
Type: files; Name: "{app}\chat_tokens.json"
Type: files; Name: "{app}\config.json"
Type: files; Name: "{app}\config.backup.json"
Type: files; Name: "{app}\history.json"
Type: files; Name: "{app}\overlay.txt"
Type: files; Name: "{app}\chat_outbox.txt"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\screenshot"
Type: filesandordirs; Name: "{app}\debug"
Type: filesandordirs; Name: "{app}\build_logs"

[Code]
{ ============================================================
  WebView2 Runtime 자동 감지 + 설치 로직
  - 레지스트리에서 WebView2 Runtime 의 pv (ProductVersion) 키를 확인
  - 없으면 Microsoft 공식 부트스트래퍼를 다운로드해 사일런트 설치
  ============================================================ }

const
  WV2_BOOTSTRAPPER_URL = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';
  WV2_REG_KEY_64 = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WV2_REG_KEY_32 = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WV2_REG_KEY_USER = 'Software\Microsoft\EdgeUpdate\ClientState\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function IsWebView2Installed(): Boolean;
var
  Version: string;
begin
  Result := False;
  { HKLM (시스템 전역 설치) - 64비트 키 }
  if RegQueryStringValue(HKEY_LOCAL_MACHINE, WV2_REG_KEY_64, 'pv', Version) then
  begin
    if (Version <> '') and (Version <> '0.0.0.0') then
    begin
      Result := True;
      Exit;
    end;
  end;
  { HKLM 32비트 키 (32비트 시스템 또는 일부 환경) }
  if RegQueryStringValue(HKEY_LOCAL_MACHINE, WV2_REG_KEY_32, 'pv', Version) then
  begin
    if (Version <> '') and (Version <> '0.0.0.0') then
    begin
      Result := True;
      Exit;
    end;
  end;
  { HKCU (사용자별 설치) }
  if RegQueryStringValue(HKEY_CURRENT_USER, WV2_REG_KEY_USER, 'pv', Version) then
  begin
    if (Version <> '') and (Version <> '0.0.0.0') then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

{ ============================================================
  바로가기(.lnk) 파일에 "관리자 권한으로 실행" 플래그 설정
  - .lnk 바이너리 포맷의 21번째 바이트 (offset 0x15) 의 특정 비트(0x20) 를 켬
  - Inno Setup 자체가 이 기능을 안 가지고 있어서 직접 바이너리 편집
  - 이 함수는 바로가기 생성 직후 AfterInstall 콜백으로 호출됨
  ============================================================ }
procedure SetElevationBit(Filename: string);
var
  Buffer: string;
  Stream: TFileStream;
begin
  Filename := ExpandConstant(Filename);
  Log('[ElevationBit] Setting for: ' + Filename);
  if not FileExists(Filename) then
  begin
    Log('[ElevationBit] File not found: ' + Filename);
    Exit;
  end;
  try
    Stream := TFileStream.Create(Filename, fmOpenReadWrite);
    try
      Stream.Seek(21, soFromBeginning);
      SetLength(Buffer, 1);
      Stream.ReadBuffer(Buffer, 1);
      Buffer[1] := Chr(Ord(Buffer[1]) or $20);
      Stream.Seek(-1, soFromCurrent);
      Stream.WriteBuffer(Buffer, 1);
      Log('[ElevationBit] Successfully applied to: ' + Filename);
    finally
      Stream.Free;
    end;
  except
    Log('[ElevationBit] Failed: ' + GetExceptionMessage);
  end;
end;

function InstallWebView2(): Boolean;
var
  TempPath, BootstrapperPath: string;
  ResultCode: Integer;
begin
  Result := False;
  TempPath := ExpandConstant('{tmp}');
  BootstrapperPath := TempPath + '\MicrosoftEdgeWebview2Setup.exe';

  { 부트스트래퍼 다운로드 - Inno Setup 6 내장 DownloadTemporaryFile 사용 }
  WizardForm.StatusLabel.Caption := 'WebView2 런타임 다운로드 중...';
  try
    DownloadTemporaryFile(WV2_BOOTSTRAPPER_URL, 'MicrosoftEdgeWebview2Setup.exe', '', nil);
  except
    Log('[WebView2] Bootstrapper download failed: ' + GetExceptionMessage);
    MsgBox(
      'WebView2 런타임 다운로드에 실패했습니다.' + #13#10 + #13#10 +
      '인터넷 연결을 확인하고 다시 시도하거나,' + #13#10 +
      '아래 링크에서 직접 설치해주세요:' + #13#10 +
      'https://developer.microsoft.com/microsoft-edge/webview2/',
      mbError, MB_OK
    );
    Exit;
  end;

  if not FileExists(BootstrapperPath) then
  begin
    Log('[WebView2] Bootstrapper file not found after download.');
    MsgBox('WebView2 런타임 다운로드 파일을 찾을 수 없습니다.', mbError, MB_OK);
    Exit;
  end;

  { 사일런트 설치 실행 }
  WizardForm.StatusLabel.Caption := 'WebView2 런타임 설치 중...';
  if Exec(BootstrapperPath, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if ResultCode = 0 then
    begin
      Result := True;
      Log('[WebView2] Bootstrapper installation succeeded.');
    end
    else
    begin
      Log(Format('[WebView2] Bootstrapper exited with code %d', [ResultCode]));
      MsgBox(
        Format('WebView2 런타임 설치가 코드 %d 로 종료되었습니다.', [ResultCode]) + #13#10 +
        '앱이 정상 작동하지 않을 수 있습니다.',
        mbError, MB_OK
      );
    end;
  end
  else
  begin
    Log('[WebView2] Failed to launch bootstrapper.');
    MsgBox('WebView2 런타임 설치 프로그램을 실행할 수 없습니다.', mbError, MB_OK);
  end;

  { 부트스트래퍼 파일 정리 }
  if FileExists(BootstrapperPath) then
    DeleteFile(BootstrapperPath);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { 파일 복사 직전 단계에서 WebView2 체크/설치 }
  if CurStep = ssInstall then
  begin
    if IsWebView2Installed() then
    begin
      Log('[WebView2] Already installed, skipping bootstrapper.');
    end
    else
    begin
      Log('[WebView2] Not installed. Installing...');
      InstallWebView2();
      { 설치 후 다시 확인 - 실패해도 설치는 계속 진행 (사용자가 따로 깔 수 있게 폴더는 남김) }
      if IsWebView2Installed() then
        Log('[WebView2] Installation verified.')
      else
        Log('[WebView2] WARNING: WebView2 still not detected. App may fail to launch.');
    end;
  end;
end;

{ fix_a29: 언인스톨 시 사용자 데이터(%LOCALAPPDATA%\PUBG Streamer Helper) 삭제 여부를 묻는다 }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\PUBG Streamer Helper');
    if DirExists(DataDir) then
    begin
      if MsgBox('설정·치지직 연결 정보·기록이 들어 있는 데이터 폴더도 삭제할까요?' + #13#10 + DataDir + #13#10#13#10 +
                '"아니요" 를 누르면 남겨두고, 다시 설치하면 그대로 이어서 써요.', mbConfirmation, MB_YESNO) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;

function InitializeSetup(): Boolean;
begin
  { 설치 시작 전 사전 검증 (현재는 OS 버전만 체크) }
  Result := True;
  if not IsWin64 and (GetWindowsVersion < $06010000) then
  begin
    { Windows 7 미만 차단 }
    MsgBox('이 프로그램은 Windows 10 이상에서만 동작합니다.', mbError, MB_OK);
    Result := False;
  end;
end;
