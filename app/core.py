"""JarvisCore: fachada que conecta configuração, serviços e orquestração.

    JarvisApplication (app/application.py)
      |
    JarvisCore  ---- Orchestrator
      |
    services (MemoryService, AIService, EventBus)

Desde a v0.4, quem fala com `JarvisCore` é `JarvisApplication` — o terminal e
qualquer futuro frontend falam com `JarvisApplication`, não diretamente com o
Core. `JarvisCore` continua não conhecendo serviços internos por fora nem
nada específico do Claude Agent SDK.

O lifecycle da IA é explícito: `start()` monta um contexto de memória
controlado (perfil + preferências) e conecta a sessão uma única vez;
`stop()` encerra essa sessão; `restart_ai_session()` reconecta com contexto
atualizado (usado por "nova conversa"). Nenhuma sessão é recriada por mensagem.
"""

import logging

from app.orchestrator import Orchestrator
from app.state import JarvisState
from config.settings import Settings
from config.settings import settings as default_settings
from services.ai_service import AIService, AIServiceUnavailableError, UnavailableAIService, create_ai_service
from services.context_builder import prepare_memory_context
from services.event_bus import EventBus
from services.memory_service import MemoryService, MemoryUnavailableError

logger = logging.getLogger(__name__)


class JarvisCore:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        event_bus: EventBus | None = None,
        memory_service: MemoryService | None = None,
        ai_service: AIService | None = None,
    ) -> None:
        self.settings = settings or default_settings
        self.event_bus = event_bus or EventBus()
        self.memory_service = memory_service or MemoryService(
            self.settings.profile_path, self.settings.preferences_path
        )
        self.ai_service = ai_service or create_ai_service(self.settings)
        self.state = JarvisState.IDLE
        self.orchestrator = Orchestrator(self)
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        logger.info("JARVIS Core iniciado (v%s)", self.settings.core_version)
        self.event_bus.emit("jarvis.started")
        await self._connect_ai_service()
        self._running = True

    async def stop(self) -> None:
        await self.ai_service.close()
        self.event_bus.emit("ai.disconnected")
        logger.info("JARVIS Core encerrado")
        self.event_bus.emit("jarvis.stopped")
        self._running = False

    async def restart_ai_session(self) -> None:
        """Encerra e reconecta a sessão de IA com um contexto de memória
        atualizado — usado por `JarvisApplication.new_conversation()` para
        garantir uma sessão limpa. Não-op se não houver provider configurado
        (`UnavailableAIService`)."""
        if not self.ai_service.is_available():
            return
        await self.ai_service.close()
        await self._connect_ai_service()

    def set_state(self, new_state: JarvisState) -> None:
        if new_state is self.state:
            return
        old_state = self.state
        self.state = new_state
        self.event_bus.emit("state.changed", old=old_state, new=new_state)

    async def handle_input(self, text: str) -> str:
        return await self.orchestrator.handle(text)

    def build_memory_context(self) -> str:
        """Monta o contexto de memória controlado que vai para a IA: só
        perfil e preferências, lidos via MemoryService (nunca acesso direto
        a arquivos por parte da camada de IA). Público porque também é usado
        por `restart_ai_session()` ao montar uma sessão nova.

        v1.0: o resultado passa por `prepare_memory_context()` — sanitizado
        (segredo que tenha vazado para dentro da memória vira `[REDACTED]`)
        e truncado a `settings.max_memory_context_chars`. Como isso acontece
        AQUI, todo provider recebe a memória já tratada; nenhum deles
        precisa lembrar de fazer isso (ver services/context_builder.py)."""
        parts: list[str] = []
        try:
            parts.append("Perfil do usuário:\n" + self.memory_service.get_profile())
        except MemoryUnavailableError:
            pass
        try:
            parts.append("Preferências do usuário:\n" + self.memory_service.get_preferences())
        except MemoryUnavailableError:
            pass
        return prepare_memory_context(
            "\n\n".join(parts), max_chars=self.settings.max_memory_context_chars
        )

    async def _connect_ai_service(self) -> None:
        memory_context = self.build_memory_context()
        self.event_bus.emit("ai.connecting")
        try:
            await self.ai_service.start(memory_context=memory_context)
        except AIServiceUnavailableError as exc:
            # Provider configurado mas a conexão falhou (ex.: CLI do Agent SDK
            # ausente, erro de autenticação). Cai para o fallback seguro em vez
            # de derrubar o Core — o JARVIS continua funcionando sem IA.
            logger.warning("Falha ao conectar o serviço de IA; usando fallback seguro. %s", exc)
            self.ai_service = UnavailableAIService()

        if self.ai_service.is_available() and self.ai_service.session_active:
            self.event_bus.emit("ai.connected")
        else:
            self.event_bus.emit("ai.disconnected")
