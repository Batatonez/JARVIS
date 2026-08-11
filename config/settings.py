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
    core_version: str = "0.7.0"

    project_root: Path = PROJECT_ROOT
    memory_dir: Path = PROJECT_ROOT / "memory"
    profile_path: Path = PROJECT_ROOT / "memory" / "profile.md"
    preferences_path: Path = PROJECT_ROOT / "memory" / "preferences.md"

    log_dir: Path = PROJECT_ROOT / "logs"
    log_path: Path = PROJECT_ROOT / "logs" / "jarvis.log"

    dev_mode: bool = os.environ.get("JARVIS_DEV") == "1"

    # Limite de mensagens mantidas no histórico runtime da conversa
    # (app/conversation.py) — só em RAM, nunca persistido.
    max_conversation_messages: int = int(os.environ.get("JARVIS_MAX_CONVERSATION_MESSAGES", "200"))

    # Provider de IA (Claude Agent SDK, via services/claude_agent_provider.py).
    # Nenhum valor sensível tem default hardcoded — a API key só existe se vier
    # do ambiente, e nós só checamos a presença dela: quem lê o valor de fato
    # ao conectar é o próprio Agent SDK (ver services/claude_agent_provider.py).
    anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY") or None
    # Aceita os aliases documentados pelo Agent SDK ("sonnet"/"opus"/"haiku")
    # ou um nome de modelo completo — nunca uma versão hardcoded por suposição.
    agent_model: str = os.environ.get("JARVIS_AGENT_MODEL", "sonnet")

    # --- Voz (v0.7 — ver services/stt_service.py, services/tts_service.py) ---
    # Kill-switch: mesmo com microfone e modelo presentes, permite desligar
    # a tentativa de construir um STT real (ex.: política corporativa).
    voice_input_enabled: bool = os.environ.get("JARVIS_VOICE_INPUT", "1") != "0"
    # Fala automática da resposta da IA — desligada por padrão de propósito
    # (ver docs/architecture.md, seção Voice Foundation). É o valor inicial;
    # o HUD liga/desliga em runtime via JarvisApplication.set_voice_output_enabled().
    voice_output_enabled: bool = os.environ.get("JARVIS_VOICE_OUTPUT", "0") == "1"
    # Caminho do modelo Vosk offline (nunca baixado automaticamente pelo
    # JARVIS — ver frontend/README.md para o comando de download manual).
    # Não versionado: ver .gitignore ("voice_models/").
    stt_model_path: Path = Path(
        os.environ.get("JARVIS_STT_MODEL_PATH", str(PROJECT_ROOT / "voice_models" / "vosk-model-small-pt"))
    )
    # Substring (case-insensitive) do nome de uma voz SAPI5 instalada no
    # Windows. Vazio/None = escolhe automaticamente (preferência por uma voz
    # pt-BR se existir, senão a voz padrão do sistema).
    tts_voice: str | None = os.environ.get("JARVIS_TTS_VOICE") or None

    def has_anthropic_api_key(self) -> bool:
        return bool(self.anthropic_api_key)


settings = Settings()
