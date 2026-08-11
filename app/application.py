"""JarvisApplication: a fronteira estável entre o JARVIS Core e qualquer
frontend (terminal hoje; HUD, voz, etc. no futuro).

    Frontend (terminal, futuro HUD, futura voz)
        |
    JarvisApplication   <-- este módulo
        |              \\
    JarvisCore       Event stream (subscribe/events)
        |
    Orchestrator
        |
    AIService / MemoryService / EventBus

Um frontend nunca deve importar `services.claude_agent_provider`, chamar
`memory_service.get_profile()` ou falar com `Orchestrator` diretamente — tudo
isso fica atrás desta fachada. `JarvisApplication` não conhece nada
específico do Claude Agent SDK nem lê arquivos: delega tudo a `JarvisCore`.

Política de concorrência: **uma requisição ativa por conversa**. Se
`send_message()` for chamado enquanto outra requisição está em andamento,
ela é rejeitada de forma limpa (`AppErrorCode.JARVIS_BUSY`) em vez de
enfileirada — mais simples e previsível para uma futura GUI (estado "ocupado"
explícito) do que uma fila implícita.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from enum import Enum

from app.commands import CommandRegistry
from app.conversation import Conversation
from app.core import JarvisCore
from app.models import (
    AppError,
    AppErrorCode,
    AppEvent,
    AssistantResponse,
    Message,
    MessageRole,
    ResponseStatus,
    StatusSnapshot,
)
from app.permissions import PermissionService
from app.state import JarvisState
from app.status import build_status_snapshot
from services.stt_service import STTUnavailableError
from services.tts_service import TTSUnavailableError
from services.voice_service import VoiceService

logger = logging.getLogger(__name__)


class JarvisApplication:
    def __init__(self, core: JarvisCore, *, voice_service: VoiceService | None = None) -> None:
        self._core = core
        self._conversation = Conversation(max_messages=core.settings.max_conversation_messages)
        self._current_request_task: asyncio.Task[str] | None = None
        self._subscribers: list[asyncio.Queue[AppEvent]] = []
        self.permissions = PermissionService(core.event_bus)
        # VoiceService vive aqui, não em JarvisCore — é um frontend-facing
        # capability opcional (push-to-talk, fala), não parte do raciocínio
        # do Core/Orchestrator (ver docs/architecture.md, Voice Foundation).
        self.voice = voice_service or VoiceService(core.settings, core.event_bus)

        for event_name in (
            "jarvis.started",
            "jarvis.stopped",
            "state.changed",
            "ai.connected",
            "ai.disconnected",
            "permission.requested",
            "permission.resolved",
            "voice.listening.started",
            "voice.listening.stopped",
            "voice.transcription.started",
            "voice.transcription.completed",
            "voice.transcription.failed",
            "voice.speaking.started",
            "voice.speaking.stopped",
            "voice.speaking.failed",
            "voice.level",
        ):
            core.event_bus.subscribe(event_name, self._make_relay(event_name))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._core.is_running:
            return  # idempotente
        logger.info("Application iniciada.")
        await self._core.start()
        self._emit("conversation.started")

    async def stop(self) -> None:
        if not self._core.is_running:
            return  # idempotente
        logger.info("Application encerrando.")
        self._emit("jarvis.stopping")
        await self.cancel_current_request()
        await self.voice.shutdown()
        await self._core.stop()

    # ------------------------------------------------------------------
    # Mensagens
    # ------------------------------------------------------------------

    async def send_message(self, text: str) -> AssistantResponse | None:
        """Envia uma mensagem conversacional. Retorna `None` para entrada
        vazia (nada a fazer). Nunca levanta exceção para falhas esperadas
        (IA indisponível, ocupado, cancelado) — essas viram `AssistantResponse`
        com `status`/`error` estruturados, para o frontend nunca precisar
        analisar texto humano."""
        text = text.strip()
        if not text:
            return None

        if self._current_request_task is not None and not self._current_request_task.done():
            logger.info("Mensagem rejeitada: JARVIS ocupado com outra requisição.")
            return AssistantResponse(
                status=ResponseStatus.ERROR,
                error=AppError(AppErrorCode.JARVIS_BUSY, "O JARVIS está processando outra mensagem. Aguarde."),
            )
        if self.voice.listening or self.voice.speaking:
            logger.info("Mensagem rejeitada: voz em uso (escutando ou falando).")
            return AssistantResponse(
                status=ResponseStatus.ERROR,
                error=AppError(AppErrorCode.JARVIS_BUSY, "O JARVIS está com o microfone/voz em uso no momento."),
            )

        self._conversation.add(MessageRole.USER, text)
        self._emit("message.received", {"content": text})

        if not self._core.ai_service.is_available():
            logger.info("Requisição rejeitada: serviço de IA não configurado.")
            self._emit("response.failed", {"reason": "ai_unavailable"})
            return AssistantResponse(
                status=ResponseStatus.ERROR,
                error=AppError(AppErrorCode.AI_UNAVAILABLE, "O serviço de inteligência ainda não está configurado."),
            )

        logger.info("Requisição de IA iniciada.")
        self._emit("response.started")

        # Captura falhas que o Orchestrator já trata internamente (ex.: erro
        # em runtime do provider) sem precisar analisar o texto de resposta.
        failure: dict[str, str] = {}

        def _capture_failure(**payload: object) -> None:
            failure["error"] = str(payload.get("error", ""))

        self._core.event_bus.subscribe("ai.request.failed", _capture_failure)
        task: asyncio.Task[str] = asyncio.ensure_future(self._core.handle_input(text))
        self._current_request_task = task
        try:
            raw_reply = await task
        except asyncio.CancelledError:
            logger.info("Requisição de IA cancelada.")
            self._emit("response.failed", {"reason": "cancelled"})
            return AssistantResponse(status=ResponseStatus.CANCELLED)
        except Exception:
            logger.exception("Erro interno inesperado ao processar mensagem.")
            self._emit("response.failed", {"reason": "internal_error"})
            return AssistantResponse(
                status=ResponseStatus.ERROR,
                error=AppError(AppErrorCode.INTERNAL_ERROR, "Ocorreu um erro interno inesperado."),
            )
        else:
            if failure:
                self._emit("response.failed", {"reason": "ai_error"})
                return AssistantResponse(
                    status=ResponseStatus.ERROR,
                    error=AppError(AppErrorCode.AI_UNAVAILABLE, failure["error"]),
                )
            assistant_message = self._conversation.add(MessageRole.ASSISTANT, raw_reply)
            self._emit("response.completed", {"message_id": assistant_message.id})
            if self.voice.voice_output_enabled and self.voice.tts_ready:
                # Fala a resposta real já produzida — nunca inventa texto.
                # Fire-and-forget: não atrasa o retorno de send_message().
                asyncio.ensure_future(self.speak(raw_reply))
            return AssistantResponse(
                status=ResponseStatus.SUCCESS, message_id=assistant_message.id, content=raw_reply
            )
        finally:
            self._core.event_bus.unsubscribe("ai.request.failed", _capture_failure)
            self._current_request_task = None

    async def cancel_current_request(self) -> bool:
        """Cancela a requisição em andamento, se houver. Usa
        `asyncio.Task.cancel()` (nunca mata processos arbitrariamente).
        Retorna `False` se não havia nada para cancelar."""
        task = self._current_request_task
        if task is None or task.done():
            return False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return True

    async def new_conversation(self) -> None:
        """Limpa o histórico runtime e, se houver IA configurada, reinicia a
        sessão do Agent SDK para garantir contexto limpo. NÃO apaga
        `memory/`, `profile.md` ou `preferences.md` — só a conversa atual."""
        await self.cancel_current_request()
        self._conversation.clear()
        self._emit("conversation.cleared")
        await self._core.restart_ai_session()
        self._emit("conversation.started")
        logger.info("Nova conversa iniciada.")

    # ------------------------------------------------------------------
    # Voz (v0.7 — push-to-talk + fala; ver docs/architecture.md)
    #
    # O resultado da transcrição NUNCA é enviado à IA automaticamente e
    # NUNCA aciona nenhuma ação/ferramenta diretamente — chega só como texto
    # via `voice.transcription.completed`, para o frontend decidir (ver
    # `docs/architecture.md`, seção "Voz e permissões"). Erros de voz nunca
    # levantam exceção para quem chama: viram eventos `voice.*.failed` (o
    # `AppError` de retorno cobre só as rejeições síncronas, como microfone
    # ausente ou JARVIS ocupado, que acontecem antes de qualquer evento).
    # ------------------------------------------------------------------

    async def start_listening(self) -> AppError | None:
        """Começa a capturar áudio do microfone (push-to-talk). Retorna um
        `AppError` estruturado se não for possível começar; `None` em
        sucesso (o estado passa a `LISTENING`, visível via `state.changed`)."""
        if self._current_request_task is not None and not self._current_request_task.done():
            return AppError(AppErrorCode.JARVIS_BUSY, "O JARVIS está processando outra mensagem. Aguarde.")
        if self.voice.listening or self.voice.speaking:
            return AppError(AppErrorCode.JARVIS_BUSY, "O microfone/voz já está em uso.")
        if not self.voice.voice_available:
            return AppError(
                AppErrorCode.MICROPHONE_UNAVAILABLE, "Microfone ou reconhecimento de fala não está disponível."
            )

        self._core.set_state(JarvisState.LISTENING)
        try:
            await self.voice.start_listening()
        except STTUnavailableError as exc:
            self._core.set_state(JarvisState.IDLE)
            return AppError(AppErrorCode.MICROPHONE_UNAVAILABLE, str(exc))
        return None

    async def stop_listening_and_transcribe(self) -> None:
        """Para a captura e tenta transcrever. O texto (ou erro) chega via
        eventos (`voice.transcription.completed`/`.failed`), não pelo
        retorno — assim qualquer frontend assistindo aos eventos vê o
        resultado, não só quem chamou este método."""
        if not self.voice.listening:
            return
        self._core.set_state(JarvisState.PROCESSING_SPEECH)
        try:
            await self.voice.stop_and_transcribe()
        except STTUnavailableError:
            pass  # já relatado via voice.transcription.failed
        finally:
            self._core.set_state(JarvisState.IDLE)

    async def cancel_listening(self) -> None:
        """Cancela a captura em andamento sem transcrever (descarta o áudio)."""
        if not self.voice.listening:
            return
        await self.voice.cancel_listening()
        self._core.set_state(JarvisState.IDLE)

    async def speak(self, text: str) -> AppError | None:
        """Fala um texto via TTS (usado automaticamente após uma resposta da
        IA quando `voice_output_enabled`, e disponível para o frontend
        acionar diretamente). Retorna um `AppError` se a síntese de voz não
        estiver disponível ou falhar."""
        if not self.voice.tts_ready:
            return AppError(AppErrorCode.TTS_UNAVAILABLE, "Síntese de voz não está disponível.")
        self._core.set_state(JarvisState.SPEAKING)
        try:
            await self.voice.speak(text)
        except TTSUnavailableError as exc:
            self._core.set_state(JarvisState.IDLE)
            return AppError(AppErrorCode.TTS_UNAVAILABLE, str(exc))
        self._core.set_state(JarvisState.IDLE)
        return None

    async def stop_speaking(self) -> None:
        """Interrompe a fala em andamento (controle STOP/mute do HUD)."""
        await self.voice.stop_speaking()
        if self._core.state is JarvisState.SPEAKING:
            self._core.set_state(JarvisState.IDLE)

    def set_voice_output_enabled(self, enabled: bool) -> None:
        """Liga/desliga a fala automática da resposta da IA. Desligado por
        padrão (`Settings.voice_output_enabled`); o HUD chama isto a partir
        do controle "VOICE OUTPUT ON/OFF"."""
        self.voice.voice_output_enabled = enabled

    # ------------------------------------------------------------------
    # Terminal (primeiro frontend)
    # ------------------------------------------------------------------

    async def handle_input(self, text: str) -> str | None:
        """Ponto de entrada usado pelo terminal (`app/terminal.py`): decide
        comando local vs. mensagem conversacional e devolve texto já
        formatado para exibição, ou `None` para entrada vazia."""
        stripped = text.strip()
        if not stripped:
            return None

        if stripped.startswith("/"):
            name = stripped[1:].split(None, 1)[0].lower()
            if name in ("new", "reset"):
                await self.new_conversation()
                return "Nova conversa iniciada. Histórico da sessão anterior foi limpo."

        if CommandRegistry.is_command(stripped):
            return await self._core.handle_input(text)

        response = await self.send_message(text)
        if response is None:
            return None
        if response.status is ResponseStatus.SUCCESS:
            return f"JARVIS: {response.content}"
        if response.status is ResponseStatus.CANCELLED:
            return "JARVIS: (resposta cancelada)"
        return f"JARVIS: {response.error.message}"

    # ------------------------------------------------------------------
    # Estado / histórico
    # ------------------------------------------------------------------

    def get_status(self) -> StatusSnapshot:
        busy = self._current_request_task is not None and not self._current_request_task.done()
        return build_status_snapshot(
            self._core, busy=busy, active_conversation=bool(self._conversation.messages), voice=self.voice
        )

    def get_messages(self) -> list[Message]:
        return self._conversation.messages

    def memory_status(self) -> tuple[bool, bool]:
        """(perfil carregado, preferências carregadas) — para um frontend
        mostrar isso sem tocar `MemoryService` diretamente."""
        return (
            self._core.memory_service.is_profile_available(),
            self._core.memory_service.is_preferences_available(),
        )

    # ------------------------------------------------------------------
    # Eventos
    # ------------------------------------------------------------------

    def subscribe(self) -> "asyncio.Queue[AppEvent]":
        queue: asyncio.Queue[AppEvent] = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[AppEvent]") -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    async def events(self) -> AsyncIterator[AppEvent]:
        """`async for event in application.events(): ...` — conveniência
        sobre `subscribe()`/`unsubscribe()` para consumidores assíncronos."""
        queue = self.subscribe()
        try:
            while True:
                yield await queue.get()
        finally:
            self.unsubscribe(queue)

    def _emit(self, event_type: str, payload: dict | None = None) -> None:
        event = AppEvent(type=event_type, timestamp=datetime.now(timezone.utc), payload=payload or {})
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    def _make_relay(self, event_name: str):
        def _relay(**payload: object) -> None:
            clean_payload = {k: (v.value if isinstance(v, Enum) else v) for k, v in payload.items()}
            self._emit(event_name, clean_payload)

        return _relay
