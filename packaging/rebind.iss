[Setup]
AppName=Rebind
AppVersion=0.0.1
AppPublisher=Thomas Scharff
DefaultDirName={autopf}\Rebind
DefaultGroupName=Rebind
OutputBaseFilename=rebind-setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
DisableProgramGroupPage=yes

[Files]
Source: "dist\rebind\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Rebind"; Filename: "{app}\rebind.exe"
Name: "{autodesktop}\Rebind"; Filename: "{app}\rebind.exe"

[Run]
Filename: "{app}\rebind.exe"; Description: "Start Rebind"; Flags: nowait postinstall skipifsilent
