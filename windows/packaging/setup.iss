#define Version GetFileVersion("..\..\out\windows\Lianwang.exe")
[Setup]
AppId=LianwangWindows
AppName=联网
AppVersion={#Version}
DefaultDirName={autopf}\Lianwang
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
SetupIconFile=..\src\App.ico
OutputDir=..\..\out
OutputBaseFilename=Lianwang-Windows-x64-Setup
Compression=lzma2/fast
SolidCompression=yes
UninstallDisplayIcon={app}\Lianwang.exe
CloseApplications=yes
RestartApplications=no
[Files]
Source: "..\..\out\windows\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
[Icons]
Name: "{commonprograms}\联网"; Filename: "{app}\Lianwang.exe"
[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Lianwang"; ValueData: """{app}\Lianwang.exe"""; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\LianwangWindows"; Flags: uninsdeletekey
[Run]
Filename: "{app}\Lianwang.exe"; Parameters: "--prepare"; Flags: runasoriginaluser runhidden waituntilterminated
Filename: "{app}\Lianwang.exe"; Flags: runasoriginaluser nowait skipifsilent
[UninstallRun]
Filename: "{app}\Lianwang.exe"; Parameters: "--remove-hook"; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "stop LianwangWindows"; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "delete LianwangWindows"; Flags: runhidden waituntilterminated
[Code]
procedure Checked(const FileName, Arguments: String);
var Code: Integer;
begin
  if not Exec(FileName, Arguments, '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
    RaiseException('无法初始化联网后台 (' + IntToStr(Code) + ')');
end;
procedure CurStepChanged(Step: TSetupStep);
var Exe, Sc: String; Exists: Boolean; Code: Integer;
begin
  if Step = ssPostInstall then begin
    Exe := ExpandConstant('{app}\Lianwang.exe');
    Sc := ExpandConstant('{sys}\sc.exe');
    Checked(Exe, '--owner');
    Exists := Exec(Sc, 'query LianwangWindows', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
    if not Exists then
      Checked(Sc, 'create LianwangWindows binPath= "\"' + Exe + '\" --service" start= auto DisplayName= "联网后台"');
    if not Exists then Checked(Sc, 'start LianwangWindows')
    else Exec(Sc, 'start LianwangWindows', '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
end;
