"""Fakes/dublês usados pelos testes de IA — nenhum faz chamada real a nenhuma
API ou processo externo (Claude Agent SDK, Anthropic, ou qualquer outro).
"""

from services.ai_service import AIService


class FakeAIService(AIService):
    """AIService genérico e controlável por teste — usado para provar que o
    Orchestrator/Core lidam com qualquer AIService (não conhecem Claude)."""

    def __init__(self, *, available: bool = True, reply: str = "ok", ask_error: Exception | None = None):
        self._available = available
        self._reply = reply
        self._ask_error = ask_error
        self._session_active = False
        self.started = False
        self.closed = False
        self.received_memory_context: str | None = None
        self.asked_messages: list[str] = []

    def is_available(self) -> bool:
        return self._available

    @property
    def session_active(self) -> bool:
        return self._session_active

    @property
    def backend_name(self) -> str:
        return "Fake"

    async def start(self, *, memory_context: str = "") -> None:
        self.started = True
        self.received_memory_context = memory_context
        self._session_active = True

    async def ask(self, message: str) -> str:
        self.asked_messages.append(message)
        if self._ask_error:
            raise self._ask_error
        return self._reply

    async def close(self) -> None:
        self.closed = True
        self._session_active = False


class FakeClaudeSDKClient:
    """Substitui `claude_agent_sdk.ClaudeSDKClient` nos testes do
    ClaudeAgentProvider. `responses` é uma lista de listas de mensagens: uma
    lista de mensagens por chamada a `query()`, na ordem em que ocorrerem."""

    def __init__(
        self,
        *,
        responses: list[list[object]] | None = None,
        connect_error: Exception | None = None,
        ask_error: Exception | None = None,
    ):
        self._responses = list(responses or [])
        self._connect_error = connect_error
        self._ask_error = ask_error
        self.connected = False
        self.disconnected = False
        self.queries: list[str] = []

    async def connect(self, prompt=None) -> None:
        if self._connect_error:
            raise self._connect_error
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True
        self.connected = False

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self.queries.append(prompt)
        if self._ask_error:
            raise self._ask_error

    async def receive_response(self):
        messages = self._responses.pop(0) if self._responses else []
        for message in messages:
            yield message
