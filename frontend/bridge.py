"""JarvisBridge: a única ponte entre QML e o resto do JARVIS.

Não contém lógica de domínio — só traduz `AccountManager`/`JarvisApplication`
para Properties/Signals/Slots do Qt, e vice-versa. O QML nunca importa
`app.account_manager`, `app.application`, `app.core` ou qualquer coisa de
`services/` — só conhece este Bridge.

**v0.9 — auth-first:** antes não existia login; `JarvisApplication` era
construída uma vez no início e vivia pelo processo inteiro. Agora
`AccountManager` (não o Bridge) é dono do ciclo de vida de
`JarvisCore`/`JarvisApplication` — eles só existem enquanto alguém está
logado. O Bridge acompanha isso via `self._app` (uma property que sempre lê
`self._account.app`, nunca uma referência fixa) e reage à troca de sessão
como qualquer outra mudança de estado.

Atualização de estado continua orientada a eventos: o Bridge consome
`JarvisApplication.events()` uma vez por sessão (`_consume_events`) e
atualiza Properties Qt quando algo muda de verdade — nunca há polling nem
`get_status()` chamado em loop/timer.
"""

import asyncio
import contextlib
import logging

from PySide6.QtCore import Property, QCoreApplication, QObject, Signal, Slot
from PySide6.QtGui import QGuiApplication

from app.account_manager import (
    AccountLockedError,
    AccountManager,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    TwoFactorRequiredError,
    UsernameAlreadyExistsError,
)
from services.email_verification_service import mask_email
from app.models import AppErrorCode, AppEvent, ResponseStatus, RiskLevel
from frontend.message_model import MessageListModel, to_local_display_time
from services.stt_service import create_stt_service
from services.vosk_model_manager import ModelDownloadCancelled, ModelDownloadError, VoiceModelManager

logger = logging.getLogger(__name__)

# Eventos da Application Layer que implicam reler o status consolidado.
_STATUS_EVENTS = frozenset(
    {
        "state.changed",
        "ai.connected",
        "ai.disconnected",
        "jarvis.started",
        "jarvis.stopped",
        "response.started",
        "response.completed",
        "response.regenerated",
        "response.failed",
        "voice.listening.started",
        "voice.listening.stopped",
        "voice.speaking.started",
        "voice.speaking.stopped",
        "voice.speaking.failed",
    }
)
# Eventos que implicam ressincronizar o histórico de mensagens.
_MESSAGE_EVENTS = frozenset(
    {
        "message.received",
        "response.completed",
        # Regeneração troca o texto de uma mensagem existente — o modelo
        # precisa ressincronizar para o chat mostrar a resposta nova.
        "response.regenerated",
        "response.failed",
        "conversation.started",
        "conversation.cleared",
        "conversation.loaded",
    }
)
# Eventos que podem ter criado/atualizado uma conversa persistida (sidebar).
# `conversation.retitled` (v1.3) entra aqui: o título automático mudou o nome
# na sidebar sem que nenhuma mensagem nova tenha chegado.
_CONVERSATION_LIST_EVENTS = _MESSAGE_EVENTS | {"conversation.retitled"}


