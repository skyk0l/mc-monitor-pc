#ifndef AppVersion
  #error AppVersion must be supplied by the build workflow
#endif

[Setup]
AppId={{596DB14A-9024-4B70-A6F0-6DF81CF0C35B}
AppName=MC Monitor
AppVersion={#AppVersion}
AppPublisher=MC Monitor
AppPublisherURL=https://github.com/skyk0l/mc-monitor-pc
DefaultDirName={autopf}\MC Monitor
DefaultGroupName=MC Monitor
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=MC-Monitor-Setup-{#AppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\MC-Monitor.exe
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: checkedonce

[Files]
Source: "dist\MC-Monitor.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\MC Monitor"; Filename: "{app}\MC-Monitor.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\MC Monitor"; Filename: "{app}\MC-Monitor.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\MC-Monitor.exe"; Description: "Запустить MC Monitor"; Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallDelete]
Type: files; Name: "{app}\installed.marker"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not SaveStringToFile(ExpandConstant('{app}\installed.marker'), '{#AppVersion}', False) then
      RaiseException('Не удалось сохранить отметку установки.');
  end;
end;
