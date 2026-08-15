# Build da distribuição Windows

Este documento é para **desenvolvedores**. Quem só quer usar o JARVIS baixa o
`JARVIS-Setup.exe` das [Releases](https://github.com/Batatonez/JARVIS/releases)
e não precisa de nada disto.

## Pré-requisitos

| Ferramenta | Por quê | Como |
|---|---|---|
| Python 3.10+ | rodar o projeto e o build | já necessário para desenvolvimento |
| Dependências do projeto | PySide6 e o resto | `pip install -r requirements.txt` |
| PyInstaller | gera o standalone | `pip install -r requirements-build.txt` |
| Inno Setup 6 | gera o `JARVIS-Setup.exe` | `winget install JRSoftware.InnoSetup` |

O Inno Setup **não** é um pacote Python e não é instalado pelo pip. Sem ele o
build ainda funciona: produz o standalone e avisa que o installer foi pulado.

O `winget` instala em `%LOCALAPPDATA%\Programs\Inno Setup 6` e **não** coloca
o `ISCC.exe` no PATH — o build procura lá automaticamente. Se estiver em
outro lugar, aponte:

```bash
set INNO_SETUP_ISCC=C:\caminho\para\ISCC.exe
```

## Gerar tudo

```bash
python scripts/build_windows.py
```

Variantes:

```bash
python scripts/build_windows.py --skip-installer
```

```bash
python scripts/build_windows.py --dev
```

`--dev` mantém o console visível — é onde o traceback aparece quando o
executável empacotado falha. **Nunca distribua um build `--dev`**: além do
console, ele não é o modo que o installer espera.

## O que o script faz

1. Lê a versão de `config.settings.Settings.core_version` e confere contra o
   `pyproject.toml`. Divergência é erro de build, não algo a resolver
   escolhendo um dos dois em silêncio.
2. Confere as dependências de build.
3. Limpa `build/` e `dist/`. A limpeza recusa qualquer caminho que não esteja
   dentro do projeto e não se chame `build` ou `dist` — um caminho montado
   errado não pode virar um apagamento na pasta do usuário.
4. Gera `packaging/windows/version_info.txt` (metadados de versão do Windows)
   a partir da versão oficial. Este arquivo é gerado, não versionado.
5. Roda o PyInstaller com `packaging/windows/jarvis.spec`.
6. Verifica que os arquivos essenciais entraram no artefato — inclusive o QML
   e os componentes de STT.
7. Audita o artefato procurando credencial e arquivo proibido.
8. Compila o installer com o Inno Setup.
9. Calcula os SHA-256.

## Artefatos

```
dist/
├── JARVIS/                      standalone (onedir)
│   ├── JARVIS.exe
│   └── _internal/               Qt, Python, QML, DLLs
├── JARVIS-Setup-<versão>.exe    installer
└── SHA256SUMS.txt
```

## Decisões de packaging

### PyInstaller, não Nuitka

Nuitka compila para C e produz binário mais rápido e menor. Foi descartado
para este projeto por três razões concretas: o suporte a PySide6/QML do
PyInstaller é mais maduro e mais testado, o tempo de build é ordens de
grandeza menor (2 minutos contra dezenas), e o ganho de performance de
runtime é irrelevante aqui — o JARVIS passa o tempo esperando I/O de rede e
do usuário, não em CPU de Python.

Se um dia a inicialização virar gargalo medido, Nuitka volta à mesa.

### ONEDIR, não ONEFILE

`--onefile` extrai o bundle inteiro para um diretório temporário a cada
execução. Com centenas de MB isso são segundos de espera antes do primeiro
pixel, todas as vezes — e "executável que se descompacta em `%TEMP%` e roda
de lá" é o padrão que heurística de antivírus procura.

O argumento a favor do onefile — "o usuário lida com um arquivo só" — não se
aplica: quem entrega um arquivo só é o **installer**. Ele esconde a pasta de
qualquer jeito.

### Inno Setup, não MSI/WiX

MSI faz sentido para implantação corporativa via política de grupo. Para
distribuição direta ao usuário final, o Inno Setup é mais simples de manter,
tem melhor experiência padrão e suporta instalação por usuário sem
elevação. Adicionar MSI depois não exige desfazer nada: seria um segundo alvo
a partir do mesmo standalone em `dist/JARVIS/`.

### Instalação por usuário

O installer instala em `%LOCALAPPDATA%\Programs\JARVIS` com
`PrivilegesRequired=lowest` — mesma escolha do VS Code, Discord e Spotify.
Não pede senha de administrador, e um app que exige admin é um app que muita
gente não instala.

## Onde ficam os dados

| | Caminho | Some ao desinstalar? |
|---|---|---|
| Programa | `%LOCALAPPDATA%\Programs\JARVIS` | sim |
| Dados do usuário | `%LOCALAPPDATA%\JARVIS` | **não**, a menos que o usuário confirme |

Contas, conversas, memória, configurações regionais, logs e modelos de voz
ficam **fora** da pasta de instalação. É isso que faz uma atualização
preservar tudo: o installer substitui o programa e não toca nos dados.

Ver `config/paths.py`. Rodando do código-fonte, os dois caminhos continuam
sendo a raiz do repositório — desenvolvimento não muda de comportamento e
nenhum banco existente é movido.

## Segredos

O installer **nunca** contém `.env`, chave de API, credencial de SMTP, token
de sessão, segredo TOTP ou código de recuperação. O passo 7 do build audita o
artefato e falha se encontrar algum.

Em produção, as chaves de provider vêm do ambiente ou de um `.env` que o
**usuário** cria em `%LOCALAPPDATA%\JARVIS`. O `.env.example` acompanha o app
como referência de quais variáveis existem — e ele não contém valor nenhum.

## Assinatura de código

O executável **não é assinado**. Consequência real: o SmartScreen do Windows
mostra "O Windows protegeu o computador" na primeira execução, e o usuário
precisa clicar em "Mais informações" → "Executar assim mesmo".

Isso não é evitável sem um certificado de assinatura de código, que é pago e
emitido para uma entidade verificada. O projeto não tem um, e criar um
certificado auto-assinado não resolveria (o SmartScreen não confia nele) além
de ser uma declaração falsa de identidade.

Se um certificado existir no futuro, a assinatura entra em dois pontos, nesta
ordem:

1. `dist/JARVIS/JARVIS.exe`, depois do passo 5 e antes do 8 — o installer
   precisa empacotar o executável já assinado;
2. `dist/JARVIS-Setup-<versão>.exe`, depois do passo 8.

```
signtool sign /fd SHA256 /tr <timestamp-server> /td SHA256 <arquivo>
```

A reputação do SmartScreen se acumula por assinante ao longo de downloads, e
não é instantânea nem com certificado.

## Ícone

O projeto **ainda não tem um ícone próprio**. O build procura
`packaging/windows/assets/jarvis.ico` e segue sem ele se não existir — o
executável usa o ícone padrão do Windows.

Isto está registrado como limitação de propósito: gerar arte automaticamente
não é decisão de um script de build. Para resolver, coloque um `.ico`
(recomendado: 16/32/48/256 px no mesmo arquivo) naquele caminho e rode o
build de novo. Nada mais precisa mudar.

## Licença

O repositório ainda não define uma licença, e por isso o installer não mostra
página de licença. Escolher uma é decisão do autor do projeto, não do
packaging.

## Release

```bash
git pull
python -m unittest discover -s tests
python scripts/build_windows.py
```

Teste o artefato de verdade antes de publicar (ver "Teste em máquina limpa").
Depois:

```bash
git tag v1.6.0
```

```bash
git push origin v1.6.0
```

A tag dispara `.github/workflows/windows-release.yml`, que roda a suíte,
gera os artefatos e cria a release como **rascunho** — a publicação continua
sendo um ato humano.

## Teste em máquina limpa

O teste que importa, e o único que prova o objetivo: uma máquina **sem
Python, sem pip e sem o repositório**.

1. Windows Sandbox ou VM limpa.
2. Copie o `JARVIS-Setup-<versão>.exe`.
3. Instale.
4. Abra pelo Menu Iniciar.
5. Confirme: a janela abre, sem console preto, e o primeiro uso funciona.

Se em qualquer ponto o Python for necessário, o build falhou no seu objetivo.

## Problemas comuns

**A janela abre vazia / "Falha ao carregar a interface".** O QML não entrou
no bundle. Confira `datas` no `jarvis.spec` e rode o build de novo — o passo
6 deveria ter pego isso.

**`ModuleNotFoundError` só no executável.** Import dinâmico que a análise
estática do PyInstaller não vê. Acrescente o módulo a `hidden_imports` no
spec.

**STT não funciona no build.** `faster_whisper`, `ctranslate2` e `av` são
opcionais: se não estavam instalados no ambiente de build, o spec os pula e
avisa. O app abre normalmente sem voz — por desenho, não por acidente.

Quando eles ESTÃO no ambiente, o passo 6 exige que tenham entrado
**completos** e falha o build caso contrário. Isso existe por causa de um bug
real: `collect_submodules` trazia o código de `faster_whisper` mas não o
`assets/silero_vad_v6.onnx` que ele carrega por caminho — o import passava e a
primeira transcrição quebrava, sem nenhum sinal em tempo de build.

## Validação real executada (v1.6.0)

O que abaixo foi verificado executando de verdade, não por inspeção:

| Item | Resultado |
|---|---|
| `JARVIS-Setup-1.6.0.exe` compilado | sim, 178 MB, Inno Setup 6.7.3 |
| Instalação sem admin | sim (`IsInRole(Administrator) = False`) |
| Caminho com espaços | `...\Programs\Test User Space\JARVIS` |
| Atalho no Menu Iniciar | criado e removido no uninstall |
| Atalho no Desktop (opcional) | criado com `/TASKS=desktopicon` |
| Entrada em Installed Apps | criada em `HKCU`, removida no uninstall |
| App abre | janela real, sem console |
| Encerramento limpo | fecha pela janela, exit 0 |
| Banco em user-data | sim; nenhum `.db` na pasta de instalação |
| Update por cima | banco byte-a-byte idêntico, login/prefs/chats preservados |
| Uninstall | binários, atalhos e registro removidos; dados preservados |
| Reinstalar após uninstall | dados continuam acessíveis |
| Segredos no installer | nenhum (varredura no binário de 178 MB) |

**O antivírus reclama.** Esperado num binário não assinado. Ver "Assinatura
de código". O build não usa UPX nem obfuscação justamente para reduzir isso.
