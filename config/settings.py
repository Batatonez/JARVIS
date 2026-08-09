"""Configuração central do JARVIS: nomes, versão e caminhos importantes.

Nenhum caminho aqui é absoluto/hardcoded para uma máquina específica — tudo é
derivado de `PROJECT_ROOT`, calculado a partir da localização deste arquivo,
para que o projeto funcione em qualquer computador ou diretório.
"""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    app_name: str = "JARVIS"
    core_version: str = "0.1.0"

    project_root: Path = PROJECT_ROOT
    memory_dir: Path = PROJECT_ROOT / "memory"
    profile_path: Path = PROJECT_ROOT / "memory" / "profile.md"
    preferences_path: Path = PROJECT_ROOT / "memory" / "preferences.md"

    log_dir: Path = PROJECT_ROOT / "logs"
    log_path: Path = PROJECT_ROOT / "logs" / "jarvis.log"

    dev_mode: bool = os.environ.get("JARVIS_DEV") == "1"

    # Provider de IA (Claude, via services/claude_provider.py). Nenhum valor
    # sensível tem default hardcoded — a API key só existe se vier do ambiente.
    anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY") or None
    anthropic_model: str = os.environ.get("JARVIS_ANTHROPIC_MODEL", "claude-sonnet-5")
    anthropic_timeout: float = float(os.environ.get("JARVIS_ANTHROPIC_TIMEOUT", "30"))
    anthropic_max_tokens: int = int(os.environ.get("JARVIS_ANTHROPIC_MAX_TOKENS", "1024"))

    def has_anthropic_api_key(self) -> bool:
        return bool(self.anthropic_api_key)


settings = Settings()
