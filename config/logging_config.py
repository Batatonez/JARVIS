"""Configuração de logging do JARVIS usando apenas a biblioteca padrão.

Console: só mostra avisos/erros, para não poluir o uso normal do terminal.
Arquivo: registra tudo (INFO+) em `<dados do usuário>/logs/jarvis.log`.

**Rotação** (packaging): um aplicativo instalado roda por meses sem ninguém
olhar a pasta de logs. Sem rotação, `jarvis.log` cresceria indefinidamente
até virar um problema de disco silencioso. `RotatingFileHandler` com teto e
número fixo de backups resolve isso sem depender de manutenção manual.

**Sanitização**: o JARVIS nunca loga senha, token de sessão, chave de API,
header `Authorization`, segredo TOTP, código de recuperação ou raciocínio
interno do modelo — isso é garantido na origem, por cada módulo que lida com
esses valores, e verificado por varredura em `tests/test_security_v1.py`. O
filtro abaixo é uma segunda camada: se um valor com forma reconhecível de
credencial escapar para uma mensagem de log, ele é mascarado antes de tocar o
disco. Segunda camada, não a primeira — não substitui a disciplina de não
passar segredo para o logger.
"""

import logging
import re
from logging.handlers import RotatingFileHandler

from config.settings import settings

# 2 MB por arquivo, 3 backups: cobre folgadamente uma investigação de
# problema recente sem virar um passivo de disco.
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 3

# Formas reconhecíveis de credencial. Deliberadamente específicas: um padrão
# amplo demais mascararia texto legítimo e tornaria o log inútil justamente
# quando ele é necessário.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,})"),          # OpenAI/OpenRouter
    re.compile(r"\b(nvapi-[A-Za-z0-9_\-]{16,})"),        # NVIDIA
    re.compile(r"\b(gsk_[A-Za-z0-9_\-]{16,})"),          # Groq
    re.compile(r"\b(AIza[A-Za-z0-9_\-]{16,})"),          # Google
    re.compile(r"(?i)\b(bearer\s+[A-Za-z0-9._\-]{16,})"),
    re.compile(r"(?i)\b(authorization\s*[:=]\s*\S+)"),
    re.compile(r"(?i)\b(api[_-]?key\s*[:=]\s*\S+)"),
    re.compile(r"(?i)\b(password\s*[:=]\s*\S+)"),
)

_REDACTED = "[REDACTED]"


class SecretRedactingFilter(logging.Filter):
    """Mascara credencial reconhecível na mensagem final do registro.

    Age sobre `record.getMessage()` (a mensagem JÁ interpolada) porque um
    segredo pode chegar tanto no template quanto nos argumentos — checar só
    `record.msg` deixaria passar `logger.info("key=%s", chave)`."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = message
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(_REDACTED, redacted)
        if redacted != message:
            # Substitui a mensagem e descarta os argumentos: mantê-los faria
            # o formatter reinterpolar o valor original de volta.
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(
    console_level: int = logging.WARNING,
    file_level: int = logging.INFO,
) -> logging.Logger:
    root_logger = logging.getLogger()

    if root_logger.handlers:
        return root_logger

    root_logger.setLevel(logging.DEBUG)

    settings.log_dir.mkdir(parents=True, exist_ok=True)

    redactor = SecretRedactingFilter()

    file_handler = RotatingFileHandler(
        settings.log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    file_handler.addFilter(redactor)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    console_handler.addFilter(redactor)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return root_logger
