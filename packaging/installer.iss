; ตัวติดตั้งสำหรับ Windows (Inno Setup 6) — build ด้วย GitHub Actions: .github/workflows/build-windows.yml
; ต้อง build โฟลเดอร์ dist\VideoDownloader ด้วย packaging\build.py ก่อน
; ไฟล์นี้ต้องบันทึกเป็น UTF-8 แบบมี BOM เพื่อให้ภาษาไทยแสดงถูกต้อง

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{A28DE9FD-286B-4823-A9D8-82CE2CF6EF76}
AppName=Video Downloader (โหลดวิดีโอ)
AppVersion={#AppVersion}
AppVerName=Video Downloader {#AppVersion}
DefaultDirName={localappdata}\Programs\VideoDownloader
DefaultGroupName=Video Downloader
DisableProgramGroupPage=yes
DisableDirPage=yes
; ติดตั้งเฉพาะผู้ใช้คนนี้ ไม่ต้องใช้สิทธิ์ Admin
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\dist
OutputBaseFilename=VideoDownloader-Setup
SetupIconFile=..\build\icon.ico
UninstallDisplayIcon={app}\VideoDownloader.exe
UninstallDisplayName=Video Downloader (โหลดวิดีโอ)
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\VideoDownloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; ลบไฟล์ของเวอร์ชันเก่าก่อนติดตั้งทับ
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\extension"

[Registry]
; ลิงก์ videodownloader://start ให้ส่วนเสริมเบราว์เซอร์สั่งเปิดแอปได้
Root: HKCU; Subkey: "Software\Classes\videodownloader"; ValueType: string; ValueName: ""; ValueData: "URL:Video Downloader"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\videodownloader"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\videodownloader\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\VideoDownloader.exe,0"
Root: HKCU; Subkey: "Software\Classes\videodownloader\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\VideoDownloader.exe"" ""%1"""

[Icons]
Name: "{autoprograms}\โหลดวิดีโอ"; Filename: "{app}\VideoDownloader.exe"
Name: "{autodesktop}\โหลดวิดีโอ"; Filename: "{app}\VideoDownloader.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\VideoDownloader.exe"; Description: "{cm:LaunchProgram,Video Downloader}"; Flags: nowait postinstall skipifsilent
