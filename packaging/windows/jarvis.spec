# -*- mode: python ; coding: utf-8 -*-
"""Spec do PyInstaller para o JARVIS no Windows.

--------------------------------------------------------------------------
Por que ONEDIR e não ONEFILE
--------------------------------------------------------------------------
`--onefile` produz um único `.exe` — atraente até você olhar o que ele custa
num app deste tamanho:

- **Startup**: onefile extrai TODO o bundle (Qt inteiro, Python, DLLs
  nativas) para um diretório temporário a cada execução. Para centenas de MB
  isso são segundos de espera antes de qualquer pixel aparecer, toda vez.
- **Antivírus**: "executável que se descompacta em `%TEMP%` e roda de lá" é
  literalmente o comportamento que heurística de antivírus procura. Onedir
  reduz falso positivo.
- **Bibliotecas nativas**: Qt e CTranslate2 carregam DLLs por caminho em
  runtime. Onedir mantém o layout previsível no disco; onefile move tudo
  para um temporário diferente a cada execução.

E o argumento a favor do onefile — "o usuário só lida com um arquivo" — não
se aplica aqui: quem entrega um arquivo só ao usuário é o INSTALADOR. Ele
esconde a pasta de qualquer forma, então o onedir não custa nada em
experiência e paga em confiabilidade.

--------------------------------------------------------------------------
Dependências opcionais
--------------------------------------------------------------------------
STT (`faster_whisper`, `ctranslate2`, `av`) e Vosk são opcionais em runtime:
o JARVIS já cai para um provider de voz indisponível sem quebrar. Aqui eles
são coletados SE estiverem instalados no ambiente de build, e simplesmente
pulados se não estiverem — um build sem STT continua sendo um build válido,
só sem voz. Falhar o build inteiro por causa de um extra opcional
transformaria uma degradação em bloqueio.
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

# `SPECPATH` é injetado pelo PyInstaller — `__file__` não existe num .spec.
PROJECT_ROOT = Path(SPECPATH).resolve().parent.parent
PACKAGING_DIR = Path(SPECPATH).resolve()

BUILD_MODE = os.environ.get("JARVIS_BUILD_MODE", "release").strip().lower()
IS_RELEASE = BUILD_MODE != "dev"

# Ícone e metadados do Windows são OPCIONAIS: se o arquivo não existir, o
# build segue sem ele. O projeto ainda não tem um ícone próprio, e inventar
# arte automaticamente não é uma decisão que este script deva tomar (ver
# docs/BUILD_WINDOWS.md, seção Ícone).
_icon = PACKAGING_DIR / "assets" / "jarvis.ico"
ICON_PATH = _icon if _icon.is_file() else None

# Gerado por `scripts/build_windows.py` a partir da versão oficial do projeto
# — nunca escrito à mão, para não existir uma segunda fonte de versão.
_version_file = PACKAGING_DIR / "version_info.txt"
VERSION_FILE = _version_file if _version_file.is_file() else None


def _optional(package: str):
    """`(submódulos, DLLs, dados)` de um pacote opcional, ou vazio se ele não
    estiver instalado. Nunca levanta: ausência de extra não pode derrubar o
    build.

    Os DADOS são coletados junto com o código porque, sem eles, o pacote
    entra pela metade e falha só em runtime. Caso concreto encontrado ao
    validar o installer: `faster_whisper` embarca
    `assets/silero_vad_v6.onnx` (1,2 MB), o modelo de detecção de voz que
    ele carrega por caminho. Com só os submódulos, o import funciona e a
    primeira transcrição quebra — o pior tipo de falha de packaging, porque
    passa por toda verificação que só olha import."""
    try:
        __import__(package)
    except Exception:
        print(f"[jarvis.spec] Extra opcional ausente, seguindo sem ele: {package}")
        return [], [], []
    try:
        return (
            collect_submodules(package),
            collect_dynamic_libs(package),
            collect_data_files(package),
        )
    except Exception as exc:  # pragma: no cover - depende do ambiente de build
        print(f"[jarvis.spec] Falha ao coletar {package} ({exc}); seguindo sem ele.")
        return [], [], []


hidden_imports: list[str] = [
    # Integração Qt+asyncio: importada por string dentro do PySide6, então a
    # análise estática do PyInstaller não a encontra sozinha.
    "PySide6.QtAsyncio",
    # DPAPI (token de sessão e segredo TOTP). Import protegido por try/except
    # em `services/session_store.py`, o que também o esconde da análise.
    "win32crypt",
    "win32timezone",
]
binaries: list[tuple] = []
optional_datas: list[tuple] = []

for package in ("faster_whisper", "ctranslate2", "av", "vosk", "sounddevice", "pyttsx3"):
    modules, libs, package_datas = _optional(package)
    hidden_imports += modules
    binaries += libs
    optional_datas += package_datas

# `pyttsx3` escolhe o driver por nome em runtime (`pyttsx3.drivers.sapi5`) —
# invisível para a análise estática.
hidden_imports.append("pyttsx3.drivers.sapi5")

datas = [
    # QML: o app inteiro é desenhado aqui. Sem isto o executável abre e não
    # mostra nada — o modo de falha mais comum ao empacotar Qt Quick.
    (str(PROJECT_ROOT / "frontend" / "qml"), "frontend/qml"),
]

# `.env.example` acompanha o app como REFERÊNCIA de quais variáveis existem.
# O `.env` REAL nunca é empacotado (ver `scripts/build_windows.py`, que audita
# o artefato final).
env_example = PROJECT_ROOT / ".env.example"
if env_example.is_file():
    datas.append((str(env_example), "."))

datas += optional_datas

a = Analysis(
    [str(PROJECT_ROOT / "frontend" / "__main__.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Nunca empacotar a suíte de testes nem ferramentas de
        # desenvolvimento: aumentam o artefato e podem carregar fixtures.
        "tests",
        "pytest",
        "unittest",
        "tkinter",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JARVIS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX é um packer — aumenta muito a chance de falso positivo
    # de antivírus, e o ganho de tamanho não compensa num installer.
    # `console=False` no release: um app de janela não pode abrir um console
    # preto junto. Em build de desenvolvimento o console volta, porque é onde
    # o traceback aparece.
    console=not IS_RELEASE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_PATH) if ICON_PATH else None,
    version=str(VERSION_FILE) if VERSION_FILE else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="JARVIS",
)
