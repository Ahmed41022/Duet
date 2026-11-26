#define AppName "Duet"
#ifndef AppVersion
#define AppVersion "0.1.0"
#endif
#define AppPublisher "Duet"

[Setup]
AppId={{C3D20795-198F-4C9F-A0AD-5516D84EC863}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Duet
DefaultGroupName=Duet
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=Duet-Setup-{#AppVersion}-win64
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\duet-desktop.exe

[Files]
Source: "..\dist\duet-desktop\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\Duet"; Filename: "{app}\duet-desktop.exe"
Name: "{userdesktop}\Duet"; Filename: "{app}\duet-desktop.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\duet-desktop.exe"; Description: "Launch Duet"; Flags: postinstall nowait skipifsilent