class JarvisBridge(QObject):
    # --- Contas/sessão (v0.9) ---
    authenticatedChanged = Signal()
    currentUserChanged = Signal()
    authErrorRaised = Signal(str)
    conversationsChanged = Signal()
    currentConversationIdChanged = Signal()

    # --- Estado do Core/Application (por sessão) ---
    stateChanged = Signal()
    runningChanged = Signal()
    busyChanged = Signal()
    memoryAvailableChanged = Signal()
    aiConfiguredChanged = Signal()
    aiBackendChanged = Signal()
    aiSessionActiveChanged = Signal()
    activeConversationChanged = Signal()
    pendingPermissionChanged = Signal()
    canCloseChanged = Signal()
    voiceAvailableChanged = Signal()
    ttsReadyChanged = Signal()
    voiceOutputEnabledChanged = Signal()
    voiceLevelChanged = Signal()
    microphoneAvailableChanged = Signal()
    sttReadyChanged = Signal()
    sttStatusChanged = Signal()

    # --- Verificação de e-mail (v1.0) ---
    verificationStateChanged = Signal()
    verificationErrorRaised = Signal(str)
    verificationSucceeded = Signal()

    # --- Conta, 2FA e sessões (v1.3) ---
    awaitingTwoFactorChanged = Signal()
    accountErrorRaised = Signal(str)
    accountUpdated = Signal(str)  # mensagem curta de sucesso para o HUD
    reauthStateChanged = Signal()
    twoFactorStatusChanged = Signal()
    twoFactorEnrollmentReady = Signal()
    recoveryCodesReady = Signal()
    sessionsChanged = Signal()
    emailChangeStateChanged = Signal()
    accountDeleted = Signal()

    # --- Voz: dispositivos e teste de microfone (v1.3) ---
    audioDevicesChanged = Signal()
    microphoneTestFinished = Signal(str)
    microphoneTestActiveChanged = Signal()
    sttEngineChanged = Signal()

    # --- Voice Model Manager (v0.9 — global, não por usuário) ---
    voiceModelInstalledChanged = Signal()
    voiceModelDownloadActiveChanged = Signal()
    voiceModelDownloadProgressChanged = Signal()

    busyRejected = Signal(str)
    internalErrorRaised = Signal(str)
    transcriptionReady = Signal(str)
    voiceErrorRaised = Signal(str)

    def __init__(
        self,
        account_manager: AccountManager,
        voice_model_manager: VoiceModelManager,
        *,
        dev_mode: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._account = account_manager
        self._voice_models = voice_model_manager
        self._dev_mode = dev_mode
        self._message_model = MessageListModel(self)

        self._authenticated = False
        self._current_user: dict | None = None
        self._conversations: list[dict] = []

        self._state = "idle"
        self._running = False
        self._busy = False
        self._memory_available = False
        self._ai_configured = False
        self._ai_backend = "nenhum"
        self._ai_session_active = False
        self._active_conversation = False
        self._pending_permission: dict | None = None
        self._can_close = False
        self._voice_available = False
        self._tts_ready = False
        self._voice_output_enabled = False
        self._voice_level = 0.0
        self._microphone_available = False
        self._stt_ready = False
        self._stt_status = "unavailable"

        # --- v1.3 ---
        self._awaiting_two_factor = False
        self._two_factor_status: dict = {
            "enabled": False,
            "enrollmentPending": False,
            "recoveryCodesRemaining": 0,
            "secretProtected": False,
            "lockoutSeconds": 0,
        }
        # Material de ativação do 2FA e códigos de recuperação vivem só em RAM
        # e só enquanto a tela está aberta — nunca são persistidos aqui nem
        # emitidos como evento da Application Layer.
        self._two_factor_enrollment: dict | None = None
        self._recovery_codes: list[str] = []
        self._sessions: list[dict] = []
        self._email_change: dict | None = None
        self._audio_devices: list[dict] = []
        self._selected_microphone_key = ""
        self._microphone_fell_back = False
        self._microphone_test_active = False
        self._stt_engine = "—"

        # Verificação de e-mail (v1.0) — os segundos vêm de timestamps reais
        # do banco, nunca de um timer que começa quando a tela abre.
        self._verification_seconds_until_expiry = 0
        self._verification_seconds_until_resend = 0

        self._voice_model_download_active = False
        self._voice_model_downloaded_bytes = 0
        self._voice_model_total_bytes = 0

        self._event_queue: "asyncio.Queue[AppEvent] | None" = None
        self._event_task: asyncio.Task | None = None
        self._shutdown_task: asyncio.Task | None = None

    @property
    def _app(self):
        """Sempre a `JarvisApplication` da sessão atual (ou `None` se
        deslogado) — nunca uma referência fixa, porque `AccountManager` troca
        essa instância a cada login/logout."""
        return self._account.app

    # ------------------------------------------------------------------
    # Lifecycle (chamado pelo launcher, não pelo QML)
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Chamado uma vez, no início do processo: tenta continuar logado a
        partir de uma sessão local persistida (ver `AccountManager.try_auto_login`).
        Nunca pede usuário/senha aqui — se não houver sessão válida, o HUD
        mostra a tela de login normalmente."""
        user = await self._account.try_auto_login()
        if user is not None:
            await self._enter_session(user)

    async def _enter_session(self, user) -> None:
        self._current_user = self._user_to_dict(user)
        self.currentUserChanged.emit()
        self._set_property("_authenticated", True, self.authenticatedChanged)

        # `subscribe()` é síncrono e registra a fila imediatamente — mesmo
        # motivo de sempre (ver docstring do módulo v0.5): se inscrevêssemos
        # via `events()` (async generator), qualquer evento emitido antes da
        # primeira execução da task seria perdido.
        self._event_queue = self._app.subscribe()
        self._event_task = asyncio.ensure_future(self._consume_events())

        self._refresh_status()
        self._sync_messages()
        self._refresh_conversations()
        self._refresh_verification_state()
        # v1.3 — estado de conta e voz da sessão recém-aberta.
        self._refresh_two_factor_status()
        self._refresh_sessions()
        self._refresh_email_change_state()
        self._apply_preferred_microphone()
        self._refresh_audio_devices()

    async def _leave_session(self) -> None:
        if self._event_task is not None:
            self._event_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._event_task
            self._event_task = None
        self._event_queue = None

    async def _consume_events(self) -> None:
        assert self._event_queue is not None
        queue = self._event_queue
        try:
            while True:
                event = await queue.get()
                try:
                    self._handle_event(event)
                except Exception:
                    logger.exception("Erro ao processar evento '%s' no Bridge.", event.type)
        finally:
            if self._app is not None:
                self._app.unsubscribe(queue)

    def _handle_event(self, event: AppEvent) -> None:
        if event.type in _STATUS_EVENTS:
            self._refresh_status()
        if event.type in _MESSAGE_EVENTS:
            self._sync_messages()
        if event.type in _CONVERSATION_LIST_EVENTS:
            self._refresh_conversations()
        if event.type == "permission.requested":
            self._set_pending_permission()
        elif event.type == "permission.resolved":
            self._clear_pending_permission()
        elif event.type == "voice.level":
            level = float(event.payload.get("level", 0.0))
            self._set_property("_voice_level", level, self.voiceLevelChanged)
        elif event.type == "voice.transcription.completed":
            text = str(event.payload.get("text", "")).strip()
            if text:
                self.transcriptionReady.emit(text)
            else:
                self.voiceErrorRaised.emit("Não entendi — tente novamente.")
        elif event.type == "voice.transcription.failed":
            self.voiceErrorRaised.emit(str(event.payload.get("error", "Falha ao transcrever.")))
        elif event.type == "voice.speaking.failed":
            self.voiceErrorRaised.emit(str(event.payload.get("error", "Falha ao falar.")))
        elif event.type == "response.delta":
            # Preparação para streaming real (v0.8) — `response.delta` NÃO é
            # emitido por nenhum backend hoje (nenhum Claude real conectado,
            # nenhum código em app/application.py produz este evento). Existe
            # só para o `MessageListModel.update_content()` já ter, desde
            # agora, um único ponto de entrada óbvio quando um evento desses
            # existir de verdade — sem precisar reestruturar o Bridge depois.
            message_id = event.payload.get("message_id")
            content = event.payload.get("content")
            if message_id and content is not None:
                self._message_model.update_content(str(message_id), str(content))

    # ------------------------------------------------------------------
    # Sincronização de estado (sempre disparada por evento, nunca por timer)
    # ------------------------------------------------------------------

    def _refresh_status(self) -> None:
        if self._app is None:
            return
        snapshot = self._app.get_status()
        self._set_property("_state", snapshot.state, self.stateChanged)
        self._set_property("_running", snapshot.running, self.runningChanged)
        self._set_property("_busy", snapshot.busy, self.busyChanged)
        self._set_property("_memory_available", snapshot.memory_available, self.memoryAvailableChanged)
        self._set_property("_ai_configured", snapshot.ai_configured, self.aiConfiguredChanged)
        self._set_property("_ai_backend", snapshot.ai_backend, self.aiBackendChanged)
        self._set_property("_ai_session_active", snapshot.ai_session_active, self.aiSessionActiveChanged)
        self._set_property("_active_conversation", snapshot.active_conversation, self.activeConversationChanged)
        self._set_property("_voice_available", snapshot.voice_available, self.voiceAvailableChanged)
        self._set_property("_tts_ready", snapshot.tts_ready, self.ttsReadyChanged)
        self._set_property("_voice_output_enabled", snapshot.voice_output_enabled, self.voiceOutputEnabledChanged)
        self._set_property("_microphone_available", snapshot.microphone_available, self.microphoneAvailableChanged)
        self._set_property("_stt_ready", snapshot.stt_ready, self.sttReadyChanged)
        self._set_property("_stt_status", self._app.voice.stt_status.value, self.sttStatusChanged)

    def _set_property(self, attr: str, value: object, signal: Signal) -> None:
        if getattr(self, attr) != value:
            setattr(self, attr, value)
            signal.emit()

    def _sync_messages(self) -> None:
        if self._app is not None:
            self._message_model.sync(self._app.get_messages())

    def _refresh_conversations(self) -> None:
        summaries = self._account.list_conversations()
        self._conversations = [self._summary_to_dict(summary) for summary in summaries]
        self.conversationsChanged.emit()
        self.currentConversationIdChanged.emit()

    def _set_pending_permission(self) -> None:
        if self._app is None:
            return
        pending = self._app.permissions.list_pending()
        if not pending:
            return
        request = pending[-1]
        self._pending_permission = {
            "id": request.id,
            "action": request.action,
            "description": request.description,
            "riskLevel": request.risk_level.value,
        }
        self.pendingPermissionChanged.emit()

    def _clear_pending_permission(self) -> None:
        if self._pending_permission is not None:
            self._pending_permission = None
            self.pendingPermissionChanged.emit()

    @staticmethod
    def _user_to_dict(user) -> dict:
        return {
            "id": user.id,
            "username": user.username,
            "displayName": user.display_name,
            "plan": user.plan.value,
            # `email` é `None` em contas legacy (v0.9) — o QML trata como
            # string vazia. Nunca expomos hash de senha ou token aqui.
            "email": user.email or "",
            "maskedEmail": mask_email(user.email) if user.email else "",
            "emailVerified": user.email_verified,
            # v1.3 — só o FATO de o 2FA estar ligado. O segredo TOTP nunca
            # sai de `services/user_repository.py`.
            "twoFactorEnabled": user.totp_enabled,
        }

    def _refresh_verification_state(self) -> None:
        """Relê o desafio ativo do banco e recalcula os dois contadores. Só
        isto alimenta a tela — o Timer do QML apenas decrementa a exibição
        entre atualizações."""
        challenge = self._account.active_verification_challenge()
        if challenge is None:
            expiry, resend = 0, 0
        else:
            expiry = challenge.seconds_until_expiry()
            resend = challenge.seconds_until_resend()
        changed = (
            expiry != self._verification_seconds_until_expiry
            or resend != self._verification_seconds_until_resend
        )
        self._verification_seconds_until_expiry = expiry
        self._verification_seconds_until_resend = resend
        if changed:
            self.verificationStateChanged.emit()

    @staticmethod
    def _summary_to_dict(summary) -> dict:
        return {
            "id": summary.id,
            "title": summary.title,
            "createdAt": summary.created_at.isoformat(),
            "updatedAt": summary.updated_at.isoformat(),
            "messageCount": summary.message_count,
        }

    # ------------------------------------------------------------------
    # Properties Qt — contas/sessão
    # ------------------------------------------------------------------

    @Property(bool, notify=authenticatedChanged)
    def authenticated(self) -> bool:
        return self._authenticated

    @Property("QVariant", notify=currentUserChanged)
    def currentUser(self):
        return self._current_user

    @Property("QVariant", notify=conversationsChanged)
    def conversations(self):
        return self._conversations

    @Property(str, notify=currentConversationIdChanged)
    def currentConversationId(self) -> str:
        return self._account.current_conversation_id or ""

    # ------------------------------------------------------------------
    # Properties/Slots — conta, 2FA e sessões (v1.3)
    #
    # O QML NUNCA decide nada aqui (item 60): não escolhe `user_id`, não sabe
    # se o 2FA está válido, não julga se a reautenticação expirou. Toda
    # property abaixo é um espelho somente-leitura de uma decisão que o
    # backend já tomou, e todo Slot devolve o resultado do backend.
    # ------------------------------------------------------------------

    @Property(bool, notify=awaitingTwoFactorChanged)
    def awaitingTwoFactor(self) -> bool:
        """`True` entre "senha aceita" e "segundo fator confirmado". Enquanto
        for `True` não existe sessão nenhuma criada."""
        return self._awaiting_two_factor

    @Property(bool, notify=reauthStateChanged)
    def reauthValid(self) -> bool:
        return self._account.reauth_valid()

    @Property("QVariant", notify=twoFactorStatusChanged)
    def twoFactorStatus(self):
        return self._two_factor_status

    @Property("QVariant", notify=twoFactorEnrollmentReady)
    def twoFactorEnrollment(self):
        """Segredo, URI e matriz do QR — só existe enquanto a tela de
        ativação está aberta, e some assim que ela fecha."""
        return self._two_factor_enrollment

    @Property("QVariant", notify=recoveryCodesReady)
    def recoveryCodes(self):
        return self._recovery_codes

    @Property("QVariant", notify=sessionsChanged)
    def activeSessions(self):
        return self._sessions

    @Property("QVariant", notify=emailChangeStateChanged)
    def pendingEmailChange(self):
        return self._email_change

    @Slot(str)
    def confirmPassword(self, password: str) -> None:
        """Abre a janela de reautenticação recente (item 45)."""
        error = self._account.confirm_password(password)
        self.reauthStateChanged.emit()
        if error is not None:
            self.accountErrorRaised.emit(error.message)
            return
        self.accountUpdated.emit("Senha confirmada.")

    @Slot(str)
    def changeDisplayName(self, display_name: str) -> None:
        user, error = self._account.change_display_name(display_name)
        if error is not None:
            self.accountErrorRaised.emit(error.message)
            return
        self._current_user = self._user_to_dict(user)
        self.currentUserChanged.emit()
        self.accountUpdated.emit("Nome atualizado.")

    @Slot(str)
    def changeUsername(self, username: str) -> None:
        user, error = self._account.change_username(username)
        if error is not None:
            self.accountErrorRaised.emit(error.message)
            return
        self._current_user = self._user_to_dict(user)
        self.currentUserChanged.emit()
        self.accountUpdated.emit("Username atualizado.")

    @Slot(str, str, str)
    def changePassword(self, current_password: str, new_password: str, confirm_password: str) -> None:
        revoked, error = self._account.change_password(
            current_password=current_password,
            new_password=new_password,
            confirm_password=confirm_password,
        )
        self.reauthStateChanged.emit()
        if error is not None:
            self.accountErrorRaised.emit(error.message)
            return
        self._refresh_sessions()
        message = "Senha alterada."
        if revoked:
            message += f" {revoked} outra(s) sessão(ões) foram encerradas."
        self.accountUpdated.emit(message)

    # --- Troca de e-mail ---

    @Slot(str)
    def requestEmailChange(self, new_email: str) -> None:
        asyncio.ensure_future(self._request_email_change(new_email))

    async def _request_email_change(self, new_email: str) -> None:
        result = await self._account.request_email_change(new_email)
        self._refresh_email_change_state()
        if result.error is not None:
            self.accountErrorRaised.emit(result.error.message)
            return
        self.accountUpdated.emit("Código enviado ao novo e-mail.")

    @Slot(str)
    def confirmEmailChange(self, code: str) -> None:
        asyncio.ensure_future(self._confirm_email_change(code))

    async def _confirm_email_change(self, code: str) -> None:
        error = await self._account.confirm_email_change(code)
        self._refresh_email_change_state()
        if error is not None:
            self.accountErrorRaised.emit(error.message)
            return
        self._current_user = self._user_to_dict(self._account.current_user)
        self.currentUserChanged.emit()
        self.accountUpdated.emit("E-mail alterado.")

    def _refresh_email_change_state(self) -> None:
        pending = self._account.active_email_change()
        self._email_change = (
            None
            if pending is None
            else {
                "maskedEmail": pending.masked_email,
                "secondsUntilExpiry": pending.seconds_until_expiry(),
                "secondsUntilResend": pending.seconds_until_resend(),
                "attempts": pending.attempts,
            }
        )
        self.emailChangeStateChanged.emit()

    # --- 2FA ---

    @Slot()
    def refreshTwoFactorStatus(self) -> None:
        self._refresh_two_factor_status()

    def _refresh_two_factor_status(self) -> None:
        status = self._account.two_factor_status()
        self._two_factor_status = {
            "enabled": bool(status and status.enabled),
            "enrollmentPending": bool(status and status.enrollment_pending),
            "recoveryCodesRemaining": status.recovery_codes_remaining if status else 0,
            "secretProtected": bool(status and status.secret_protected),
            "lockoutSeconds": status.lockout_seconds if status else 0,
        }
        self.twoFactorStatusChanged.emit()

    @Slot()
    def startTwoFactorEnrollment(self) -> None:
        enrollment, error = self._account.start_two_factor_enrollment()
        if error is not None:
            self.accountErrorRaised.emit(error.message)
            return
        self._two_factor_enrollment = {
            # `secret` vai para a UI porque o usuário precisa poder digitar a
            # chave à mão quando não consegue escanear o QR (item 47). Ele
            # nunca é logado, persistido em claro, nem enviado a provider.
            "secret": enrollment.formatted_secret,
            "qr": enrollment.qr_matrix,
            "secretProtected": enrollment.secret_protected,
        }
        self.twoFactorEnrollmentReady.emit()
        self._refresh_two_factor_status()

    @Slot(str)
    def confirmTwoFactorEnrollment(self, code: str) -> None:
        codes, error = self._account.confirm_two_factor_enrollment(code)
        if error is not None:
            self.accountErrorRaised.emit(error.message)
            return
        # O material de ativação some da RAM assim que o 2FA fica ativo.
        self._two_factor_enrollment = None
        self.twoFactorEnrollmentReady.emit()
        self._recovery_codes = codes or []
        self.recoveryCodesReady.emit()
        self._current_user = self._user_to_dict(self._account.current_user)
        self.currentUserChanged.emit()
        self._refresh_two_factor_status()
        self.accountUpdated.emit("Verificação em duas etapas ativada.")

    @Slot(str)
    def regenerateRecoveryCodes(self, code: str) -> None:
        codes, error = self._account.regenerate_recovery_codes(code)
        if error is not None:
            self.accountErrorRaised.emit(error.message)
            return
        self._recovery_codes = codes or []
        self.recoveryCodesReady.emit()
        self._refresh_two_factor_status()
        self.accountUpdated.emit("Novos códigos de recuperação gerados.")

    @Slot(str)
    def disableTwoFactor(self, code: str) -> None:
        error = self._account.disable_two_factor(code)
        if error is not None:
            self.accountErrorRaised.emit(error.message)
            return
        self._recovery_codes = []
        self.recoveryCodesReady.emit()
        self._current_user = self._user_to_dict(self._account.current_user)
        self.currentUserChanged.emit()
        self._refresh_two_factor_status()
        self.accountUpdated.emit("Verificação em duas etapas desativada.")

    @Slot()
    def cancelTwoFactorEnrollment(self) -> None:
        """Usuário fechou a tela de ativação sem confirmar. Descarta o
        segredo gerado — `TwoFactorService.cancel_enrollment` só age quando o
        2FA NÃO está ativo, então isto nunca vira um caminho para desligar o
        2FA sem segundo fator."""
        user = self._account.current_user
        if user is not None:
            self._account.two_factor.cancel_enrollment(user.id)
        self._two_factor_enrollment = None
        self.twoFactorEnrollmentReady.emit()
        self._refresh_two_factor_status()

    @Slot()
    def clearRecoveryCodes(self) -> None:
        """Chamado quando o usuário fecha a tela que mostrou os códigos —
        eles não devem continuar acessíveis ao QML depois disso."""
        self._recovery_codes = []
        self.recoveryCodesReady.emit()

    # --- Sessões ---

    @Slot()
    def refreshSessions(self) -> None:
        self._refresh_sessions()

    def _refresh_sessions(self) -> None:
        self._sessions = [
            {
                "id": session.session_id,
                "device": session.device_label,
                "platform": session.platform,
                "createdAt": to_local_display_time(session.created_at),
                "lastUsedAt": (
                    to_local_display_time(session.last_used_at) if session.last_used_at else ""
                ),
                "isCurrent": session.is_current,
            }
            for session in self._account.list_sessions()
        ]
        self.sessionsChanged.emit()

    @Slot()
    def logOutOtherSessions(self) -> None:
        revoked, error = self._account.log_out_other_sessions()
        if error is not None:
            self.accountErrorRaised.emit(error.message)
            return
        self._refresh_sessions()
        self.accountUpdated.emit(f"{revoked} outra(s) sessão(ões) encerrada(s).")

    # --- Exclusão da conta ---

    @Slot(str, str, str)
    def deleteAccount(self, password: str, confirmation: str, two_factor_code: str) -> None:
        asyncio.ensure_future(self._delete_account(password, confirmation, two_factor_code))

    async def _delete_account(self, password: str, confirmation: str, two_factor_code: str) -> None:
        error = await self._account.delete_current_account(
            password=password, confirmation=confirmation, two_factor_code=two_factor_code
        )
        if error is not None:
            self.accountErrorRaised.emit(error.message)
            return
        # A conta não existe mais: o Bridge volta ao estado de "ninguém
        # logado", igual a um logout, mas sem sessão para invalidar.
        await self._leave_session()
        self._current_user = None
        self.currentUserChanged.emit()
        self._conversations = []
        self.conversationsChanged.emit()
        self._message_model.sync([])
        self._sessions = []
        self.sessionsChanged.emit()
        self._set_property("_authenticated", False, self.authenticatedChanged)
        self.accountDeleted.emit()

    # ------------------------------------------------------------------
    # Properties/Slots — verificação de e-mail (v1.0)
    # ------------------------------------------------------------------

    @Property(bool, constant=True)
    def emailServiceConfigured(self) -> bool:
        return self._account.verification.email_configured

    @Property(bool, notify=currentUserChanged)
    def emailVerified(self) -> bool:
        user = self._account.current_user
        return bool(user and user.email_verified)

    @Property(bool, notify=currentUserChanged)
    def emailVerificationPending(self) -> bool:
        """Só é "pendente" quando há e-mail cadastrado e ainda não
        verificado. Conta legacy sem e-mail não fica presa nesse estado."""
        user = self._account.current_user
        return bool(user and user.email and not user.email_verified)

    @Property(int, notify=verificationStateChanged)
    def verificationSecondsUntilExpiry(self) -> int:
        return self._verification_seconds_until_expiry

    @Property(int, notify=verificationStateChanged)
    def verificationSecondsUntilResend(self) -> int:
        return self._verification_seconds_until_resend

    @Slot()
    def requestVerificationCode(self) -> None:
        asyncio.ensure_future(self._request_verification(force=False))

    async def _request_verification(self, *, force: bool) -> None:
        result = await self._account.request_email_verification(force=force)
        self._refresh_verification_state()
        if result.error is not None:
            self.verificationErrorRaised.emit(result.error.message)

    @Slot(str)
    def verifyEmailCode(self, code: str) -> None:
        error = self._account.verify_email_code(code)
        self._refresh_verification_state()
        if error is not None:
            self.verificationErrorRaised.emit(error.message)
            return
        self._current_user = self._user_to_dict(self._account.current_user)
        self.currentUserChanged.emit()
        self.verificationSucceeded.emit()

    # ------------------------------------------------------------------
    # Slots — contas/sessão
    # ------------------------------------------------------------------

    @Slot(str, str, str, str)
    def register(self, username: str, display_name: str, email: str, password: str) -> None:
        asyncio.ensure_future(self._register(username, display_name, email, password))

    async def _register(self, username: str, display_name: str, email: str, password: str) -> None:
        try:
            user = await self._account.register(
                username=username, display_name=display_name, password=password, email=email or None
            )
        except (UsernameAlreadyExistsError, EmailAlreadyRegisteredError, ValueError) as exc:
            self.authErrorRaised.emit(str(exc))
            return
        except Exception:
            logger.exception("Falha inesperada ao criar conta.")
            self.authErrorRaised.emit("Não foi possível criar a conta. Tente novamente.")
            return
        await self._enter_session(user)
        # Dispara o primeiro código automaticamente (force=True: ainda não há
        # desafio anterior, então não há cooldown a respeitar). Sem SMTP
        # configurado isto falha de forma explícita e o overlay diz isso —
        # nunca fingimos ter enviado.
        asyncio.ensure_future(self._request_verification(force=True))

    @Slot(str, str)
    def login(self, identifier: str, password: str) -> None:
        """`identifier` é username OU e-mail (item 38 da v1.3) — o campo do
        HUD virou "USERNAME OR EMAIL" e quem resolve é o backend."""
        asyncio.ensure_future(self._login(identifier, password))

    async def _login(self, identifier: str, password: str) -> None:
        try:
            user = await self._account.login(identifier=identifier, password=password)
        except TwoFactorRequiredError:
            # A senha bateu, mas nenhuma sessão existe ainda: o HUD abre a
            # tela do segundo fator (item 52).
            self._set_property("_awaiting_two_factor", True, self.awaitingTwoFactorChanged)
            return
        except (InvalidCredentialsError, AccountLockedError) as exc:
            # `AccountLockedError` já traz "tente novamente em Ns" — é a única
            # mensagem de login que difere, e de propósito: sem ela o usuário
            # legítimo não entenderia por que a senha certa está falhando.
            self.authErrorRaised.emit(str(exc))
            return
        except Exception:
            logger.exception("Falha inesperada ao entrar.")
            self.authErrorRaised.emit("Não foi possível entrar. Tente novamente.")
            return
        await self._enter_session(user)

    @Slot(str)
    def submitTwoFactorCode(self, code: str) -> None:
        asyncio.ensure_future(self._submit_two_factor(code))

    async def _submit_two_factor(self, code: str) -> None:
        user, error = await self._account.complete_two_factor(code)
        if error is not None:
            self.authErrorRaised.emit(error.message)
            return
        self._set_property("_awaiting_two_factor", False, self.awaitingTwoFactorChanged)
        if user is not None:
            await self._enter_session(user)

    @Slot()
    def cancelTwoFactor(self) -> None:
        self._account.cancel_two_factor()
        self._set_property("_awaiting_two_factor", False, self.awaitingTwoFactorChanged)

    @Slot()
    def logout(self) -> None:
        asyncio.ensure_future(self._logout())

    async def _logout(self) -> None:
        await self._leave_session()
        await self._account.logout()
        self._current_user = None
        self.currentUserChanged.emit()
        self._conversations = []
        self.conversationsChanged.emit()
        self._pending_permission = None
        self.pendingPermissionChanged.emit()
        self._message_model.sync([])
        self._set_property("_authenticated", False, self.authenticatedChanged)

    # ------------------------------------------------------------------
    # Properties Qt — Core/Application (só fazem sentido autenticado)
    # ------------------------------------------------------------------

    @Property(str, notify=stateChanged)
    def jarvisState(self) -> str:
        return self._state

    @Property(bool, notify=runningChanged)
    def running(self) -> bool:
        return self._running

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(bool, notify=memoryAvailableChanged)
    def memoryAvailable(self) -> bool:
        return self._memory_available

    @Property(bool, notify=aiConfiguredChanged)
    def aiConfigured(self) -> bool:
        return self._ai_configured

    @Property(str, notify=aiBackendChanged)
    def aiBackend(self) -> str:
        return self._ai_backend

    @Property(bool, notify=aiSessionActiveChanged)
    def aiSessionActive(self) -> bool:
        return self._ai_session_active

    @Property(bool, notify=activeConversationChanged)
    def activeConversation(self) -> bool:
        return self._active_conversation

    @Property("QVariant", notify=pendingPermissionChanged)
    def pendingPermission(self):
        return self._pending_permission

    @Property(bool, notify=canCloseChanged)
    def canClose(self) -> bool:
        return self._can_close

    @Property(bool, notify=voiceAvailableChanged)
    def voiceAvailable(self) -> bool:
        return self._voice_available

    @Property(bool, notify=ttsReadyChanged)
    def ttsReady(self) -> bool:
        return self._tts_ready

    @Property(bool, notify=voiceOutputEnabledChanged)
    def voiceOutputEnabled(self) -> bool:
        return self._voice_output_enabled

    @Property(float, notify=voiceLevelChanged)
    def voiceLevel(self) -> float:
        return self._voice_level

    @Property(bool, notify=microphoneAvailableChanged)
    def microphoneAvailable(self) -> bool:
        return self._microphone_available

    @Property(bool, notify=sttReadyChanged)
    def sttReady(self) -> bool:
        return self._stt_ready

    @Property(str, notify=sttStatusChanged)
    def sttStatus(self) -> str:
        return self._stt_status

    @Property(bool, constant=True)
    def devMode(self) -> bool:
        return self._dev_mode

    @Property(QObject, constant=True)
    def messages(self) -> MessageListModel:
        return self._message_model

    # ------------------------------------------------------------------
    # Properties/Slots — dispositivo de entrada e teste de microfone (v1.3)
    # ------------------------------------------------------------------

    @Property(str, notify=sttEngineChanged)
    def sttEngine(self) -> str:
        """Nome legível do engine em uso ("Faster Whisper" / "Vosk" / "—").
        Saber que caiu no fallback é informação legítima para o usuário."""
        return self._stt_engine

    @Property("QVariant", notify=audioDevicesChanged)
    def audioDevices(self):
        return self._audio_devices

    @Property(str, notify=audioDevicesChanged)
    def selectedMicrophoneKey(self) -> str:
        return self._selected_microphone_key

    @Property(bool, notify=audioDevicesChanged)
    def microphoneFellBack(self) -> bool:
        """`True` quando o microfone salvo sumiu e o sistema caiu no padrão —
        o HUD avisa discretamente em vez de trocar em silêncio (item 14)."""
        return self._microphone_fell_back

    @Property(bool, notify=microphoneTestActiveChanged)
    def microphoneTestActive(self) -> bool:
        return self._microphone_test_active

    @Slot()
    def refreshAudioDevices(self) -> None:
        """"REFRESH DEVICES" (item 15) — re-enumera para pegar um microfone
        conectado depois que o app já estava aberto."""
        stt = self._stt()
        if stt is not None:
            stt.refresh_devices()
        self._refresh_audio_devices()

    @Slot(str)
    def selectMicrophone(self, device_key: str) -> None:
        """Troca o dispositivo e PERSISTE a escolha por conta. Guardamos a
        chave estável (host API + nome), nunca o índice."""
        stt = self._stt()
        if stt is None:
            self.voiceErrorRaised.emit("Reconhecimento de fala não está disponível.")
            return
        if not stt.select_device(device_key or None):
            self.voiceErrorRaised.emit("Não foi possível usar esse microfone.")
            self._refresh_audio_devices()
            return
        self._account.set_preferred_microphone(device_key or None)
        self._refresh_audio_devices()
        self._refresh_status()

    @Slot()
    def testMicrophone(self) -> None:
        """"TEST MICROPHONE" (item 16): grava alguns segundos, mostra o nível
        e transcreve. O texto vai para `microphoneTestFinished` — **nunca**
        para a IA e **nunca** vira mensagem do chat."""
        if self._microphone_test_active:
            return
        asyncio.ensure_future(self._test_microphone())

    async def _test_microphone(self) -> None:
        stt = self._stt()
        if stt is None or not stt.microphone_available:
            self.voiceErrorRaised.emit("Nenhum microfone disponível.")
            return

        self._set_property("_microphone_test_active", True, self.microphoneTestActiveChanged)
        try:
            # `auto_stop=True`: o VAD encerra sozinho depois do silêncio final,
            # então o teste não depende de o usuário clicar de novo.
            await stt.start_listening(on_level=self._on_test_level, auto_stop=True)
            while getattr(stt, "listening", False):
                await asyncio.sleep(0.1)
            text = await stt.stop_and_transcribe()
        except Exception as exc:
            logger.info("Falha no teste de microfone: %s", exc)
            self.voiceErrorRaised.emit("Não foi possível testar o microfone.")
            return
        finally:
            self._set_property("_microphone_test_active", False, self.microphoneTestActiveChanged)
            self._set_property("_voice_level", 0.0, self.voiceLevelChanged)

        self.microphoneTestFinished.emit(text or "")

    def _on_test_level(self, level: float) -> None:
        """Medidor de nível durante o teste. Reusa a MESMA property que o
        push-to-talk já alimenta — nenhum polling novo, nenhum timer extra
        (item 17)."""
        self._set_property("_voice_level", float(level), self.voiceLevelChanged)

    def _stt(self):
        """O `SpeechToTextService` da sessão atual, ou `None` quando não há
        sessão/voz. Sempre lido na hora — a sessão troca a cada login."""
        app = self._app
        voice = getattr(app, "voice", None) if app is not None else None
        return getattr(voice, "stt", None) if voice is not None else None

    def _apply_preferred_microphone(self) -> None:
        """Aplica o microfone salvo da conta ao abrir a sessão."""
        stt = self._stt()
        if stt is None:
            return
        preferred = self._account.preferred_microphone_key()
        if preferred:
            stt.select_device(preferred)

    def _refresh_audio_devices(self) -> None:
        stt = self._stt()
        devices = stt.available_devices() if stt is not None else []
        current = stt.current_device if stt is not None else None
        self._audio_devices = [
            {
                "key": device.key,
                "label": device.label,
                "hostApi": device.host_api,
                "isSystemDefault": device.is_system_default,
                "isCurrent": bool(current is not None and current.key == device.key),
            }
            for device in devices
        ]
        self._selected_microphone_key = current.key if current is not None else ""
        self._microphone_fell_back = bool(stt is not None and stt.device_fell_back)
        engine = getattr(stt, "engine", None)
        self._set_property(
            "_stt_engine", engine.label if engine is not None else "—", self.sttEngineChanged
        )
        self.audioDevicesChanged.emit()

    # ------------------------------------------------------------------
    # Properties/Slots — Voice Model Manager (v0.9)
    # ------------------------------------------------------------------

    @Property(bool, notify=voiceModelInstalledChanged)
    def voiceModelInstalled(self) -> bool:
        return self._voice_models.is_installed

    @Property("QVariant", constant=True)
    def voiceModelInfo(self):
        info = self._voice_models.info()
        return {
            "name": info.name,
            "language": info.language,
            "approximateSizeBytes": info.approximate_size_bytes,
            "license": info.license,
            "source": info.source,
        }

    @Property(bool, notify=voiceModelDownloadActiveChanged)
    def voiceModelDownloadActive(self) -> bool:
        return self._voice_model_download_active

    @Property("QVariant", notify=voiceModelDownloadProgressChanged)
    def voiceModelDownloadProgress(self):
        return {"downloaded": self._voice_model_downloaded_bytes, "total": self._voice_model_total_bytes}

    @Slot()
    def downloadVoiceModel(self) -> None:
        if self._voice_model_download_active:
            return
        asyncio.ensure_future(self._download_voice_model())

    async def _download_voice_model(self) -> None:
        self._voice_model_download_active = True
        self.voiceModelDownloadActiveChanged.emit()
        self._voice_model_downloaded_bytes = 0
        self._voice_model_total_bytes = 0
        self.voiceModelDownloadProgressChanged.emit()

        loop = asyncio.get_running_loop()

        def on_progress(downloaded: int, total: int) -> None:
            loop.call_soon_threadsafe(self._update_download_progress, downloaded, total)

        try:
            await loop.run_in_executor(
                None, lambda: self._voice_models.download_and_install(on_progress=on_progress)
            )
        except ModelDownloadCancelled:
            logger.info("Download do modelo de voz cancelado.")
        except ModelDownloadError as exc:
            self.voiceErrorRaised.emit(str(exc))
        else:
            self.voiceModelInstalledChanged.emit()
            # O provider de STT da sessão atual (se houver) foi construído
            # antes do modelo existir — troca em vez de exigir logout/login
            # para o microfone funcionar depois de instalar.
            if self._app is not None:
                self._app.voice.stt = create_stt_service(self._account.settings)
                self._refresh_status()
        finally:
            self._voice_model_download_active = False
            self.voiceModelDownloadActiveChanged.emit()

    @Slot()
    def cancelVoiceModelDownload(self) -> None:
        self._voice_models.cancel_download()

    def _update_download_progress(self, downloaded: int, total: int) -> None:
        self._voice_model_downloaded_bytes = downloaded
        self._voice_model_total_bytes = total
        self.voiceModelDownloadProgressChanged.emit()

    # ------------------------------------------------------------------
    # Slots — chat/voz/permissões (chamados pelo QML, requerem sessão ativa)
    # ------------------------------------------------------------------

    @Slot(str)
    def sendMessage(self, text: str) -> None:
        if self._app is not None:
            asyncio.ensure_future(self._send(text))

    async def _send(self, text: str) -> None:
        response = await self._app.send_message(text)
        # JARVIS_BUSY é a única resposta que não passa pelo event stream (é
        # uma rejeição limpa antes de qualquer "response" começar) — o
        # Bridge precisa tratar isso a partir do valor de retorno.
        if response is not None and response.status is ResponseStatus.ERROR and response.error is not None:
            if response.error.code is AppErrorCode.JARVIS_BUSY:
                self.busyRejected.emit(response.error.message)
            elif response.error.code is AppErrorCode.INTERNAL_ERROR:
                self.internalErrorRaised.emit(response.error.message)

    @Slot(str, result=bool)
    def copyToClipboard(self, text: str) -> bool:
        """Copia texto para a área de transferência (v1.2 — botão Copy).

        Fica no Bridge, e não em QML, para ser testável sem GUI e para o QML
        não precisar conhecer a API de clipboard do Qt. O QML passa o papel
        `content` (RAW), nunca o `markdown` renderizado nem "YOU"/"JARVIS"/
        horário — o usuário copia exatamente o texto da mensagem."""
        if not text:
            return False
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:  # pragma: no cover - sem sessão gráfica
            return False
        clipboard.setText(text)
        return True

    @Slot(str)
    def regenerateMessage(self, message_id: str) -> None:
        if self._app is not None:
            asyncio.ensure_future(self._regenerate(message_id))

    async def _regenerate(self, message_id: str) -> None:
        response = await self._app.regenerate(message_id)
        if response is None:
            return
        if response.status is ResponseStatus.ERROR and response.error is not None:
            if response.error.code is AppErrorCode.JARVIS_BUSY:
                self.busyRejected.emit(response.error.message)
            else:
                self.internalErrorRaised.emit(response.error.message)

    @Slot()
    def cancelCurrentRequest(self) -> None:
        if self._app is not None:
            asyncio.ensure_future(self._app.cancel_current_request())

    @Slot()
    def startNewConversation(self) -> None:
        asyncio.ensure_future(self._start_new_conversation())

    async def _start_new_conversation(self) -> None:
        await self._account.start_new_conversation()
        self._sync_messages()
        self._refresh_conversations()

    @Slot(str)
    def openConversation(self, conversation_id: str) -> None:
        asyncio.ensure_future(self._open_conversation(conversation_id))

    async def _open_conversation(self, conversation_id: str) -> None:
        opened = await self._account.open_conversation(conversation_id)
        if opened:
            self._sync_messages()
            self.currentConversationIdChanged.emit()

    @Slot(str, result="QVariant")
    def searchConversations(self, query: str):
        return [self._summary_to_dict(summary) for summary in self._account.search_conversations(query)]

    @Slot(str, str)
    def renameConversation(self, conversation_id: str, title: str) -> None:
        self._account.rename_conversation(conversation_id, title)
        self._refresh_conversations()

    @Slot(str)
    def deleteConversation(self, conversation_id: str) -> None:
        asyncio.ensure_future(self._delete_conversation(conversation_id))

    async def _delete_conversation(self, conversation_id: str) -> None:
        await self._account.delete_conversation(conversation_id)
        self._sync_messages()
        self._refresh_conversations()

    @Slot()
    def toggleListening(self) -> None:
        """Push-to-talk por clique: clique liga, clique de novo desliga e
        transcreve. Um único slot, chamado pelo MicButton — o estado atual
        (`jarvisState`) já diz qual dos dois deve acontecer."""
        if self._app is None:
            return
        if self._state == "listening":
            asyncio.ensure_future(self._app.stop_listening_and_transcribe())
        else:
            asyncio.ensure_future(self._start_listening())

    async def _start_listening(self) -> None:
        error = await self._app.start_listening()
        if error is not None:
            self.voiceErrorRaised.emit(error.message)

    @Slot()
    def cancelListening(self) -> None:
        if self._app is not None:
            asyncio.ensure_future(self._app.cancel_listening())

    @Slot()
    def stopSpeaking(self) -> None:
        if self._app is not None:
            asyncio.ensure_future(self._app.stop_speaking())

    @Slot(bool)
    def setVoiceOutputEnabled(self, enabled: bool) -> None:
        if self._app is None:
            return
        self._app.set_voice_output_enabled(enabled)
        self._set_property("_voice_output_enabled", enabled, self.voiceOutputEnabledChanged)

    @Slot(str)
    def approvePermission(self, request_id: str) -> None:
        if self._app is not None:
            self._app.permissions.approve(request_id)

    @Slot(str)
    def denyPermission(self, request_id: str) -> None:
        if self._app is not None:
            self._app.permissions.deny(request_id)

    @Slot()
    def requestShutdown(self) -> None:
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.ensure_future(self._shutdown())

    async def _shutdown(self) -> None:
        logger.info("HUD encerrando.")
        await self._leave_session()
        # `shutdown()` (não `logout()`): encerra a Application Layer da
        # sessão atual mas preserva o token local — a próxima execução
        # continua logada, a menos que o usuário tenha feito logout.
        await self._account.shutdown()
        # `self._app` já é `None` neste ponto (AccountManager derrubou a
        # sessão) — `_refresh_status()` não tem nada para ler, então o
        # estado visível da sessão precisa ser resetado manualmente aqui
        # (mesmo raciocínio de `_logout()`).
        self._set_property("_running", False, self.runningChanged)
        self._set_property("_busy", False, self.busyChanged)
        self._set_property("_ai_session_active", False, self.aiSessionActiveChanged)
        self._can_close = True
        self.canCloseChanged.emit()
        qt_app = QCoreApplication.instance()
        if qt_app is not None:
            qt_app.quit()

    @Slot(str)
    def simulateState(self, name: str) -> None:
        """Somente para desenvolvimento (`devMode`) — simula estados visuais
        do Core sem chamada real de IA. Nunca ativo para o usuário final."""
        if not self._dev_mode:
            return
        name = name.lower()
        if name in ("idle", "thinking", "error", "waiting_confirmation", "listening", "speaking", "processing_speech"):
            self._set_property("_state", name, self.stateChanged)
            if name == "listening":
                self._set_property("_voice_level", 0.6, self.voiceLevelChanged)
        elif name == "permission" and self._app is not None:
            self._app.permissions.request(
                "restart_dev_server",
                "Reiniciar o servidor de desenvolvimento",
                RiskLevel.ACTION,
            )
