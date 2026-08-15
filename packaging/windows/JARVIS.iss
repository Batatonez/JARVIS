; Installer do JARVIS para Windows (Inno Setup 6).
;
; Gerado por `scripts/build_windows.py`, que injeta as três definições
; abaixo — nunca compile este arquivo à mão sem elas:
;
;     JarvisVersion   versão oficial (fonte: config.settings.Settings)
;     SourceDir       pasta do standalone produzido pelo PyInstaller
;     OutputDir       onde o JARVIS-Setup-<versão>.exe deve ser escrito
;
; ---------------------------------------------------------------------
; Instalação POR USUÁRIO, não para a máquina inteira
; ---------------------------------------------------------------------
; `PrivilegesRequired=lowest` + instalação em `%LOCALAPPDATA%\Programs` é a
; mesma escolha do VS Code, Discord e Spotify, e ela resolve dois problemas
; de uma vez:
;
;   1. Não pede elevação. O usuário instala sem senha de administrador — e
;      um app que exige admin para ser instalado é um app que muita gente
;      simplesmente não instala.
;   2. `Program Files` é somente leitura para usuário padrão. Instalar lá
;      obrigaria a lidar com elevação também nas atualizações.
;
; Os DADOS do usuário não ficam aqui de qualquer forma: vão para
; `%LOCALAPPDATA%\JARVIS` (ver config/paths.py). A pasta de instalação
; contém só o programa, e pode ser apagada e recriada sem perder nada.

#ifndef JarvisVersion
  #error Compile via scripts/build_windows.py (falta /DJarvisVersion)
#endif
#ifndef SourceDir
  #error Compile via scripts/build_windows.py (falta /DSourceDir)
#endif
#ifndef OutputDir
  #error Compile via scripts/build_windows.py (falta /DOutputDir)
#endif

#define AppName "JARVIS"
#define AppPublisher "JARVIS"
#define AppExeName "JARVIS.exe"

[Setup]
AppId={{8C4F2A16-6E2B-4B3D-9E51-3A7C0D2F5B94}
AppName={#AppName}
AppVersion={#JarvisVersion}
AppVerName={#AppName} {#JarvisVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#JarvisVersion}

DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Sem página de licença: o projeto ainda não definiu uma licença (registrado
; como limitação em docs/BUILD_WINDOWS.md). Escolher uma aqui seria decidir
; algo que não cabe ao instalador.

PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

OutputDir={#OutputDir}
OutputBaseFilename=JARVIS-Setup-{#JarvisVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

; Recusa instalar em Windows antigo demais para o Qt 6 que o app usa.
MinVersion=10.0

; Sem UPX, sem obfuscação: packer é o que mais gera falso positivo de
; antivírus, e o ganho de tamanho não justifica (ver jarvis.spec).
#ifexist "assets\jarvis.ico"
SetupIconFile=assets\jarvis.ico
UninstallDisplayIcon={app}\{#AppExeName}
#endif

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; Iniciar com o Windows fica DESMARCADO por padrão e é escolha explícita:
; um app que se instala no boot sem perguntar é um app que o usuário
; desinstala.
Name: "startupicon"; Description: "Iniciar o JARVIS junto com o Windows"; GroupDescription: "Inicialização:"; Flags: unchecked

[Files]
; `recursesubdirs` + `createallsubdirs`: o layout onedir do PyInstaller tem
; a pasta `_internal` inteira, e ela precisa chegar intacta.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Só o que o PRÓPRIO programa gera dentro da pasta de instalação. Os dados
; do usuário NÃO estão aqui e não são tocados — ver [Code] abaixo.
Type: filesandordirs; Name: "{app}\_internal"

[Code]
{ ---------------------------------------------------------------------
  Desinstalação: os dados pessoais são PRESERVADOS por padrão.

  Contas, chats, memória e configurações vivem em %LOCALAPPDATA%\JARVIS,
  fora da pasta de instalação, então uma desinstalação normal já não os
  alcança. A pergunta abaixo existe para quem realmente quer apagar tudo —
  e a resposta padrão é NÃO. Apagar dados do usuário sem perguntar seria
  transformar "desinstalar o programa" em "perder o histórico", que são
  coisas diferentes e o usuário pode querer só a primeira (por exemplo,
  para reinstalar).
  --------------------------------------------------------------------- }

function UserDataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\JARVIS');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := UserDataDir();
    if DirExists(DataDir) then
    begin
      if MsgBox(
           'Remover também seus dados pessoais do JARVIS?' + #13#10#13#10 +
           'Isso apaga contas, conversas, memória e configurações em:' + #13#10 +
           DataDir + #13#10#13#10 +
           'Escolha Não para manter seus dados (recomendado se você pretende reinstalar).',
           mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;
