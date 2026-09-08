; AI嵌入式助手 安装脚本（Inno Setup 6）
; 用法：ISCC.exe AIEmbeddedAssistant.iss（在工程根目录执行）

#define MyAppName "AI嵌入式助手"
#define MyAppVersion "0.2.0"
#define MyAppPublisher "HC"
#define MyAppExeName "AIEmbeddedAssistant.exe"
#define MyAppId "{{C4A2B0F8-6E1C-4D3A-9B2E-AIEmbeddedAssistant}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableDirPage=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\dist-installer
OutputBaseFilename=AIEmbeddedAssistant-Setup
SetupIconFile=logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："
Name: "jlinkdep"; Description: "本机安装 SEGGER J-Link 软件（RTT 模式需要，完成后打开官方下载页）"; GroupDescription: "外部依赖："; Flags: unchecked

[Files]
Source: "..\dist\AIEmbeddedAssistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "logo.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\logo.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
Filename: "https://www.segger.com/downloads/jlink"; Description: "打开 SEGGER J-Link 软件下载页"; Flags: nowait skipifsilent shellexec; Tasks: jlinkdep
