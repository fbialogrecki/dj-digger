#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
[Setup]
AppId={{0A2D7B4C-42B7-4C68-8A94-7032A35116D3}
AppName=dj-digger
AppVersion={#AppVersion}
AppPublisher=Filip Białogrecki
DefaultDirName={localappdata}\Programs\dj-digger
DefaultGroupName=dj-digger
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
CloseApplications=no
RestartApplications=no
AppMutex=dj-digger-desktop-install
OutputDir=..\..\dist\installer
OutputBaseFilename=dj-digger-{#AppVersion}-windows-x64-test
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\dj-digger-gui.exe
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"
[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
[Files]
Source: "..\..\dist\dj-digger\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\dj-digger"; Filename: "{app}\dj-digger-gui.exe"
Name: "{autodesktop}\dj-digger"; Filename: "{app}\dj-digger-gui.exe"; Tasks: desktopicon
