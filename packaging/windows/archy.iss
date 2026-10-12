; Inno Setup script for Archy (Windows installer).
;
; Prerequisites (run on Windows):
;   1. Build the app bundle:  pyinstaller packaging\archy.spec --noconfirm
;      -> produces dist\Archy\ (a one-folder bundle with Archy.exe)
;   2. Put the native archive DLL next to the exe (see packaging\windows\README):
;      copy libarchive.dll into dist\Archy\
;   3. Compile this script with Inno Setup 6:
;        ISCC /DAppVersion=<version> packaging\windows\archy.iss
;      -> produces packaging\windows\Output\Archy-<ver>-setup.exe
;
; Optional file associations are behind a checkbox on the Select Tasks page
; (unchecked by default - taking over .zip silently is rude). When ticked, the
; app is registered as a handler for the archive types and shown in "Open with".

#define AppName "Archy"
; The version comes from the command line (ISCC /DAppVersion=1.2.3 ...);
; scripts/publish_build.py passes the one in src/archy/__init__.py.
#ifndef AppVersion
  #define AppVersion "0.3.0"
#endif
#define AppPublisher "Bytefulminds"
#define AppExeName "Archy.exe"
; Path to the PyInstaller onedir output, relative to this script.
#define BundleDir "..\..\dist\Archy"

[Setup]
; A stable AppId (GUID) ties upgrades/uninstall together - keep it constant
; across releases. Generate a fresh one for the real product if desired.
AppId={{5AA7107A-F2E4-4F0A-976C-A29ED181D09E}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename={#AppName}-{#AppVersion}-setup
SetupIconFile=..\archy.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "dutch"; MessagesFile: "compiler:Languages\Dutch.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "assoc"; Description: "Register Archy as a handler for archive files (zip, 7z, tar, rar)"; GroupDescription: "File associations:"; Flags: unchecked

[Files]
; The whole PyInstaller bundle (exe + _internal). Build it first (see header).
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
; AppUserModelID must match WINDOWS_APP_ID in src/archy/app.py, so a pinned
; shortcut and the running app share one taskbar button (with Archy's icon).
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; AppUserModelID: "Archy.Archy"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon; AppUserModelID: "Archy.Archy"

[Registry]
; Register an application ProgId that opens a file passed as an argument, and
; advertise the archive extensions under "Open with" (OpenWithProgids). Only
; written when the user ticks the "assoc" task. This does not steal the default
; handler; the user can still choose "Always open with" themselves.
Root: HKA; Subkey: "Software\Classes\Archy.Archive"; ValueType: string; ValueName: ""; ValueData: "Archive"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\Archy.Archive\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.zip\OpenWithProgids"; ValueType: string; ValueName: "Archy.Archive"; ValueData: ""; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.7z\OpenWithProgids"; ValueType: string; ValueName: "Archy.Archive"; ValueData: ""; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.tar\OpenWithProgids"; ValueType: string; ValueName: "Archy.Archive"; ValueData: ""; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.gz\OpenWithProgids"; ValueType: string; ValueName: "Archy.Archive"; ValueData: ""; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.rar\OpenWithProgids"; ValueType: string; ValueName: "Archy.Archive"; ValueData: ""; Flags: uninsdeletevalue; Tasks: assoc

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
