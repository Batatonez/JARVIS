"""Modelos de domínio da Application Layer — o vocabulário estável que um
futuro frontend consome. Só biblioteca padrão (dataclass, Enum, datetime,
uuid). Nada aqui importa `claude_agent_sdk`, `services/` ou qualquer detalhe
interno do Core: um frontend nunca deve receber um tipo do Agent SDK.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MessageRole(Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass(frozen=True)
class Message:
    role: MessageRole
    content: str
    id: str = field(default_factory=_new_id)
    timestamp: datetime = field(default_factory=_utcnow)


class ResponseStatus(Enum):
    SUCCESS = "success"
    CANCELLED = "cancelled"
    ERROR = "error"


class AppErrorCode(Enum):
    AI_UNAVAILABLE = "ai_unavailable"
    JARVIS_BUSY = "jarvis_busy"
    INTERNAL_ERROR = "internal_error"
    MICROPHONE_UNAVAILABLE = "microphone_unavailable"
    STT_NOT_READY = "stt_not_ready"
    TTS_UNAVAILABLE = "tts_unavailable"
    VOICE_CANCELLED = "voice_cancelled"


@dataclass(frozen=True)
class AppError:
    code: AppErrorCode
    message: str


@dataclass(frozen=True)
class TranscriptionResult:
    """Resultado de uma transcrição de voz (`VoiceService.stop_and_transcribe`).
    O texto NUNCA é enviado à IA automaticamente — cabe ao frontend decidir
    (ver `docs/architecture.md`, seção Voice Foundation)."""

    text: str


@dataclass(frozen=True)
class AssistantResponse:
    """Resultado de `JarvisApplication.send_message()`. O frontend distingue
    sucesso/cancelado/erro por `status`, nunca analisando texto."""

    status: ResponseStatus
    message_id: str | None = None
    content: str = ""
    error: AppError | None = None


@dataclass(frozen=True)
class StatusSnapshot:
    core_version: str
    state: str
    running: bool
    busy: bool
    memory_available: bool
    ai_configured: bool
    ai_backend: str
    ai_session_active: bool
    active_conversation: bool
    # --- Voz (v0.7) — só dados reais; ausentes (False) quando não há
    # VoiceService disponível para o chamador (ex.: /status do terminal). ---
    voice_available: bool = False
    microphone_available: bool = False
    stt_ready: bool = False
    tts_ready: bool = False
    voice_input_active: bool = False
    voice_output_active: bool = False
    voice_output_enabled: bool = False


@dataclass(frozen=True)
class AppEvent:
    """Evento padronizado enviado a consumidores externos (futuro HUD). O
    `payload` contém apenas tipos simples — nunca objetos internos."""

    type: str
    timestamp: datetime
    payload: dict = field(default_factory=dict)


class RiskLevel(Enum):
    READ = "read"
    ACTION = "action"
    DANGEROUS = "dangerous"


class PermissionStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


@dataclass
class PermissionRequest:
    """Fundação para permissões futuras (ver `app/permissions.py`) — ainda
    não conectada a nenhuma ferramenta real. Mutável: `status` muda com o
    tempo (PENDING -> APPROVED/DENIED)."""

    action: str
    description: str
    risk_level: RiskLevel
    id: str = field(default_factory=_new_id)
    status: PermissionStatus = PermissionStatus.PENDING
