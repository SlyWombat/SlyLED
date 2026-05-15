; installer.iss — Inno Setup 6 script for SlyLED Parent
; Build: iscc installer.iss  (from desktop\windows\)
; Or:    run build.bat — it calls iscc automatically if available.

#define AppName      "SlyLED Orchestrator"
#define AppVersion   "2.0.5"
#define AppPublisher "Electric RV Corporation"
#define AppExeName   "SlyLED.exe"
; Unique GUID for this app — keep fixed across releases so updates overwrite
#define AppId        "{{6F3A1D2E-84C7-4B9F-A051-3D28E9F07C14}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://electricrv.ca/slyled
AppCopyright=© Electric RV Corporation
AppUpdatesURL=https://github.com/SlyWombat/SlyLED

; Embed Windows VERSIONINFO into SlyLED-Setup.exe so File Version /
; Product Version / Copyright fields all reflect the release rather
; than Inno Setup's compiler defaults. `VersionInfoVersion` requires
; a 4-component a.b.c.d string — the build script's AppVersion is 3
; components, so we suffix `.0`. ProductVersion mirrors FileVersion
; (operator-visible "Product version" matches "File version").
VersionInfoVersion={#AppVersion}.0
VersionInfoProductVersion={#AppVersion}.0
VersionInfoCompany={#AppPublisher}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
VersionInfoCopyright=© Electric RV Corporation
; (Don't override the displayed string — without VersionInfoTextVersion,
; Inno Setup populates FileVersion from VersionInfoVersion, so File
; Version + Product Version both show the 4-component a.b.c.d string.)

; v1.7.108 — installer requires elevation. The [Run] netsh
; advfirewall calls each need administrator (firewall rule
; manipulation is privileged), and the orchestrator now declares 5
; rules covering its full TCP + UDP surface. Pre-v1.7.108 the script
; ran at PrivilegesRequired=lowest and the netsh adds silently failed
; with "access denied" unless the operator manually right-click → "Run
; as administrator". Requiring admin gives one UAC prompt at install
; start, everything inside [Run] runs elevated, and the install moves
; to C:\Program Files\SlyLED — the conventional location for an app
; that owns inbound firewall rules anyway.
DefaultDirName={autopf}\SlyLED
DefaultGroupName=SlyLED
PrivilegesRequired=admin

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
; Every launcher passes the operator-chosen --port so all entry points
; (Start menu, Desktop, Startup) bind to the port the firewall allows.
; If the operator double-clicks SlyLED.exe directly without a shortcut,
; main.py's port.txt fallback (dropped in {app}) honours the same value.
;
; Start menu
Name: "{group}\{#AppName}";          Filename: "{app}\{#AppExeName}"; Parameters: "--port {code:GetPort}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

; Desktop shortcut (optional)
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: "--port {code:GetPort}"; Tasks: desktopicon

; Windows startup (optional) — pass --no-browser so it doesn't open a tab every boot
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: "--port {code:GetPort} --no-browser"; Tasks: startuprun

[Run]
; Launch after install (skipped in silent mode). The port the operator
; chose on the wizard's port-prompt page is passed as --port so the
; orchestrator binds to the same port the firewall rule allows. Direct
; double-click of SlyLED.exe (no shortcut) still uses --port via the
; port.txt drop in {app} (read by main.py at startup).
Filename: "{app}\{#AppExeName}"; \
  Parameters: "--port {code:GetPort}"; \
  Description: "Launch {#AppName}"; \
  Flags: nowait postinstall skipifsilent

; ── Windows Firewall inbound rules ──────────────────────────────────────
; All five ports must be reachable from the LAN for the orchestrator to
; talk to its children + DMX nodes:
;   - HTTP/SPA: operator-chosen TCP port (default 8080) — browser + Android.
;   - UDP 4210: child performer protocol (PING/PONG, ACTION, gyro orient).
;   - UDP 4211: Android Auto Brightness UDP push (#861 — replaced the
;     HTTP fast path that contended with the playback loop).
;   - UDP 5568: sACN / E1.31 (DMX-over-IP, alternative to Art-Net).
;   - UDP 6454: Art-Net (DMX bridge replies always come back here per
;     reference_artnet_reply_port — discovery sockets must bind 6454 or
;     replies are lost).
; Rule names are fixed (don't include the port) so uninstall can delete
; them without knowing what port the operator picked.
;
; v1.7.109 — delete-before-add. `netsh advfirewall firewall add rule`
; does NOT replace an existing rule with the same name; it creates a
; duplicate. Pre-fix behaviour on upgrade-with-port-change: the old
; port's rule lingered and a new rule was added for the new port —
; both active, old port still open. [UninstallRun] only fires on
; uninstall (not upgrade), so it never cleaned up. Fix: prepend a
; delete to each add. The delete returns "No rules match" (rc=1) when
; no prior rule exists; that's harmless because Inno Setup's default
; flag set ignores netsh's exit code unless `check` is specified.
Filename: "netsh"; \
  Parameters: "advfirewall firewall delete rule name=""SlyLED HTTP"""; \
  Flags: runhidden waituntilterminated; StatusMsg: "Configuring firewall (HTTP)..."
Filename: "netsh"; \
  Parameters: "advfirewall firewall add rule name=""SlyLED HTTP"" dir=in action=allow protocol=TCP localport={code:GetPort} description=""SlyLED orchestrator HTTP/SPA"""; \
  Flags: runhidden waituntilterminated; StatusMsg: "Configuring firewall (HTTP)..."
Filename: "netsh"; \
  Parameters: "advfirewall firewall delete rule name=""SlyLED Children"""; \
  Flags: runhidden waituntilterminated; StatusMsg: "Configuring firewall (UDP 4210 children)..."
Filename: "netsh"; \
  Parameters: "advfirewall firewall add rule name=""SlyLED Children"" dir=in action=allow protocol=UDP localport=4210 description=""SlyLED child performer protocol (PING/PONG/ACTION/GYRO)"""; \
  Flags: runhidden waituntilterminated; StatusMsg: "Configuring firewall (UDP 4210 children)..."
Filename: "netsh"; \
  Parameters: "advfirewall firewall delete rule name=""SlyLED Auto Brightness"""; \
  Flags: runhidden waituntilterminated; StatusMsg: "Configuring firewall (UDP 4211 brightness)..."
Filename: "netsh"; \
  Parameters: "advfirewall firewall add rule name=""SlyLED Auto Brightness"" dir=in action=allow protocol=UDP localport=4211 description=""SlyLED Android Auto Brightness UDP push"""; \
  Flags: runhidden waituntilterminated; StatusMsg: "Configuring firewall (UDP 4211 brightness)..."
Filename: "netsh"; \
  Parameters: "advfirewall firewall delete rule name=""SlyLED sACN"""; \
  Flags: runhidden waituntilterminated; StatusMsg: "Configuring firewall (UDP 5568 sACN)..."
Filename: "netsh"; \
  Parameters: "advfirewall firewall add rule name=""SlyLED sACN"" dir=in action=allow protocol=UDP localport=5568 description=""SlyLED sACN / E1.31 (DMX-over-IP)"""; \
  Flags: runhidden waituntilterminated; StatusMsg: "Configuring firewall (UDP 5568 sACN)..."
Filename: "netsh"; \
  Parameters: "advfirewall firewall delete rule name=""SlyLED Art-Net"""; \
  Flags: runhidden waituntilterminated; StatusMsg: "Configuring firewall (UDP 6454 Art-Net)..."
Filename: "netsh"; \
  Parameters: "advfirewall firewall add rule name=""SlyLED Art-Net"" dir=in action=allow protocol=UDP localport=6454 description=""SlyLED Art-Net (DMX bridge replies)"""; \
  Flags: runhidden waituntilterminated; StatusMsg: "Configuring firewall (UDP 6454 Art-Net)..."

[UninstallRun]
; Kill any running instance before uninstalling
Filename: "taskkill"; Parameters: "/f /im SlyLED.exe"; Flags: runhidden; RunOnceId: "KillSlyLED"

; Remove firewall rules. Rule names match the [Run] block exactly so
; netsh finds + deletes them regardless of which port the operator
; picked at install time.
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""SlyLED HTTP"""; Flags: runhidden; RunOnceId: "FwHTTP"
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""SlyLED Children"""; Flags: runhidden; RunOnceId: "FwChildren"
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""SlyLED Auto Brightness"""; Flags: runhidden; RunOnceId: "FwAutoBri"
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""SlyLED sACN"""; Flags: runhidden; RunOnceId: "FwSACN"
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""SlyLED Art-Net"""; Flags: runhidden; RunOnceId: "FwArtNet"
; Legacy rule names from pre-v1.7.107 installs — clean up so they don't
; leak across upgrades that change the rule taxonomy.
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""SlyLED UDP 4210"""; Flags: runhidden; RunOnceId: "FwLegacyUDP4210"
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""SlyLED HTTP 8080"""; Flags: runhidden; RunOnceId: "FwLegacyHTTP8080"

[UninstallDelete]
; Remove the install directory if it's empty after uninstall
Type: dirifempty; Name: "{app}"

[Code]
// ── Port-prompt wizard page ─────────────────────────────────────────────
// Operator picks the HTTP/SPA port at install time. Default 8080 — but
// some hosts have 8080 reserved by Hyper-V's dynamic port pool (per
// reference_orchestrator_ports memory: "8080 blocked on this machine"),
// so a one-time prompt at install lets the operator pick a free one
// (5600, 8090, 9000, etc.) without editing config files post-install.
//
// The chosen port flows through to:
//   * launcher Parameters: "--port {code:GetPort}" on every shortcut
//   * post-install Run line that opens the orchestrator
//   * netsh firewall TCP rule's localport=
//   * a port.txt drop in {app} so direct double-clicks of SlyLED.exe
//     (without a shortcut) honour the same port via main.py fallback
var
  PortPage: TInputQueryWizardPage;

var
  PortPrevLoaded: Boolean;

procedure InitializeWizard;
begin
  PortPage := CreateInputQueryPage(
    wpSelectComponents,
    'Network Port',
    'Choose the TCP port the orchestrator listens on',
    'SlyLED''s web UI binds this port. Browsers and the Android app '
    + 'connect to http://<this-pc>:<port>/. Pick anything from 1024 to '
    + '65535 — 8080 is the default. If your machine reserves 8080 (some '
    + 'Windows hosts do via Hyper-V), pick another free port like 5600 '
    + 'or 9000.');
  PortPage.Add('Port (1024-65535):', False);
  // Initial default. The "remember last port" lookup happens in
  // CurPageChanged when the page is about to be displayed — `{app}`
  // is not initialized at InitializeWizard time and reading
  // ExpandConstant('{app}\\port.txt') here raises a runtime error
  // (1:996 "an attempt was made to expand the 'app' constant before
  // it was initialized"). The directory page populates {app} before
  // the wizard reaches our port page, so the read is safe there.
  PortPage.Values[0] := '8080';
  PortPrevLoaded := False;
end;

// Lazy-load the previous port choice when the operator reaches the
// port page. By this point Inno Setup has resolved {app} (either from
// the AppId-registry lookup on upgrade, or from the directory page on
// fresh install). PortPrevLoaded gates re-execution if the operator
// navigates back-and-forth so we don't clobber an in-progress edit.
procedure CurPageChanged(CurPageID: Integer);
var
  PrevPortFile: String;
  PrevPort: AnsiString;
  PrevPortInt: Integer;
begin
  if (PortPage <> nil) and (CurPageID = PortPage.ID) and (not PortPrevLoaded) then begin
    PortPrevLoaded := True;
    PrevPortFile := ExpandConstant('{app}\port.txt');
    if FileExists(PrevPortFile) then begin
      if LoadStringFromFile(PrevPortFile, PrevPort) then begin
        PrevPort := Trim(PrevPort);
        PrevPortInt := StrToIntDef(PrevPort, -1);
        if (PrevPortInt >= 1024) and (PrevPortInt <= 65535) then
          PortPage.Values[0] := IntToStr(PrevPortInt);
      end;
    end;
  end;
end;

function GetPort(Param: String): String;
begin
  // {code:GetPort} substitution called from [Run] / [Icons] / firewall
  // rule. Returns the operator's chosen value or 8080 if the wizard
  // page wasn't shown (silent install / unattended).
  if PortPage = nil then
    Result := '8080'
  else begin
    Result := Trim(PortPage.Values[0]);
    if Result = '' then Result := '8080';
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  PortVal: Integer;
  PortStr: String;
begin
  Result := True;
  if (PortPage <> nil) and (CurPageID = PortPage.ID) then begin
    PortStr := Trim(PortPage.Values[0]);
    if PortStr = '' then begin
      MsgBox('Enter a port number (1024-65535).', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    PortVal := StrToIntDef(PortStr, -1);
    if (PortVal < 1024) or (PortVal > 65535) then begin
      MsgBox('Port must be a number between 1024 and 65535.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
end;

// #598 — drop a marker file if the user ticked the depth component.
// The orchestrator reads this on first launch and kicks off the
// install in the background so the user sees progress through the
// normal SPA modal (no Inno Setup console window).
procedure CurStepChanged(CurStep: TSetupStep);
var
  MarkerFile: String;
  PortFile: String;
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
    // Drop port.txt next to SlyLED.exe so a direct double-click of the
    // exe (no shortcut) still binds the operator-chosen port. main.py
    // reads this file when --port is not on the command line.
    PortFile := ExpandConstant('{app}\port.txt');
    SaveStringToFile(PortFile, GetPort(''), False);
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
