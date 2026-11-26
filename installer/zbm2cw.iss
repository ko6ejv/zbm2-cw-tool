; ZBM2 CW Tool - Windows installer (Inno Setup 6)
; Built by GitHub Actions: ISCC /DAppVersion=x.y.z installer\zbm2cw.iss

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#define AppName "ZBM2 CW Tool"
#define AppExe "ZBM2 CW Tool.exe"

[Setup]
AppId={{4183A34D-51AD-4F58-85B6-A1030FA8AD5A}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=ZBM2 Industries
AppPublisherURL=https://zbm2industries.com
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=ZBM2-CW-Tool-Setup-{#AppVersion}
SetupIconFile=..\icon.ico
InfoBeforeFile=..\PRIVACY.txt
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoCompany=ZBM2 Industries
UninstallDisplayIcon={app}\{#AppExe}
WizardStyle=modern
WizardImageFile=wizard_large.bmp
WizardSmallImageFile=wizard_small.bmp
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
; in-app updates run this silently: close the running app, reuse the old folder, start the new one
CloseApplications=force
UsePreviousAppDir=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "net_update"; Description: "Check GitHub for new versions at startup"; GroupDescription: "Internet features (see the privacy page; change any time in Rig Setup):"
Name: "net_bandcond"; Description: "HF band conditions (hamqsl.com)"; GroupDescription: "Internet features (see the privacy page; change any time in Rig Setup):"
Name: "net_rbn"; Description: "Live CW activity (Reverse Beacon Network; logs in with your callsign)"; GroupDescription: "Internet features (see the privacy page; change any time in Rig Setup):"

[Code]
function B(T: String): String;
begin
  if WizardIsTaskSelected(T) then Result := 'true' else Result := 'false';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SaveStringToFile(ExpandConstant('{app}\install_options.json'),
      '{"update_check": ' + B('net_update') + ', "bandcond_enable": ' + B('net_bandcond') +
      ', "rbn_enable": ' + B('net_rbn') + '}', False);
end;

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName} now (the setup wizard will walk you through the rest)"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExe}"; Flags: nowait skipifnotsilent

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nIt decodes CW (Morse code) from your radio, translates it to plain English, and sends replies for you at the other station's speed.%n%nAfter installing, a setup wizard helps you connect your radio.
