"""Configuração de logging do JARVIS usando apenas a biblioteca padrão.

Console: só mostra avisos/erros, para não poluir o uso normal do terminal.
Arquivo: registra tudo (INFO+) em `logs/jarvis.log`, útil para desenvolvimento.
Nenhuma informação sensível (memória do usuário, credenciais) deve ser logada.
"""

import logging

from config.settings import settings


def configure_logging(
    console_level: int = logging.WARNING,
    file_level: int = logging.INFO,
) -> logging.Logger:
    root_logger = logging.getLogger()

    if root_logger.handlers:
        return root_logger

    root_logger.setLevel(logging.DEBUG)

    settings.log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(settings.log_path, encoding="utf-8")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return root_logger
