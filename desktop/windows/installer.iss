; installer.iss — Inno Setup 6 script for SlyLED Parent
; Build: iscc installer.iss  (from desktop\windows\)
; Or:    run build.bat — it calls iscc automatically if available.

#define AppName      "SlyLED Orchestrator"
#define AppVersion   "1.4.3"
#define AppPublisher "Electric RV Corporation"
#define AppExeName   "SlyLED.exe"
; Unique GUID for this app — keep fixed across releases so updates overwrite
#define AppId        "{{6F3A1D2E-84C7-4B9F-A051-3D28E9F07C14}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppUpdatesURL=https://github.com/SlyWombat/SlyLED

; Install without elevation when possible (no UAC for user-space install)
DefaultDirName={autopf}\SlyLED
DefaultGroupName=SlyLED
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline

OutputDir=dist
OutputBaseFilename=SlyLED-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\..\images\slyled.ico
UninstallDisplayIcon={app}\SlyLED.exe

DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked
Name: "startuprun";  Description: "Start SlyLED when &Windows starts (runs minimised to tray)"; GroupDescription: "Startup:"

[Files]
; Main executable — compiled by PyInstaller
Source: "dist\SlyLED.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start menu
Name: "{group}\{#AppName}";          Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

; Desktop shortcut (optional)
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

; Windows startup (optional) — pass --no-browser so it doesn't open a tab every boot
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: "--no-browser"; Tasks: startuprun

[Run]
; Launch after install (skipped in silent mode)
Filename: "{app}\{#AppExeName}"; \
  Description: "Launch {#AppName}"; \
  Flags: nowait postinstall skipifsilent

; Add Windows Firewall inbound rules so children (on WiFi) can reach this PC
Filename: "netsh"; \
  Parameters: "advfirewall firewall add rule name=""SlyLED UDP 4210"" dir=in action=allow protocol=UDP localport=4210 description=""SlyLED child discovery"""; \
  Flags: runhidden waituntilterminated; StatusMsg: "Configuring firewall (UDP 4210)..."
Filename: "netsh"; \
  Parameters: "advfirewall firewall add rule name=""SlyLED HTTP 8080"" dir=in action=allow protocol=TCP localport=8080 description=""SlyLED web UI"""; \
  Flags: runhidden waituntilterminated; StatusMsg: "Configuring firewall (TCP 8080)..."

[UninstallRun]
; Kill any running instance before uninstalling
Filename: "taskkill"; Parameters: "/f /im SlyLED.exe"; Flags: runhidden; RunOnceId: "KillSlyLED"

; Remove firewall rules
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""SlyLED UDP 4210"""; Flags: runhidden; RunOnceId: "FwUDP4210"
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""SlyLED HTTP 8080"""; Flags: runhidden; RunOnceId: "FwTCP8080"

[UninstallDelete]
; Remove the install directory if it's empty after uninstall
Type: dirifempty; Name: "{app}"

[Code]
// Kill running SlyLED.exe before install/upgrade
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  // taskkill /f can trigger a hidden UAC prompt on some machines, causing the
  // wizard to freeze.  Use ewNoWait so the installer never blocks on it.
  Exec('taskkill', '/f /im SlyLED.exe', '', SW_HIDE, ewNoWait, ResultCode);
  // Brief pause — non-blocking in practice (process exit is fast after SIGKILL)
  Sleep(500);
end;

// Ask whether to delete saved state (children, runners, settings) on uninstall
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then begin
    DataDir := ExpandConstant('{userappdata}\SlyLED');
    if DirExists(DataDir) then begin
      if MsgBox(
        'Remove saved data (children, runners, settings) from:'#13#10 + DataDir + '?',
        mbConfirmation, MB_YESNO) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
