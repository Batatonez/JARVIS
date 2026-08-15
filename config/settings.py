"""Configuração central do JARVIS: nomes, versão e caminhos importantes.

Nenhum caminho aqui é absoluto/hardcoded para uma máquina específica — tudo é
derivado de `PROJECT_ROOT`, calculado a partir da localização deste arquivo,
para que o projeto funcione em qualquer computador ou diretório.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from config.env_loader import load_project_env

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    """Lê um inteiro do ambiente tolerando ausência, valor vazio e lixo.

    Necessário desde a v1.1: com o `.env` sendo carregado automaticamente,
    copiar o `.env.example` (que traz `JARVIS_SMTP_PORT=` etc. em branco,
    de propósito) passou a colocar `""` no ambiente. Um `int("")` cru
    derrubava o JARVIS já no import de `config.settings` — ou seja, seguir
    a própria documentação quebrava o app."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_flag(name: str, default: bool) -> bool:
    """Flag booleana tolerante: vazio/ausente devolve o default. Aceita
    `1/0`, `true/false`, `yes/no`, `on/off` (case-insensitive)."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default

# v1.1 — carrega `<raiz>/.env` ANTES de qualquer default abaixo ser avaliado.
# Os defaults deste dataclass são `os.environ.get(...)` resolvidos no momento
# em que a classe é criada: carregar o `.env` depois disto não teria efeito.
# Precedência: variável já no ambiente > .env > default (ver env_loader).
load_project_env(PROJECT_ROOT)


@dataclass(frozen=True)
class Settings:
    app_name: str = "JARVIS"
    core_version: str = "1.5.0"

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
    # v1.3 — modelo do faster-whisper (engine principal de STT). Diretório
    # separado do Vosk: são formatos e fontes diferentes, e o usuário pode
    # ter os dois instalados (principal + fallback).
    whisper_models_dir: Path = PROJECT_ROOT / "data" / "models" / "whisper"

    dev_mode: bool = _env_flag("JARVIS_DEV", False)

    # Limite de mensagens mantidas no histórico runtime da conversa
    # (app/conversation.py) — só em RAM, nunca persistido.
    max_conversation_messages: int = _env_int("JARVIS_MAX_CONVERSATION_MESSAGES", 200)

    # Provider de IA (Claude Agent SDK, via services/claude_agent_provider.py).
    # Nenhum valor sensível tem default hardcoded — a API key só existe se vier
    # do ambiente, e nós só checamos a presença dela: quem lê o valor de fato
    # ao conectar é o próprio Agent SDK (ver services/claude_agent_provider.py).
    anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY") or None
    # Aceita os aliases documentados pelo Agent SDK ("sonnet"/"opus"/"haiku")
    # ou um nome de modelo completo — nunca uma versão hardcoded por suposição.
    agent_model: str = os.environ.get("JARVIS_AGENT_MODEL", "").strip() or "sonnet"

    # --- Provider Router (v1.0 — ver docs/providers.md) ---
    # Só checamos a presença da chave aqui; quem lê o valor de fato é
    # `services/providers/openrouter_provider.py`, direto do ambiente.
    openrouter_api_key: str | None = os.environ.get("OPENROUTER_API_KEY") or None
    # Kill-switch de custo. Ligado por padrão nesta fase (item 7 da v1.0):
    # o ProviderRouter nunca cai para um modelo pago silenciosamente — sem
    # rota gratuita disponível, a resposta é NO_FREE_MODEL_AVAILABLE.
    # `JARVIS_FREE_ONLY=0` desliga conscientemente (não é o padrão).
    free_only: bool = _env_flag("JARVIS_FREE_ONLY", True)
    # Orçamento de saída por resposta. 1024 e não 32: no smoke test da
    # v1.0 um modelo de raciocínio consumiu os 32 tokens inteiros em
    # raciocínio interno e devolveu texto vazio (ver docs/providers.md).
    provider_max_tokens: int = _env_int("JARVIS_PROVIDER_MAX_TOKENS", 1024)
    provider_timeout_s: float = _env_float("JARVIS_PROVIDER_TIMEOUT_S", 60.0)
    # Teto de caracteres do contexto de memória entregue à IA
    # (data minimization — ver services/context_builder.py).
    max_memory_context_chars: int = _env_int("JARVIS_MAX_MEMORY_CONTEXT_CHARS", 4000)

    # --- E-mail (v1.0 — verificação de conta; ver docs/security.md) ---
    # Sem SMTP configurado, `create_email_service()` devolve um serviço
    # explicitamente NÃO configurado: o JARVIS nunca finge ter enviado um
    # e-mail (erro EMAIL_SERVICE_NOT_CONFIGURED). A senha SMTP só existe no
    # ambiente — nunca em arquivo versionado, nunca no banco.
    smtp_host: str | None = os.environ.get("JARVIS_SMTP_HOST") or None
    smtp_port: int = _env_int("JARVIS_SMTP_PORT", 587)
    smtp_username: str | None = os.environ.get("JARVIS_SMTP_USERNAME") or None
    smtp_password: str | None = os.environ.get("JARVIS_SMTP_PASSWORD") or None
    smtp_use_tls: bool = _env_flag("JARVIS_SMTP_USE_TLS", True)
    email_from: str | None = os.environ.get("JARVIS_EMAIL_FROM") or None

    # --- Voz (v0.7 — ver services/stt_service.py, services/tts_service.py) ---
    # Kill-switch: mesmo com microfone e modelo presentes, permite desligar
    # a tentativa de construir um STT real (ex.: política corporativa).
    voice_input_enabled: bool = _env_flag("JARVIS_VOICE_INPUT", True)
    # Fala automática da resposta da IA — desligada por padrão de propósito
    # (ver docs/architecture.md, seção Voice Foundation). É o valor inicial;
    # o HUD liga/desliga em runtime via JarvisApplication.set_voice_output_enabled().
    voice_output_enabled: bool = _env_flag("JARVIS_VOICE_OUTPUT", False)
    # Caminho do modelo Vosk offline (nunca baixado automaticamente pelo
    # JARVIS — ver services/vosk_model_manager.py e frontend/README.md,
    # seção Voz, para o fluxo de instalação pelo próprio app).
    # Não versionado: ver .gitignore ("data/").
    stt_model_path: Path = Path(
        os.environ.get("JARVIS_STT_MODEL_PATH", "").strip()
        or str(PROJECT_ROOT / "data" / "models" / "vosk" / "vosk-model-small-pt")
    )
    # Substring (case-insensitive) do nome de uma voz SAPI5 instalada no
    # Windows. Vazio/None = escolhe automaticamente (preferência por uma voz
    # pt-BR se existir, senão a voz padrão do sistema).
    tts_voice: str | None = os.environ.get("JARVIS_TTS_VOICE") or None

    # --- STT v1.3 (ver services/stt_service.py) ---
    # "auto"  -> faster-whisper se instalado, senão Vosk (padrão)
    # "vosk"  -> força o fallback leve, mesmo com o Whisper disponível
    #            (máquina fraca, ou preferência explícita do usuário)
    stt_engine_preference: str = (
        os.environ.get("JARVIS_STT_ENGINE", "").strip().lower() or "auto"
    )
    # Tamanho do modelo Whisper. `base` é o padrão porque foi o MENOR modelo
    # que acertou 100% das frases de benchmark em português (ver
    # docs/speech-to-text.md): `tiny` erra nomes próprios e palavras curtas
    # ("Davi" -> "David", "chat" -> "chate"), e `small` não acerta mais nada
    # que o `base` já acertava — cobra 3,3x o disco (464MB vs 141MB) e 3x a
    # latência (1,16s vs 0,38s) por zero ganho.
    whisper_model_size: str = os.environ.get("JARVIS_WHISPER_MODEL", "").strip() or "base"
    # Quantização do CTranslate2. `int8` roda em CPU com ~4x menos RAM que
    # float32 e diferença de qualidade irrelevante para fala próxima.
    whisper_compute_type: str = (
        os.environ.get("JARVIS_WHISPER_COMPUTE_TYPE", "").strip() or "int8"
    )

    def has_anthropic_api_key(self) -> bool:
        return bool(self.anthropic_api_key)

    def has_openrouter_api_key(self) -> bool:
        return bool(self.openrouter_api_key)

    def has_smtp_config(self) -> bool:
        """SMTP só conta como configurado com host E remetente — sem os dois,
        não há como enviar nada de verdade (usuário/senha podem ser
        legitimamente vazios num relay local sem autenticação)."""
        return bool(self.smtp_host and self.email_from)


settings = Settings()
