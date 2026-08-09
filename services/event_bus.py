"""Event bus simples e síncrono, em memória.

Permite que o Core emita eventos (ex.: `jarvis.started`, `state.changed`)
sem precisar conhecer quem vai consumi-los — no futuro, o HUD e outros
componentes poderão se inscrever nesses eventos. Sem filas, sem rede: apenas
uma lista de handlers por nome de evento, chamados na hora.
"""

import logging
from collections import defaultdict
from collections.abc import Callable

logger = logging.getLogger(__name__)

EventHandler = Callable[..., None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        self._subscribers[event_name].append(handler)

    def unsubscribe(self, event_name: str, handler: EventHandler) -> None:
        handlers = self._subscribers.get(event_name)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def emit(self, event_name: str, **payload: object) -> None:
        for handler in list(self._subscribers.get(event_name, ())):
            try:
                handler(**payload)
            except Exception:
                logger.exception(
                    "Erro ao processar evento '%s' no handler %r", event_name, handler
                )
