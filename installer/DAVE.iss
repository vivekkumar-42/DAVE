#ifndef MyAppName
#define MyAppName "DAVE"
#endif

#ifndef MyAppVersion
#define MyAppVersion "0.3.0"
#endif

[Setup]
AppId={{6E0A203B-89AB-4A72-8D8B-7F7E6E01B0A7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=DAVE
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=..\release
OutputBaseFilename=DAVE-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\DAVE.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Exclude runtime-generated logs/metrics from being shipped inside the installer.
Source: "..\release\DAVE\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "data\*"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\DAVE.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\DAVE.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\DAVE.exe"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
