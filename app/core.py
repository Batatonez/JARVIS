"""JarvisCore: fachada que conecta configuração, serviços e orquestração.

    main
      |
    JarvisCore  ---- Orchestrator
      |
    services (MemoryService, AIService, EventBus)

`app/terminal.py` (ou, futuramente, uma interface gráfica) fala apenas com
`JarvisCore` — não conhece serviços internos nem o orquestrador diretamente.
"""

import logging

from app.orchestrator import Orchestrator
from app.state import JarvisState
from config.settings import Settings
from config.settings import settings as default_settings
from services.ai_service import AIService, UnavailableAIService
from services.event_bus import EventBus
from services.memory_service import MemoryService

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
        self.ai_service = ai_service or UnavailableAIService()
        self.state = JarvisState.IDLE
        self.orchestrator = Orchestrator(self)

    def start(self) -> None:
        logger.info("JARVIS Core iniciado (v%s)", self.settings.core_version)
        self.event_bus.emit("jarvis.started")

    def stop(self) -> None:
        logger.info("JARVIS Core encerrado")
        self.event_bus.emit("jarvis.stopped")

    def set_state(self, new_state: JarvisState) -> None:
        if new_state is self.state:
            return
        old_state = self.state
        self.state = new_state
        self.event_bus.emit("state.changed", old=old_state, new=new_state)

    def handle_input(self, text: str) -> str:
        return self.orchestrator.handle(text)
