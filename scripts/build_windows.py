#!/usr/bin/env python
"""Build de distribuição do JARVIS para Windows — um comando só.

    python scripts/build_windows.py                 # standalone + installer
    python scripts/build_windows.py --skip-installer
    python scripts/build_windows.py --dev           # com console, para depurar

Etapas, nesta ordem:

    1. valida a versão (fonte única: `config.settings.Settings.core_version`)
    2. confere as dependências de build
    3. limpa `build/` e `dist/` — com validação de caminho (ver `_safe_rmtree`)
    4. gera os metadados de versão do Windows
    5. gera o standalone (PyInstaller, onedir)
    6. verifica que os arquivos essenciais entraram
    7. audita o artefato em busca de segredo
    8. gera o installer (Inno Setup), se disponível
    9. calcula os SHA-256
    10. imprime o relatório

**Nada aqui publica nada.** O script produz arquivos em `dist/`; enviar para
o GitHub Releases é uma ação separada e deliberada.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"
PACKAGING_DIR = PROJECT_ROOT / "packaging" / "windows"
SPEC_PATH = PACKAGING_DIR / "jarvis.spec"
ISS_PATH = PACKAGING_DIR / "JARVIS.iss"
STANDALONE_DIR = DIST_DIR / "JARVIS"

# Arquivos sem os quais o executável abre e não funciona. Verificados no
# artefato REAL, não presumidos a partir do spec: o modo de falha clássico ao
# empacotar Qt Quick é o QML não entrar e o app abrir uma janela vazia.
REQUIRED_ARTIFACTS = (
    "JARVIS.exe",
    "_internal/frontend/qml/Main.qml",
    "_internal/frontend/qml/components/AccountSettingsOverlay.qml",
    "_internal/frontend/qml/theme/qmldir",
)

# Reconhecimento de fala. É OPCIONAL: um build sem STT abre e funciona, só
# sem voz. Mas quando o pacote está presente no ambiente de build, ele
# precisa entrar COMPLETO — código e dados.
#
# Bug real encontrado ao validar o installer: `collect_submodules` trazia os
# módulos de `faster_whisper` mas não o `assets/silero_vad_v6.onnx` que ele
# carrega por caminho. O import passava e a primeira transcrição quebrava —
# invisível para qualquer verificação que só teste import.
STT_ARTIFACTS = {
    "faster_whisper": ("_internal/faster_whisper/assets/silero_vad_v6.onnx",),
    "ctranslate2": ("_internal/ctranslate2/ctranslate2.dll",),
    "vosk": ("_internal/vosk",),
}

# Padrões de credencial procurados no artefato final. `sk-`/`nvapi-`/`gsk_`
# são prefixos reais de chave dos providers usados pelo projeto.
SECRET_PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(rb"nvapi-[A-Za-z0-9_\-]{20,}"),
    re.compile(rb"gsk_[A-Za-z0-9_\-]{20,}"),
    re.compile(rb"AIza[A-Za-z0-9_\-]{30,}"),
)
# Arquivos que jamais podem aparecer dentro do artefato.
FORBIDDEN_NAMES = (".env", "jarvis.db", "session.local", "ruvector.db")


class BuildError(RuntimeError):
    """Falha de build já explicada — o `main` imprime e sai com código 1."""


# ----------------------------------------------------------------------
# 1-2. Versão e dependências
# ----------------------------------------------------------------------


def read_version() -> str:
    """Versão oficial do projeto, de UMA fonte só.

    `Settings.core_version` é a fonte porque é ela que o app mostra em
    runtime. `pyproject.toml` é conferido contra ela: divergência entre os
    dois é erro de build, não algo a resolver escolhendo um dos dois em
    silêncio."""
    from config.settings import Settings

    version = Settings.core_version
    # Duas OU três partes: `1.8` e `1.8.0` são ambas válidas em PEP 440, e o
    # projeto usa a forma curta quando a versão não tem patch — acrescentar
    # um `.0` só por costume mudaria o nome do instalador e o que o usuário
    # vê. Quem precisa de quatro números é só o recurso de versão do Windows
    # (ver `write_version_info`), e essa normalização acontece lá, na
    # fronteira, sem contaminar a versão oficial.
    if not re.fullmatch(r"\d+\.\d+(\.\d+)?", version):
        raise BuildError(f"Versão inesperada em Settings.core_version: {version!r}")

    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    if not match:
        raise BuildError("Não foi possível ler a versão de pyproject.toml.")
    if match.group(1) != version:
        raise BuildError(
            f"Versão divergente: Settings.core_version={version} mas "
            f"pyproject.toml={match.group(1)}. Alinhe as duas antes de gerar a release."
        )
    return version


def check_build_dependencies() -> None:
    missing = []
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        missing.append("pyinstaller")
    try:
        import PySide6  # noqa: F401
    except ImportError:
        missing.append("PySide6")
    if missing:
        raise BuildError(
            "Dependência de build ausente: "
            + ", ".join(missing)
            + '\nInstale com:  pip install -r requirements-build.txt'
        )


def find_inno_setup() -> Path | None:
    """`ISCC.exe` do Inno Setup, ou `None`. A ausência dele NÃO é erro: o
    standalone continua sendo um artefato válido, e o script avisa que o
    installer foi pulado."""
    from_env = os.environ.get("INNO_SETUP_ISCC", "").strip()
    if from_env and Path(from_env).is_file():
        return Path(from_env)
    on_path = shutil.which("iscc")
    if on_path:
        return Path(on_path)

    candidates = [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    # Instalação POR USUÁRIO. O `winget install JRSoftware.InnoSetup` usa este
    # caminho por padrão — e como ele não põe o ISCC no PATH, procurar só em
    # `Program Files` faz o build reportar "Inno Setup não encontrado" numa
    # máquina onde ele acabou de ser instalado com sucesso.
    local_programs = os.environ.get("LOCALAPPDATA", "").strip()
    if local_programs:
        candidates.append(Path(local_programs) / "Programs" / "Inno Setup 6" / "ISCC.exe")

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


# ----------------------------------------------------------------------
# 3. Limpeza
# ----------------------------------------------------------------------


def _safe_rmtree(target: Path) -> None:
    """Remove um diretório de build, recusando qualquer coisa que não seja
    comprovadamente um deles.

    A validação não é paranoia decorativa: um caminho montado errado
    (variável vazia, raiz mal resolvida) transformaria uma limpeza de build
    num `rm -rf` na pasta do usuário. As três condições — precisa existir,
    precisa estar DENTRO da raiz do projeto, e precisa se chamar `build` ou
    `dist` — tornam isso impossível por construção."""
    target = target.resolve()
    if not target.exists():
        return
    if target == PROJECT_ROOT or PROJECT_ROOT not in target.parents:
        raise BuildError(f"Recusando limpar um caminho fora do projeto: {target}")
    if target.name not in ("build", "dist"):
        raise BuildError(f"Recusando limpar um diretório que não é de build: {target}")
    try:
        shutil.rmtree(target)
    except PermissionError as exc:
        # Causa quase sempre a mesma: um JARVIS.exe do build anterior ainda
        # está aberto e segura uma DLL. O traceback cru de `shutil` aponta
        # para um `qgif.dll` qualquer e não diz o que fazer.
        raise BuildError(
            f"Não foi possível limpar {target}: {exc.filename or target}\n"
            "Provavelmente há uma instância do JARVIS aberta segurando um arquivo do build.\n"
            "Feche o JARVIS (ou: taskkill /IM JARVIS.exe /F) e rode o build de novo."
        ) from exc
    print(f"  limpo: {target}")


def clean() -> None:
    print("[3/10] Limpando builds anteriores...")
    _safe_rmtree(BUILD_DIR)
    _safe_rmtree(DIST_DIR)


# ----------------------------------------------------------------------
# 4. Metadados do Windows
# ----------------------------------------------------------------------


def write_version_info(version: str) -> Path:
    """Gera o recurso de versão do Windows (o que aparece em Propriedades do
    arquivo). Derivado da versão oficial — nunca digitado à mão.

    `CompanyName` recebe o nome do projeto, não uma empresa: não existe
    pessoa jurídica por trás disto, e inventar uma seria declarar algo falso
    num campo que ferramentas de segurança leem."""
    # O recurso VERSIONINFO do Windows exige QUATRO inteiros. Uma versão de
    # duas partes (`1.8`) é completada com zeros SÓ AQUI: é uma exigência do
    # formato do sistema operacional, não uma renomeação da versão do
    # projeto — o instalador continua se chamando `JARVIS-Setup-1.8.exe`.
    parts = [int(part) for part in version.split(".")]
    while len(parts) < 3:
        parts.append(0)
    major, minor, patch = parts[:3]
    content = f"""# Gerado por scripts/build_windows.py — NÃO editar à mão.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'JARVIS'),
        StringStruct('FileDescription', 'JARVIS Desktop Assistant'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'JARVIS'),
        StringStruct('OriginalFilename', 'JARVIS.exe'),
        StringStruct('ProductName', 'JARVIS'),
        StringStruct('ProductVersion', '{version}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    PACKAGING_DIR.mkdir(parents=True, exist_ok=True)
    path = PACKAGING_DIR / "version_info.txt"
    path.write_text(content, encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# 5-7. Standalone, verificação e auditoria
# ----------------------------------------------------------------------


def build_standalone(dev: bool) -> None:
    print("[5/10] Gerando o standalone (PyInstaller, onedir)...")
    environment = dict(os.environ, JARVIS_BUILD_MODE="dev" if dev else "release")
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        str(SPEC_PATH),
    ]
    result = subprocess.run(command, cwd=str(PROJECT_ROOT), env=environment)
    if result.returncode != 0:
        raise BuildError("PyInstaller falhou — veja a saída acima.")


def verify_artifacts() -> None:
    print("[6/10] Verificando arquivos essenciais no artefato...")
    missing = [name for name in REQUIRED_ARTIFACTS if not (STANDALONE_DIR / name).exists()]
    if missing:
        raise BuildError(
            "Arquivos essenciais ausentes do build:\n  "
            + "\n  ".join(missing)
            + "\nO executável abriria sem interface. Confira `datas` em packaging/windows/jarvis.spec."
        )
    for name in REQUIRED_ARTIFACTS:
        print(f"  ok: {name}")

    verify_stt()


def verify_stt() -> None:
    """Confere que cada componente de STT presente no ambiente de build
    entrou COMPLETO no artefato.

    Ausência do pacote no ambiente é apenas um aviso — o build segue e o app
    abre sem voz. Presença no ambiente com ausência no artefato é ERRO: é um
    componente pela metade, que só falha na máquina do usuário."""
    incomplete: list[str] = []
    for package, expected in STT_ARTIFACTS.items():
        try:
            __import__(package)
        except Exception:
            print(f"  STT: {package} não está no ambiente de build — build seguirá sem voz por ele")
            continue
        for relative in expected:
            if not (STANDALONE_DIR / relative).exists():
                incomplete.append(f"{package}: falta {relative}")
            else:
                print(f"  STT ok: {relative}")

    if incomplete:
        raise BuildError(
            "Componente de STT entrou incompleto no artefato:\n  "
            + "\n  ".join(incomplete)
            + "\nO app importaria o pacote e falharia na primeira transcrição."
        )


def audit_artifacts() -> None:
    """Procura credencial e arquivo proibido dentro do que será distribuído.

    Roda sobre o artefato FINAL e não sobre a lista de `datas`: só o
    resultado real prova o que entrou. Um `.env` puxado por um hook
    transitivo não apareceria em nenhuma configuração nossa."""
    print("[7/10] Auditando o artefato (segredos e arquivos proibidos)...")
    problems: list[str] = []

    for path in STANDALONE_DIR.rglob("*"):
        if not path.is_file():
            continue
        if path.name in FORBIDDEN_NAMES:
            problems.append(f"arquivo proibido no bundle: {path.relative_to(STANDALONE_DIR)}")

    # Varredura de conteúdo só nos arquivos de texto/config: procurar padrões
    # em centenas de MB de DLL geraria falso positivo e levaria minutos.
    for path in STANDALONE_DIR.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in (".txt", ".env", ".json", ".cfg", ".ini", ".qml", ".py"):
            continue
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(blob):
                problems.append(f"possível credencial em {path.relative_to(STANDALONE_DIR)}")
                break

    if problems:
        raise BuildError("Auditoria do artefato falhou:\n  " + "\n  ".join(problems))
    print("  nenhum segredo nem arquivo proibido encontrado")


# ----------------------------------------------------------------------
# 8-9. Installer e checksums
# ----------------------------------------------------------------------


def build_installer(version: str) -> Path | None:
    iscc = find_inno_setup()
    if iscc is None:
        print("[8/10] Inno Setup não encontrado — installer PULADO.")
        print("       Instale o Inno Setup 6 (https://jrsoftware.org/isdl.php)")
        print("       ou aponte INNO_SETUP_ISCC para o ISCC.exe.")
        return None

    print(f"[8/10] Gerando o installer com {iscc}...")
    command = [
        str(iscc),
        f"/DJarvisVersion={version}",
        f"/DSourceDir={STANDALONE_DIR}",
        f"/DOutputDir={DIST_DIR}",
        str(ISS_PATH),
    ]
    result = subprocess.run(command, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        raise BuildError("Inno Setup falhou — veja a saída acima.")

    installer = DIST_DIR / f"JARVIS-Setup-{version}.exe"
    return installer if installer.is_file() else None


def generate_checksums(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    print("[9/10] Calculando SHA-256...")
    lines = []
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
        print(f"  {digest}  {path.name}")
    checksums = DIST_DIR / "SHA256SUMS.txt"
    checksums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksums


def _directory_size_mb(path: Path) -> float:
    total = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    return total / (1024 * 1024)


# ----------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Build de distribuição do JARVIS (Windows).")
    parser.add_argument("--skip-installer", action="store_true", help="só o standalone")
    parser.add_argument("--dev", action="store_true", help="build de desenvolvimento (com console)")
    args = parser.parse_args()

    if os.name != "nt":
        print("AVISO: este build produz um artefato Windows e foi feito para rodar no Windows.")

    started = time.monotonic()
    try:
        print("[1/10] Lendo a versão oficial...")
        version = read_version()
        print(f"  versão: {version}")

        print("[2/10] Conferindo dependências de build...")
        check_build_dependencies()

        clean()

        print("[4/10] Gerando metadados de versão do Windows...")
        write_version_info(version)

        build_standalone(args.dev)
        verify_artifacts()
        audit_artifacts()

        artifacts = []
        installer = None if args.skip_installer else build_installer(version)
        if args.skip_installer:
            print("[8/10] Installer pulado a pedido (--skip-installer).")
        if installer is not None:
            artifacts.append(installer)

        checksums = generate_checksums(artifacts)

        print("[10/10] Pronto.\n")
        print("=" * 62)
        print(f"Versão:      {version}")
        print(f"Standalone:  {STANDALONE_DIR}  ({_directory_size_mb(STANDALONE_DIR):.0f} MB)")
        if installer is not None:
            print(f"Installer:   {installer}  ({installer.stat().st_size / (1024 * 1024):.0f} MB)")
        else:
            print("Installer:   não gerado")
        if checksums is not None:
            print(f"Checksums:   {checksums}")
        print(f"Tempo:       {time.monotonic() - started:.0f}s")
        print("=" * 62)
        return 0
    except BuildError as exc:
        print(f"\nERRO DE BUILD: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
