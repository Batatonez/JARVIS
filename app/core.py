"""JarvisCore: fachada que conecta configuração, serviços e orquestração.

    main
      |
    JarvisCore  ---- Orchestrator
      |
    services (MemoryService, AIService, EventBus)

`app/terminal.py` (ou, futuramente, uma interface gráfica) fala apenas com
`JarvisCore` — não conhece serviços internos nem o orquestrador diretamente,
e não conhece nada específico do Claude Agent SDK.

O lifecycle da IA é explícito: `start()` monta um contexto de memória
controlado (perfil + preferências) e conecta a sessão uma única vez;
`stop()` encerra essa sessão. Nenhuma sessão é recriada por mensagem.
"""

import logging

from app.orchestrator import Orchestrator
from app.state import JarvisState
from config.settings import Settings
from config.settings import settings as default_settings
from services.ai_service import AIService, AIServiceUnavailableError, UnavailableAIService, create_ai_service
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

    async def start(self) -> None:
        logger.info("JARVIS Core iniciado (v%s)", self.settings.core_version)
        self.event_bus.emit("jarvis.started")

        memory_context = self._build_memory_context()
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

    async def stop(self) -> None:
        await self.ai_service.close()
        self.event_bus.emit("ai.disconnected")
        logger.info("JARVIS Core encerrado")
        self.event_bus.emit("jarvis.stopped")

    def set_state(self, new_state: JarvisState) -> None:
        if new_state is self.state:
            return
        old_state = self.state
        self.state = new_state
        self.event_bus.emit("state.changed", old=old_state, new=new_state)

    async def handle_input(self, text: str) -> str:
        return await self.orchestrator.handle(text)

    def _build_memory_context(self) -> str:
        """Monta o contexto de memória controlado que vai para a IA: só
        perfil e preferências, lidos via MemoryService (nunca acesso direto
        a arquivos por parte da camada de IA)."""
        parts: list[str] = []
        try:
            parts.append("Perfil do usuário:\n" + self.memory_service.get_profile())
        except MemoryUnavailableError:
            pass
        try:
            parts.append("Preferências do usuário:\n" + self.memory_service.get_preferences())
        except MemoryUnavailableError:
            pass
        return "\n\n".join(parts)
