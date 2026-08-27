#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{680E831C-C13A-4B39-BE9C-C9F59167E913}
AppName=Apple Music Downloader
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\AppleMusicDownloader
DefaultGroupName=Apple Music Downloader
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=AppleMusicDownloader-Setup-windows-x64
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\AppleMusicDownloader.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "..\dist\AppleMusicDownloader-windows-x64.exe"; DestDir: "{app}"; DestName: "AppleMusicDownloader.exe"; Flags: ignoreversion

[Icons]
Name: "{group}\Apple Music Downloader"; Filename: "{app}\AppleMusicDownloader.exe"
Name: "{autodesktop}\Apple Music Downloader"; Filename: "{app}\AppleMusicDownloader.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AppleMusicDownloader.exe"; Description: "Launch Apple Music Downloader"; Flags: nowait postinstall skipifsilent
