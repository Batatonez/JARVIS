"""Fonte única do snapshot de status do JARVIS.

Usado tanto pelo comando `/status` (`app/commands.py`, que só tem acesso a
`JarvisCore`) quanto por `JarvisApplication.get_status()` (que também sabe
sobre a conversa em runtime) — para nunca existirem duas implementações
divergentes de "qual é o status atual".
"""

from typing import TYPE_CHECKING

from app.models import StatusSnapshot
from app.state import JarvisState

if TYPE_CHECKING:
    from app.core import JarvisCore


def build_status_snapshot(
    core: "JarvisCore", *, busy: bool | None = None, active_conversation: bool = False
) -> StatusSnapshot:
    """Se `busy` não for informado, aproxima a partir de `core.state` — só
    fica preciso um instante depois de uma requisição ser aceita, porque o
    Orchestrator só muda o estado quando a própria tarefa roda. Quem já sabe
    com certeza (`JarvisApplication`, que controla a task da requisição)
    deve passar `busy` explicitamente para um sinal imediato, sem essa
    janela de atraso."""
    ai = core.ai_service
    memory_available = (
        core.memory_service.is_profile_available() or core.memory_service.is_preferences_available()
    )
    return StatusSnapshot(
        core_version=core.settings.core_version,
        state=core.state.value,
        running=core.is_running,
        busy=busy if busy is not None else core.state is JarvisState.THINKING,
        memory_available=memory_available,
        ai_configured=ai.is_available(),
        ai_backend=ai.backend_name,
        ai_session_active=ai.session_active,
        active_conversation=active_conversation,
    )
