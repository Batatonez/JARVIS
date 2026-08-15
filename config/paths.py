"""Onde ficam os arquivos do JARVIS — recursos versus dados do usuário.

--------------------------------------------------------------------------
A distinção que este módulo existe para impor
--------------------------------------------------------------------------
Até aqui, tudo (banco, logs, memória, modelos de voz) morava sob a raiz do
repositório. Isso funciona quando o JARVIS roda a partir do código-fonte,
e quebra assim que ele vira um aplicativo instalado:

    diretório de INSTALAÇÃO   — executável, Qt, QML, migrações
                                somente leitura; num Windows normal, um
                                usuário padrão NÃO tem permissão de escrita
                                em `Program Files`

    diretório de DADOS        — contas, chats, memória, sessões, logs,
                                modelos baixados
                                precisa sobreviver a desinstalar, reinstalar
                                e atualizar o programa

Gravar o banco dentro da pasta de instalação produziria dois defeitos ao
mesmo tempo: falha de permissão em máquina de usuário padrão e perda de
todas as contas na primeira atualização.

--------------------------------------------------------------------------
Em desenvolvimento, NADA muda
--------------------------------------------------------------------------
Rodando do código-fonte (`python main.py`, `python -m frontend`, `pytest`),
os dois caminhos continuam sendo a raiz do repositório, exatamente como
antes. Isso é deliberado: nenhum banco existente é movido, nenhuma migração
de local é necessária, e o fluxo de desenvolvimento não muda de comportamento
por causa de packaging.

A separação só entra em vigor quando o processo está CONGELADO (empacotado
pelo PyInstaller) ou quando `JARVIS_USER_DATA` é definido explicitamente.

--------------------------------------------------------------------------
`JARVIS_USER_DATA`
--------------------------------------------------------------------------
Override explícito do diretório de dados. Serve a três casos reais: testes
que querem um diretório temporário, a versão portátil (que guarda os dados
ao lado do executável extraído) e diagnóstico. Nunca é lido de arquivo de
configuração — só do ambiente, onde é uma decisão consciente de quem
executou o processo.
"""

import os
import sys
from pathlib import Path

APP_DIR_NAME = "JARVIS"

# Raiz do repositório quando rodando do código-fonte. `parent.parent` porque
# este arquivo mora em `config/`.
_SOURCE_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    """`True` quando o processo é um executável empacotado (PyInstaller
    define `sys.frozen`). É a única pergunta que distingue "instalado" de
    "rodando do código-fonte" — nunca inferir isso do caminho, que muda
    conforme onde a pessoa clonou o repositório."""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Raiz dos arquivos SOMENTE LEITURA que acompanham o programa: QML,
    ícones, defaults.

    Empacotado, o PyInstaller expõe isso em `sys._MEIPASS` — que no modo
    onedir é a própria pasta de instalação e no modo onefile é o diretório
    temporário de extração. Usar `sys._MEIPASS` (em vez de derivar do
    caminho do executável) mantém os dois modos funcionando sem código
    condicional.

    Nunca escreva aqui: num Windows normal esta pasta é somente leitura para
    o usuário que roda o programa."""
    if is_frozen():
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            return Path(bundle)
        return Path(sys.executable).resolve().parent
    return _SOURCE_ROOT


def user_data_root() -> Path:
    """Raiz dos dados MUTÁVEIS do usuário — o que precisa sobreviver a
    atualização e desinstalação do programa.

    Precedência:

    1. `JARVIS_USER_DATA` (override explícito de quem executou o processo);
    2. `%LOCALAPPDATA%\\JARVIS` quando empacotado;
    3. a raiz do repositório, em desenvolvimento (comportamento histórico
       preservado — nenhum banco existente é movido).

    `LOCALAPPDATA` e não `APPDATA`: os dados do JARVIS são locais desta
    máquina (banco SQLite, modelos de voz de centenas de MB, logs, token de
    sessão protegido por DPAPI daquela máquina). `APPDATA` sincroniza em
    perfil móvel corporativo, o que replicaria gigabytes pela rede e levaria
    junto um token que só é decifrável na máquina de origem."""
    override = os.environ.get("JARVIS_USER_DATA", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    if is_frozen():
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return Path(base) / APP_DIR_NAME
        # Fora do Windows, ou com o ambiente incompleto: `~/.jarvis` é o
        # equivalente conservador. Nunca cair para o diretório de instalação.
        return Path.home() / f".{APP_DIR_NAME.lower()}"

    return _SOURCE_ROOT


def ensure_user_data_dirs() -> Path:
    """Cria a árvore de dados do usuário, se ainda não existir, e devolve a
    raiz. Idempotente.

    Não cria nada em desenvolvimento além do que o projeto já criava: as
    subpastas são as mesmas de sempre (`data/`, `logs/`, `memory/`)."""
    root = user_data_root()
    for relative in ("data", "logs", "memory", "data/models"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    return root


def describe() -> dict[str, str]:
    """Resumo dos caminhos resolvidos — usado pelo diagnóstico e pelos
    testes de packaging. Só caminhos, nunca conteúdo."""
    return {
        "frozen": str(is_frozen()),
        "resource_root": str(resource_root()),
        "user_data_root": str(user_data_root()),
    }
