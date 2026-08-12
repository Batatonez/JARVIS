"""Carregamento automático do `.env` do projeto.

Antes da v1.1, o `.env` na raiz não era lido por ninguém: `python main.py`
subia com `OPENROUTER_API_KEY`/`JARVIS_SMTP_*` valendo `None`, e só
funcionava quem exportasse as variáveis à mão no PowerShell antes. Isso
está corrigido aqui.

**Onde isto é chamado importa**: no topo de `config/settings.py`, antes da
definição do dataclass. Os defaults de `Settings` são `os.environ.get(...)`
avaliados no momento em que a classe é criada — carregar o `.env` depois
disso não teria efeito nenhum. Colocando a chamada lá, nenhum ponto de
entrada (`main.py`, `python -m frontend`, teste, script) consegue esquecer.

**Precedência** (requisito da v1.1):

    variável já definida no processo/sistema  >  .env  >  default do código

Ou seja, o `.env` nunca sobrescreve algo que o usuário definiu
explicitamente no ambiente — é `override=False`, o padrão do `python-dotenv`.

O arquivo em si **nunca** vai para o Git (`.gitignore`), nunca é logado, e
nunca é lido pelos testes automatizados (que usam ambiente temporário).
"""

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Variáveis que a suíte de testes nunca pode herdar do ambiente real —
# credenciais e configuração que mudaria o resultado dos testes.
_SENSITIVE_ENV_VARS = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "NVIDIA_API_KEY",
    "OLLAMA_API_KEY",
    "JARVIS_SMTP_HOST",
    "JARVIS_SMTP_PORT",
    "JARVIS_SMTP_USERNAME",
    "JARVIS_SMTP_PASSWORD",
    "JARVIS_SMTP_USE_TLS",
    "JARVIS_EMAIL_FROM",
    "JARVIS_FREE_ONLY",
    "JARVIS_PROVIDER_MAX_TOKENS",
    "JARVIS_PROVIDER_TIMEOUT_S",
    "JARVIS_MAX_CONVERSATION_MESSAGES",
    "JARVIS_MAX_MEMORY_CONTEXT_CHARS",
    "JARVIS_AGENT_MODEL",
    "JARVIS_STT_MODEL_PATH",
    "JARVIS_VOICE_INPUT",
    "JARVIS_VOICE_OUTPUT",
    "JARVIS_TTS_VOICE",
    "JARVIS_DEV",
)


def _running_under_tests() -> bool:
    """Detecta execução de teste sem depender de ordem de import.

    `tests/__init__.py` também marca isso, mas tarde demais para servir
    sozinho: um módulo de teste que faz `from app.account_manager import ...`
    antes de `from tests.helpers import ...` já teria importado
    `config.settings` — cujos defaults são congelados na criação do
    dataclass — com as credenciais reais dentro.

    Checar o runner em `sys.modules` acontece cedo o bastante em qualquer
    ordem, porque `python -m unittest` / `pytest` já estão carregados antes
    de qualquer código de teste rodar."""
    if os.environ.get("JARVIS_DISABLE_DOTENV", "").strip() == "1":
        return True
    return "unittest" in sys.modules or "pytest" in sys.modules


def _scrub_sensitive_env() -> None:
    """Remove credencial real do processo de teste.

    Não basta deixar de ler o `.env`: o desenvolvedor pode ter exportado as
    variáveis no próprio shell. Sem isto, a suíte poderia gastar requisição
    de verdade ou enviar e-mail real — que é exatamente o que os testes
    prometem nunca fazer."""
    for name in _SENSITIVE_ENV_VARS:
        os.environ.pop(name, None)


def _load_with_dotenv(path: Path) -> bool:
    """Caminho normal: `python-dotenv` (declarado em requirements.txt).
    Maduro e já trata aspas, comentários, `export ` e valores vazios."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    # override=False é o que garante a precedência pedida (ambiente > .env).
    load_dotenv(dotenv_path=path, override=False, encoding="utf-8")
    return True


def _load_minimal(path: Path) -> None:
    """Fallback para um ambiente onde `python-dotenv` não foi instalado —
    o JARVIS não pode deixar de abrir por causa disso. Deliberadamente
    mínimo: `KEY=VALUE`, comentários com `#`, aspas simples/duplas ao redor
    do valor. Nada de expansão de variável, multilinha ou escapes; se você
    precisa disso, instale o `python-dotenv`."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        # Mesma precedência do caminho principal: nunca sobrescreve o que já
        # está no ambiente.
        os.environ.setdefault(key, value)


def load_project_env(project_root: Path, *, force: bool = False) -> bool:
    """Carrega `<project_root>/.env` se ele existir. Devolve `True` se algo
    foi carregado. Nunca levanta exceção: `.env` ausente, ilegível ou
    malformado não pode impedir o JARVIS de iniciar.

    Sob teste (`JARVIS_DISABLE_DOTENV=1`, ou runner detectado), o `.env` não
    é lido **e** as variáveis sensíveis são removidas do processo — a suíte
    nunca toca credencial real nem gasta requisição/e-mail de verdade.

    `force=True` existe só para os testes DO PRÓPRIO loader, que precisam
    exercitar o carregamento de verdade contra um `.env` temporário. Nunca
    é usado em produção (lá a detecção é justamente o que se quer)."""
    if not force and _running_under_tests():
        _scrub_sensitive_env()
        return False
    path = Path(project_root) / ".env"
    try:
        if not path.is_file():
            return False
        if not _load_with_dotenv(path):
            _load_minimal(path)
        # Nunca logamos nomes nem valores de variáveis — só o fato.
        logger.debug("Arquivo .env do projeto carregado.")
        return True
    except Exception:
        # Um .env quebrado degrada para "sem .env", nunca para um crash.
        logger.warning("Não foi possível carregar o arquivo .env do projeto; seguindo sem ele.")
        return False
