; Script de Inno Setup para generar DJNova-Setup-Win64.exe
#define MyAppName "DJNova"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "DJNova Project (GPL-3.0)"
#define MyAppExeName "DJNova.exe"

[Setup]
AppId={{8D6B4F7A-9C3E-4C1B-8D9A-2E7B1C4F6A10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist
OutputBaseFilename=DJNova-Setup-{#MyAppVersion}-Win64
SetupIconFile=..\..\assets\logo\djnova.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\DJNova\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
