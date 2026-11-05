; ---------------------------------------------------------------
; BoxdList installer (Inno Setup)
; Run "pyinstaller" first, then compile this file to get
; installer_output\BoxdListSetup.exe
; ---------------------------------------------------------------

[Setup]
; Fixed ID so a new version replaces the old one instead of installing twice
AppId={{8F3B2A71-5C4D-4E9A-B6D1-2A7E9C1F4D30}
AppName=BoxdList
AppVersion=1.0.7
AppPublisher=Gonçalo Pinto
; Installs in the user folder, so no admin rights are needed
; and the data folder next to the .exe stays writable
PrivilegesRequired=lowest
DefaultDirName={autopf}\BoxdList
DefaultGroupName=BoxdList
DisableProgramGroupPage=yes
; Icon shown in the installer and in Settings > Apps
SetupIconFile=assets\boxdlist.ico
UninstallDisplayIcon={app}\BoxdList.exe
OutputDir=installer_output
OutputBaseFilename=BoxdListSetup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
; Optional checkbox in the installer
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
; Copy everything PyInstaller built
Source: "dist\BoxdList\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
; This Start menu shortcut is what Windows Search finds
Name: "{autoprograms}\BoxdList"; Filename: "{app}\BoxdList.exe"
Name: "{autodesktop}\BoxdList"; Filename: "{app}\BoxdList.exe"; Tasks: desktopicon

[UninstallDelete]
; The app creates this folder at runtime, so remove it on uninstall
Type: filesandordirs; Name: "{app}\data"

[Run]
; "Launch BoxdList" checkbox on the last page
Filename: "{app}\BoxdList.exe"; Description: "Launch BoxdList"; Flags: nowait postinstall skipifsilent
; Only runs in silent mode (our auto update): reopens the app
Filename: "{app}\BoxdList.exe"; Flags: nowait; Check: WizardSilent
