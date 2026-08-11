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

from app.account_manager import AccountManager, InvalidCredentialsError, UsernameAlreadyExistsError
from app.models import AppErrorCode, AppEvent, ResponseStatus, RiskLevel
from frontend.message_model import MessageListModel
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
        "response.failed",
        "conversation.started",
        "conversation.cleared",
        "conversation.loaded",
    }
)
# Eventos que podem ter criado/atualizado uma conversa persistida (sidebar).
_CONVERSATION_LIST_EVENTS = _MESSAGE_EVENTS


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
        }

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
    # Slots — contas/sessão
    # ------------------------------------------------------------------

    @Slot(str, str, str)
    def register(self, username: str, display_name: str, password: str) -> None:
        asyncio.ensure_future(self._register(username, display_name, password))

    async def _register(self, username: str, display_name: str, password: str) -> None:
        try:
            user = await self._account.register(username=username, display_name=display_name, password=password)
        except (UsernameAlreadyExistsError, ValueError) as exc:
            self.authErrorRaised.emit(str(exc))
            return
        except Exception:
            logger.exception("Falha inesperada ao criar conta.")
            self.authErrorRaised.emit("Não foi possível criar a conta. Tente novamente.")
            return
        await self._enter_session(user)

    @Slot(str, str)
    def login(self, username: str, password: str) -> None:
        asyncio.ensure_future(self._login(username, password))

    async def _login(self, username: str, password: str) -> None:
        try:
            user = await self._account.login(username=username, password=password)
        except InvalidCredentialsError as exc:
            self.authErrorRaised.emit(str(exc))
            return
        except Exception:
            logger.exception("Falha inesperada ao entrar.")
            self.authErrorRaised.emit("Não foi possível entrar. Tente novamente.")
            return
        await self._enter_session(user)

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
