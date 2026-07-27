; Patchwerk setup wizard for Windows (Inno Setup 6).
;
; Build it with:   iscc build\windows\patchwerk.iss
; after `python3 packaging/build.py windows` has staged build\windows\Patchwerk.
; That folder already contains a complete CPython 3.12 with every dependency
; installed, so this script installs FILES ONLY — there is no pip step, no
; venv, no internet requirement, and nothing that can half-succeed.
;
; @VERSION@ is substituted by build.py.

#define AppName      "Patchwerk"
#define AppVersion   "@VERSION@"
#define AppPublisher "Patchwerk"
#define AppURL       "https://github.com/Jew-C-Fruit/Patchwerk"
#define SCURL        "https://supercollider.github.io/downloads"

[Setup]
; A stable AppId is what makes an upgrade an UPGRADE rather than a second
; copy. Never change it once a build has shipped.
AppId={{7F3C1A62-5D48-4E9B-9E2A-1C6B0E7A4D31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputBaseFilename={#AppName}-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The bundled CPython is x86_64. Say so, rather than letting Setup run and
; then failing at launch on an arm64-only or 32-bit machine.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-machine by default, but a user without admin rights can still install
; into their own profile instead of being stopped dead.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
LicenseFile=Patchwerk\app\LICENSE
SetupIconFile=Patchwerk\Patchwerk.ico
UninstallDisplayIcon={app}\Patchwerk.ico
UninstallDisplayName={#AppName} {#AppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
  GroupDescription: "Additional shortcuts:"

[Files]
Source: "Patchwerk\*"; DestDir: "{app}"; \
  Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
; The normal way in: pythonw.exe, so there is no console window. Working
; directory matters — launcher.py finds the app tree relative to itself, but
; the engine is spawned with cwd={app}\app and inherits this otherwise.
Name: "{group}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; \
  Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; \
  IconFilename: "{app}\Patchwerk.ico"
; The diagnostic way in, kept next to it deliberately: when a user says "it
; won't start", this is what you ask them to run.
Name: "{group}\{#AppName} (show log)"; Filename: "{app}\Patchwerk-console.bat"; \
  WorkingDir: "{app}"; IconFilename: "{app}\Patchwerk.ico"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; \
  Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; \
  IconFilename: "{app}\Patchwerk.ico"; Tasks: desktopicon

[Run]
; Offered only when SuperCollider is genuinely absent, so the common case —
; already installed — never sees a nagging checkbox.
Filename: "{#SCURL}"; \
  Description: "Download SuperCollider (required — it is not installed yet)"; \
  Flags: shellexec postinstall skipifsilent; Check: NeedsSuperCollider
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; \
  WorkingDir: "{app}"; Description: "Launch {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; pip and the engine write bytecode next to the sources after install; without
; this the uninstall leaves a tree of __pycache__ folders behind.
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\python"
Type: files; Name: "{app}\*.pyc"

[Code]
{ ---------------------------------------------------------------------------
  SuperCollider is DETECTED, never installed by us — the reasoning is in
  packaging/README.md. This mirrors boot_core.find_scsynth's Windows branch:
  the SuperCollider installer's default directory carries a version suffix
  (SuperCollider-3.13.0), so the name has to be globbed, not guessed. Keep
  the two in step — a wizard that says "found" where the launcher says
  "missing" is worse than no check at all.
  --------------------------------------------------------------------------- }

function ScsynthIn(Root: String): String;
var
  FR: TFindRec;
  Cand: String;
begin
  Result := '';
  if Root = '' then Exit;
  if FindFirst(AddBackslash(Root) + 'SuperCollider*', FR) then
  begin
    try
      repeat
        if (FR.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
        begin
          Cand := AddBackslash(Root) + FR.Name + '\scsynth.exe';
          if FileExists(Cand) then
          begin
            Result := Cand;
            Exit;
          end;
        end;
      until not FindNext(FR);
    finally
      FindClose(FR);
    end;
  end;
end;

function FindScsynth(): String;
begin
  Result := ScsynthIn(ExpandConstant('{commonpf}'));
  if Result = '' then
    Result := ScsynthIn(ExpandConstant('{commonpf32}'));
  if Result = '' then
    Result := ScsynthIn(ExpandConstant('{localappdata}'));
end;

function NeedsSuperCollider(): Boolean;
begin
  Result := FindScsynth() = '';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { Told on the Ready page, before any files are copied, so the user can go
    and get SuperCollider while Setup runs rather than discovering the gap at
    first launch. It is a warning, not a blocker: installing Patchwerk first
    and SuperCollider second works fine — the launcher re-checks. }
  if (CurPageID = wpReady) and NeedsSuperCollider() then
    MsgBox('SuperCollider is not installed on this computer.' #13#13
           'Patchwerk''s audio engine is SuperCollider''s scsynth. It is a '
           'free, separate download, and Patchwerk cannot make sound without '
           'it.' #13#13
           'Setup will continue. On the last page, tick "Download '
           'SuperCollider" — or install it any time later and start '
           'Patchwerk again; it checks every launch.',
           mbInformation, MB_OK);
end;
