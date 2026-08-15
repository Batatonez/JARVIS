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
    # --- Provider Router (v1.0 — ver services/providers/) ---
    # Erros de provider normalizados: o HUD nunca vê stack trace nem
    # mensagem crua de HTTP (ver docs/providers.md).
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    NO_FREE_MODEL_AVAILABLE = "no_free_model_available"
    # v1.3.2 — provider respondeu com metadata válida mas sem conteúdo
    # visível. Metadata nunca vira mensagem de assistant.
    EMPTY_PROVIDER_RESPONSE = "empty_provider_response"
    # --- Multi-provider fallback (v1.4.0 — ver services/providers/router.py) ---
    # Toda a cadeia (OpenRouter -> NVIDIA -> Gemini -> Groq -> Cerebras ->
    # Mistral) foi tentada e nenhum candidato produziu resposta utilizável.
    FALLBACK_EXHAUSTED = "fallback_exhausted"
    # Erro NÃO-recuperável de um provider (credencial inválida, request
    # malformado, resposta fora do schema esperado) — nunca mascarado por
    # fallback; sinal de configuração quebrada ou bug, não de instabilidade
    # transitória (ver services/providers/exceptions.py::NonRecoverableProviderError).
    PROVIDER_CONFIGURATION_ERROR = "provider_configuration_error"
    # v1.6.0 — o modelo recusou este pedido. Código PRÓPRIO, distinto de
    # falha de provider: recusa não é indisponibilidade nem erro de
    # configuração, e o HUD não deve sugerir "tente de novo"/"cheque a
    # credencial" para algo que foi uma decisão sobre o conteúdo do pedido.
    PROVIDER_REFUSED = "provider_refused"
    # --- Verificação de e-mail (v1.0 — ver services/email_verification_service.py) ---
    EMAIL_SERVICE_NOT_CONFIGURED = "email_service_not_configured"
    VERIFICATION_CODE_INVALID = "verification_code_invalid"
    VERIFICATION_CODE_EXPIRED = "verification_code_expired"
    VERIFICATION_RESEND_TOO_SOON = "verification_resend_too_soon"
    VERIFICATION_TOO_MANY_ATTEMPTS = "verification_too_many_attempts"
    # --- Conta e 2FA (v1.3 — ver services/account_service.py e two_factor_service.py) ---
    EMAIL_ALREADY_IN_USE = "email_already_in_use"
    USERNAME_ALREADY_IN_USE = "username_already_in_use"
    INVALID_USERNAME = "invalid_username"
    INVALID_EMAIL = "invalid_email"
    INVALID_PASSWORD = "invalid_password"
    REAUTH_REQUIRED = "reauth_required"
    TWO_FACTOR_REQUIRED = "two_factor_required"
    TWO_FACTOR_INVALID = "two_factor_invalid"
    TWO_FACTOR_RATE_LIMITED = "two_factor_rate_limited"
    TWO_FACTOR_ALREADY_ENABLED = "two_factor_already_enabled"
    TWO_FACTOR_NOT_ENABLED = "two_factor_not_enabled"
    CONFIRMATION_MISMATCH = "confirmation_mismatch"


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
    # --- Rota de IA da última resposta (v1.5.0) ---
    #
    # A telemetria já existia desde a v1.4.0
    # (`ProviderRouterAIService.last_result_summary`); o que faltava era ela
    # chegar ao HUD. Vazios até a primeira resposta — nunca preenchidos com um
    # provider "provável", só com o que realmente serviu.
    ai_provider: str = ""
    ai_model: str = ""
    ai_fallback_used: bool = False
    ai_fallback_count: int = 0


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


class Plan(Enum):
    """Ver `app/entitlements.py` — nenhuma outra parte do projeto deve
    comparar `plan == Plan.PRO` diretamente; sempre passar pela função
    `entitlements_for()`."""

    FREE = "free"
    PRO = "pro"


@dataclass(frozen=True)
class User:
    """Identidade pública de uma conta local (v0.9 — ver `app/account_manager.py`).
    Deliberadamente NUNCA carrega `password_hash` nem qualquer segredo — este
    é o tipo que cruza para o frontend; o hash fica só dentro de
    `services/user_repository.py`.

    `email` é `None` em contas legacy (criadas na v0.9, antes de e-mail
    existir) — elas continuam funcionando normalmente e podem adicionar um
    e-mail depois (ver `UserRepository.set_email`). Contas novas sempre têm."""

    id: str
    username: str
    display_name: str
    plan: Plan
    email: str | None = None
    email_verified: bool = False
    # v1.3 — só o FATO de o 2FA estar ligado atravessa para o frontend; o
    # segredo TOTP nunca sai de `services/user_repository.py`.
    totp_enabled: bool = False
    created_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class ConversationSummary:
    """Uma linha da lista de conversas (sidebar) — sem as mensagens, só o
    suficiente para listar/buscar/agrupar por data."""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int
