; installer.iss — Inno Setup 6 script for SlyLED Parent
; Build: iscc installer.iss  (from desktop\windows\)
; Or:    run build.bat — it calls iscc automatically if available.

#define AppName      "SlyLED Orchestrator"
#define AppVersion   "1.6.44"
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

[Types]
Name: "full";    Description: "Full installation"
Name: "compact"; Description: "Compact installation"
Name: "custom";  Description: "Custom installation"; Flags: iscustom

[Components]
; #598 — base orchestrator is always installed. Depth runtime is a
; big optional download; left unticked by default even on Full so the
; installer never surprises a user with a 2 GB download.
Name: "core";  Description: "SlyLED Orchestrator (required)"; Types: full compact custom; Flags: fixed
Name: "depth"; Description: "Host-side AI depth runtime (ZoeDepth) — adds 'ZoeDepth (host)' scan method; ~2 GB downloaded after install"
; #623 / #685 — local vision AI (Ollama). Optional, unticked by default.
; Auto-tune now defaults to a deterministic OpenCV `analyzer` (no AI
; needed); this component installs Ollama itself for operators who
; want to opt into a vision-language evaluator. NO model is pulled at
; install time — operator picks one from USER_MANUAL Appendix D
; (qwen2.5vl:3b, llava:7b, etc.) and pulls it via `ollama pull <name>`,
; then selects it in Settings -> AI Runtime -> Active vision model.
Name: "ai";    Description: "Local AI vision-language runtime (Ollama only — no model pulled; operator picks one from USER_MANUAL Appendix D)"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked
Name: "startuprun";  Description: "Start SlyLED when &Windows starts (runs minimised to tray)"; GroupDescription: "Startup:"; Flags: unchecked


[Files]
; Main executable — compiled by PyInstaller
Source: "dist\SlyLED.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: core

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
// #598 — drop a marker file if the user ticked the depth component.
// The orchestrator reads this on first launch and kicks off the
// install in the background so the user sees progress through the
// normal SPA modal (no Inno Setup console window).
procedure CurStepChanged(CurStep: TSetupStep);
var
  MarkerFile: String;
begin
  if CurStep = ssPostInstall then begin
    if WizardIsComponentSelected('depth') then begin
      MarkerFile := ExpandConstant('{app}\depth.install-requested');
      SaveStringToFile(MarkerFile, '1', False);
    end;
    // #623 — AI auto-tune component drops its own marker. The orchestrator
    // downloads + installs Ollama and pulls the vision model on first launch.
    if WizardIsComponentSelected('ai') then begin
      MarkerFile := ExpandConstant('{app}\ollama.install-requested');
      SaveStringToFile(MarkerFile, '1', False);
    end;
  end;
end;

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
  DepthDir: String;
begin
  if CurUninstallStep = usPostUninstall then begin
    // #598 — depth runtime lives in %LOCALAPPDATA%\SlyLED\runtimes\depth
    // (not {userappdata} which is roaming). Offer to remove it separately
    // since a reinstall would otherwise pick up the 2+ GB of stale weights.
    // Weights live in a sibling dir so Reinstall can preserve them.
    // On full uninstall we offer to remove both together.
    DepthDir := ExpandConstant('{localappdata}\SlyLED\runtimes\depth');
    if DirExists(DepthDir)
       or DirExists(ExpandConstant('{localappdata}\SlyLED\runtimes\depth-weights')) then begin
      if MsgBox(
        'Remove the ZoeDepth runtime + cached weights (~2 GB) from:'#13#10
        + ExpandConstant('{localappdata}\SlyLED\runtimes\') + '?',
        mbConfirmation, MB_YESNO) = IDYES then begin
        if DirExists(DepthDir) then
          DelTree(DepthDir, True, True, True);
        if DirExists(ExpandConstant('{localappdata}\SlyLED\runtimes\depth-weights')) then
          DelTree(ExpandConstant('{localappdata}\SlyLED\runtimes\depth-weights'), True, True, True);
      end;
    end;
    // #623 — Ollama installs itself into its own directory (%LOCALAPPDATA%\
    // Programs\Ollama by default) and owns its own uninstaller. We only
    // offer to REMIND the user so shared models aren't silently orphaned.
    if MsgBox(
      'Ollama (used by SlyLED AI auto-tune) was installed separately.'#13#10
      + 'If you want to remove it, open "Apps & Features" in Windows and '
      + 'uninstall Ollama there. Continue?',
      mbInformation, MB_OK) = IDOK then
      ;  // no-op — the message box is purely informational
    DataDir := ExpandConstant('{userappdata}\SlyLED');
    if DirExists(DataDir) then begin
      if MsgBox(
        'Remove saved data (children, runners, settings) from:'#13#10 + DataDir + '?',
        mbConfirmation, MB_YESNO) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
