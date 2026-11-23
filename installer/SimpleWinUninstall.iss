; Inno Setup script for SimpleWinUninstall.
;
; Build it with:   .\build.ps1 -Installer      (or double-click BUILD-INSTALLER.cmd)
; or by hand:      iscc installer\SimpleWinUninstall.iss
;
; Produces dist\SimpleWinUninstallSetup.exe
;
; Updating is the same installer: AppId below never changes, so running a
; newer setup upgrades the existing install in place rather than leaving two
; copies behind. No background updater service, no scheduled task, and the
; app itself still makes no network connections of any kind.
;
; The install folder is safe from the app itself: hudkit.paths.self_paths()
; covers the frozen exe's own directory, so SimpleWinUninstall will never offer
; to remove its own files, even when you point it at its own entry.

#define AppName       "SimpleWinUninstall"
#define AppPublisher  "Michael Calkins"
#define AppExeName    "SimpleWinUninstall.exe"
#define AppUrl        "https://github.com/iCodeForBananas/simplewinuninstall"

; Version is passed in by build.ps1 (/DAppVersion=0.1.0). Fall back so the
; script still compiles when run directly.
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
; Never change AppId - it is what makes a new build upgrade the old one.
AppId={{3B7D2E91-5C4A-4F08-A6E3-9D1C7B5F2A84}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppSupportURL={#AppUrl}
AppUpdatesURL={#AppUrl}/releases
VersionInfoVersion={#AppVersion}

; Per-user install: no UAC prompt to install. The app offers to restart
; elevated itself, and an app's own uninstaller raises its own UAC prompt.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto

OutputDir=..\dist
OutputBaseFilename=SimpleWinUninstallSetup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

LicenseFile=..\LICENSE
SetupIconFile=..\assets\simplewinuninstall.ico
InfoBeforeFile=..\installer\before-install.txt
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
    GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";          DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "..\LICENSE";            DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";                 Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}";       Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";           Filename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; \
    Description: "Open {#AppName} now"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only the logs. The backups folder next to it holds the .reg exports of every
; registry key, service and task this app removed - the only way to undo
; those - so uninstalling the tool must not throw them away.
Type: filesandordirs; Name: "{localappdata}\{#AppName}\logs"
Type: dirifempty;     Name: "{localappdata}\{#AppName}"

[Messages]
BeveledLabel={#AppName} - free and open source, MIT licensed
