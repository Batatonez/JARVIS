"""JarvisBridge: a única ponte entre QML e `JarvisApplication`.

Não contém lógica de domínio — só traduz a Application Layer para
Properties/Signals/Slots do Qt, e vice-versa. O QML nunca importa
`app.application`, `app.core` ou qualquer coisa de `services/` — só conhece
este Bridge.

Atualização de estado é orientada a eventos: o Bridge consome
`JarvisApplication.events()` uma vez (`_consume_events`, um único
`async for`) e atualiza Properties Qt quando algo muda de verdade — nunca há
polling nem `get_status()` chamado em loop/timer.
"""

import asyncio
import contextlib
import logging

from PySide6.QtCore import Property, QCoreApplication, QObject, Signal, Slot

from app.application import JarvisApplication
from app.models import AppErrorCode, AppEvent, ResponseStatus, RiskLevel
from frontend.message_model import MessageListModel

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
    }
)
# Eventos que implicam ressincronizar o histórico de mensagens.
_MESSAGE_EVENTS = frozenset(
    {"message.received", "response.completed", "response.failed", "conversation.started", "conversation.cleared"}
)


class JarvisBridge(QObject):
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

    busyRejected = Signal(str)
    internalErrorRaised = Signal(str)

    def __init__(self, application: JarvisApplication, *, dev_mode: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._app = application
        self._dev_mode = dev_mode
        self._message_model = MessageListModel(self)

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

        self._event_queue: asyncio.Queue | None = None
        self._event_task: asyncio.Task | None = None
        self._shutdown_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle (chamado pelo launcher, não pelo QML)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        await self._app.start()
        self._refresh_status()
        self._sync_messages()
        # `subscribe()` é síncrono e registra a fila imediatamente — ao
        # contrário de `events()` (async generator), que só se inscreve
        # quando a task criada por `ensure_future` ganha sua primeira
        # execução. Se inscrevêssemos via `events()`, qualquer evento
        # emitido entre `start()` retornar e essa primeira execução seria
        # perdido (ninguém ainda estaria ouvindo).
        self._event_queue = self._app.subscribe()
        self._event_task = asyncio.ensure_future(self._consume_events())

    async def _consume_events(self) -> None:
        assert self._event_queue is not None
        try:
            while True:
                event = await self._event_queue.get()
                try:
                    self._handle_event(event)
                except Exception:
                    logger.exception("Erro ao processar evento '%s' no Bridge.", event.type)
        finally:
            self._app.unsubscribe(self._event_queue)

    def _handle_event(self, event: AppEvent) -> None:
        if event.type in _STATUS_EVENTS:
            self._refresh_status()
        if event.type in _MESSAGE_EVENTS:
            self._sync_messages()
        if event.type == "permission.requested":
            self._set_pending_permission()
        elif event.type == "permission.resolved":
            self._clear_pending_permission()

    # ------------------------------------------------------------------
    # Sincronização de estado (sempre disparada por evento, nunca por timer)
    # ------------------------------------------------------------------

    def _refresh_status(self) -> None:
        snapshot = self._app.get_status()
        self._set_property("_state", snapshot.state, self.stateChanged)
        self._set_property("_running", snapshot.running, self.runningChanged)
        self._set_property("_busy", snapshot.busy, self.busyChanged)
        self._set_property("_memory_available", snapshot.memory_available, self.memoryAvailableChanged)
        self._set_property("_ai_configured", snapshot.ai_configured, self.aiConfiguredChanged)
        self._set_property("_ai_backend", snapshot.ai_backend, self.aiBackendChanged)
        self._set_property("_ai_session_active", snapshot.ai_session_active, self.aiSessionActiveChanged)
        self._set_property("_active_conversation", snapshot.active_conversation, self.activeConversationChanged)

    def _set_property(self, attr: str, value: object, signal: Signal) -> None:
        if getattr(self, attr) != value:
            setattr(self, attr, value)
            signal.emit()

    def _sync_messages(self) -> None:
        self._message_model.sync(self._app.get_messages())

    def _set_pending_permission(self) -> None:
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

    # ------------------------------------------------------------------
    # Properties Qt (somente leitura pelo QML)
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

    @Property(bool, constant=True)
    def devMode(self) -> bool:
        return self._dev_mode

    @Property(QObject, constant=True)
    def messages(self) -> MessageListModel:
        return self._message_model

    # ------------------------------------------------------------------
    # Slots (chamados pelo QML)
    # ------------------------------------------------------------------

    @Slot(str)
    def sendMessage(self, text: str) -> None:
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
        asyncio.ensure_future(self._app.cancel_current_request())

    @Slot()
    def newConversation(self) -> None:
        asyncio.ensure_future(self._app.new_conversation())

    @Slot(str)
    def approvePermission(self, request_id: str) -> None:
        self._app.permissions.approve(request_id)

    @Slot(str)
    def denyPermission(self, request_id: str) -> None:
        self._app.permissions.deny(request_id)

    @Slot()
    def requestShutdown(self) -> None:
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.ensure_future(self._shutdown())

    async def _shutdown(self) -> None:
        logger.info("HUD encerrando.")
        if self._event_task is not None:
            self._event_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._event_task
            self._event_task = None
        await self._app.stop()
        # O consumidor de eventos já foi encerrado acima, então os eventos
        # emitidos por `stop()` (jarvis.stopped, ai.disconnected) não seriam
        # vistos por ninguém — sincroniza o status final manualmente.
        self._refresh_status()
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
        if name in ("idle", "thinking", "error"):
            self._set_property("_state", name, self.stateChanged)
        elif name == "permission":
            self._app.permissions.request(
                "restart_dev_server",
                "Reiniciar o servidor de desenvolvimento",
                RiskLevel.ACTION,
            )
