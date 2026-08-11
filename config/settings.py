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
    core_version: str = "0.9.0"

    project_root: Path = PROJECT_ROOT
    # Memória legacy (pré-contas, v0.1-v0.8) — nunca apagada, só lida uma vez
    # por `services/memory_migration.py` para copiar para a primeira conta
    # criada neste ambiente. Ver docs/architecture.md, seção Contas.
    memory_dir: Path = PROJECT_ROOT / "memory"
    profile_path: Path = PROJECT_ROOT / "memory" / "profile.md"
    preferences_path: Path = PROJECT_ROOT / "memory" / "preferences.md"

    log_dir: Path = PROJECT_ROOT / "logs"
    log_path: Path = PROJECT_ROOT / "logs" / "jarvis.log"

    # --- Dados locais (v0.9 — contas, chats, modelos de voz) ---
    # Tudo aqui é local e pessoal: nunca vai para o Git (ver .gitignore).
    data_dir: Path = PROJECT_ROOT / "data"
    db_path: Path = PROJECT_ROOT / "data" / "jarvis.db"
    users_dir: Path = PROJECT_ROOT / "data" / "users"
    # Token de sessão local (bearer opaco, nunca a senha) para continuar
    # logado entre execuções — ver services/session_store.py.
    session_token_path: Path = PROJECT_ROOT / "data" / "session.local"
    # Modelos de voz baixados pelo próprio JARVIS (nunca automaticamente —
    # ver services/vosk_model_manager.py). Movido de `voice_models/` (v0.7)
    # para dentro de `data/` — mesma regra de "nunca no Git" que o resto.
    stt_models_dir: Path = PROJECT_ROOT / "data" / "models" / "vosk"

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
    # JARVIS — ver services/vosk_model_manager.py e frontend/README.md,
    # seção Voz, para o fluxo de instalação pelo próprio app).
    # Não versionado: ver .gitignore ("data/").
    stt_model_path: Path = Path(
        os.environ.get("JARVIS_STT_MODEL_PATH", str(PROJECT_ROOT / "data" / "models" / "vosk" / "vosk-model-small-pt"))
    )
    # Substring (case-insensitive) do nome de uma voz SAPI5 instalada no
    # Windows. Vazio/None = escolhe automaticamente (preferência por uma voz
    # pt-BR se existir, senão a voz padrão do sistema).
    tts_voice: str | None = os.environ.get("JARVIS_TTS_VOICE") or None

    def has_anthropic_api_key(self) -> bool:
        return bool(self.anthropic_api_key)


settings = Settings()
