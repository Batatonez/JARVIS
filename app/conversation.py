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
