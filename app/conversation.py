"""Application Conversation: histórico de mensagens em memória da sessão atual.

Isto é diferente da sessão interna do Claude Agent SDK (`AIService.session_active`
— ver `services/claude_agent_provider.py`): aquela é a sessão de conversa do
*provider* de IA; esta é a visão do Core/Application sobre a conversa em si.

Existe apenas em RAM: fechar o JARVIS apaga o histórico. Nunca é escrita em
`memory/`, `daily/`, banco de dados ou qualquer arquivo — persistência é
planejada para uma etapa futura, não esta.
"""

from app.models import Message, MessageRole


class Conversation:
    def __init__(self, *, max_messages: int = 200) -> None:
        self._max_messages = max_messages
        self._messages: list[Message] = []

    def add(self, role: MessageRole, content: str) -> Message:
        message = Message(role=role, content=content)
        self._messages.append(message)
        if len(self._messages) > self._max_messages:
            del self._messages[: len(self._messages) - self._max_messages]
        return message

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)  # cópia: quem chama não pode mutar o histórico interno

    def clear(self) -> None:
        self._messages.clear()

    def load(self, messages: list[Message]) -> None:
        """Substitui o histórico em RAM por mensagens já existentes — usado
        ao abrir uma conversa antiga (v0.9, ver `app/account_manager.py`).
        Isto é só a VISÃO da conversa; não recria nem restaura a sessão real
        do Claude Agent SDK (que nem existe nesta versão — sem API key). O
        histórico visual persistido e a sessão de IA são coisas diferentes,
        documentado em `docs/architecture.md`."""
        self._messages = list(messages[-self._max_messages :])
