; WinQ installer (Inno Setup 6). Build with tools\build_release.ps1, which passes the
; version from src\winq\__init__.py:
;   ISCC.exe /DAppVersion=0.9.0 installer\winq.iss
; Installs per user (no admin), registers .winq show files, Start menu entry, optional
; desktop shortcut. Settings and autosaves in %APPDATA%\WinQ are kept on uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "WinQ"
#define AppExe "WinQ.exe"
#define ProgId "WinQ.Show"

[Setup]
; Never change AppId: it is how upgrades find the existing install.
AppId={{6E1B7C3A-9F2D-4B8E-A5C1-3D7F0E2B9A64}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=WinQ
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} installer
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no
WizardStyle=modern
LicenseFile=..\LICENSE.txt
AppCopyright=Copyright (c) 2026 Benedek Miko-Szabo and other contributors
SetupIconFile=..\assets\winq.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
SourceDir=.
OutputDir=..\dist\installer
OutputBaseFilename=WinQ-{#AppVersion}-Setup
Compression=lzma2/ultra64
SolidCompression=yes
LZMANumBlockThreads=4

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Remove files from an older version first, so nothing stale is left in _internal.
Source: "..\dist\WinQ\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; .winq → WinQ (per user: HKA = HKCU for a non-admin install).
Root: HKA; Subkey: "Software\Classes\.winq"; ValueType: string; ValueName: ""; ValueData: "{#ProgId}"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.winq\OpenWithProgids"; ValueType: string; ValueName: "{#ProgId}"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\{#ProgId}"; ValueType: string; ValueName: ""; ValueData: "WinQ Show"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#ProgId}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"
Root: HKA; Subkey: "Software\Classes\{#ProgId}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".winq"; ValueData: ""; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
