; Inno Setup script for DeskSpace. Built by build\build.ps1 (needs Inno Setup 6).
;
; Installs per user (no admin prompt) into %LOCALAPPDATA%\Programs\DeskSpace:
;   runtime\   official PSF-signed embeddable Python + libraries; DeskSpace.exe is a renamed copy of the
;              signed pythonw.exe, so Windows' Smart App Control lets it run
;   app\       the DeskSpace source
; Your fences, notes and settings live in %USERPROFILE%\DeskSpace and are never touched by
; installing, updating or uninstalling.
;
; DeskSpace was called DeskFences before 1.1. It keeps the same AppId, so this upgrades an existing
; DeskFences install: the old program folder, shortcuts and startup entry are removed, and the app
; moves the old %USERPROFILE%\DeskFences data folder over when it starts.

#define AppName "DeskSpace"
#define OldName "DeskFences"
#define AppVersion "1.4.1"
#define Launcher "{app}\runtime\DeskSpace.exe"
#define Script "{app}\app\deskspace.pyw"

[Setup]
AppId={{6F1C0E2A-5B7D-4C3E-9A8F-2D4B6E8A1C90}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
DefaultDirName={localappdata}\Programs\{#AppName}
; Don't reuse the old ...\Programs\DeskFences folder from before the rename.
UsePreviousAppDir=no
DefaultGroupName={#AppName}
UsePreviousGroup=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=DeskSpace-Setup-{#AppVersion}
SetupIconFile=deskspace.ico
UninstallDisplayIcon={app}\deskspace.ico
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The running app holds this mutex (older versions the second one); setup and uninstall ask to close it first.
AppMutex=Local\DeskSpaceSingleInstance,Local\DeskFencesSingleInstance
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "startup"; Description: "Start DeskSpace when I sign in to Windows"; GroupDescription: "Options:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Options:"; Flags: unchecked

[Files]
Source: "..\dist\stage\runtime\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\stage\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\stage\deskspace.ico"; DestDir: "{app}"

[InstallDelete]
; An update replaces the app's code wholesale, so files removed from DeskSpace don't linger.
Type: filesandordirs; Name: "{app}\app"
; Left over from before the rename.
Type: filesandordirs; Name: "{localappdata}\Programs\{#OldName}"
Type: filesandordirs; Name: "{userprograms}\{#OldName}"
Type: files; Name: "{userdesktop}\{#OldName}.lnk"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{#Launcher}"; Parameters: """{#Script}"""; WorkingDir: "{app}\app"; \
    IconFilename: "{app}\deskspace.ico"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{#Launcher}"; Parameters: """{#Script}"""; WorkingDir: "{app}\app"; \
    IconFilename: "{app}\deskspace.ico"; Tasks: desktopicon

[Registry]
; Exactly what the app's own "Start with Windows" setting writes, so the two always agree.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; \
    ValueData: """{#Launcher}"" ""{#Script}"""; Tasks: startup
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{#OldName}"; \
    Flags: deletevalue

[Run]
Filename: "{#Launcher}"; Parameters: """{#Script}"""; WorkingDir: "{app}\app"; Description: "Launch {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Messages]
FinishedLabel=DeskSpace is installed. Your existing fences, notes and settings (in your DeskSpace folder) are kept.

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Value: String;
begin
  // Remove the start-with-Windows entry if it points at this install - whether setup or the
  // app's own Settings created it - so Windows isn't left starting a program that's gone.
  if CurUninstallStep = usPostUninstall then
    if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#AppName}', Value) then
      if Pos(Lowercase(ExpandConstant('{app}')), Lowercase(Value)) > 0 then
        RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#AppName}');
end;
