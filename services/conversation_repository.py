"""Repositório de conversas persistidas — toda query é escopada a um
`user_id` (isolamento entre contas é reforçado aqui, não só na UI: mesmo se
alguém chamar `get_conversation()` com o `id` de outro usuário, a query
não devolve nada porque o `WHERE user_id = ?` não bate).

Só persiste o que `app.models.Message`/`ConversationSummary` já descrevem —
nunca um tipo do Claude Agent SDK (o `Orchestrator`/`AIService` já garantem
isso rio acima; aqui só gravamos `role`/`content`/`timestamp`, texto puro).
"""

import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from app.models import ConversationSummary, Message, MessageRole

_DEFAULT_TITLE = "Nova conversa"
_MAX_TITLE_LENGTH = 60


def sanitize_title(title: str) -> str:
    """Limpa um título vindo do usuário ou da IA: sem quebras de linha, sem
    caracteres de controle, espaços colapsados e comprimento limitado
    (item 18). Vazio vira o título padrão."""
    text = " ".join((title or "").split())
    text = "".join(ch for ch in text if ch.isprintable())
    text = text.strip()
    if not text:
        return _DEFAULT_TITLE
    if len(text) <= _MAX_TITLE_LENGTH:
        return text
    return text[:_MAX_TITLE_LENGTH].rstrip() + "…"


def default_title() -> str:
    """Título de um chat recém-criado. Um chat NUNCA nasce com a primeira
    mensagem como nome.

    **Removido na v1.3.2: `derive_title(first_message)`.** Ele existia como
    "fallback" do `ChatTitleService`, com a ideia de que um recorte do texto
    seria melhor que "Nova conversa". Na prática era o oposto: como o título
    automático só roda depois da primeira resposta — e não roda sem IA
    configurada — o recorte era o nome definitivo, e a sidebar ficava cheia
    de "Opa! E aí, tudo bem?".

    A regra agora é a do item 12: melhor "Nova conversa" do que um título
    ruim. Truncar uma pergunta não é resumir um assunto."""
    return _DEFAULT_TITLE


class ConversationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def create_conversation(self, user_id: str, *, title: str = _DEFAULT_TITLE) -> str:
        conversation_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO conversations (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conversation_id, user_id, title, now, now),
        )
        self._conn.commit()
        return conversation_id

    def list_conversations(self, user_id: str) -> list[ConversationSummary]:
        rows = self._conn.execute(
            "SELECT c.id, c.title, c.created_at, c.updated_at, "
            "       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count "
            "FROM conversations c WHERE c.user_id = ? ORDER BY c.updated_at DESC",
            (user_id,),
        ).fetchall()
        return [self._row_to_summary(row) for row in rows]

    def get_conversation(self, conversation_id: str, user_id: str) -> list[Message] | None:
        if not self._owns(conversation_id, user_id):
            return None
        rows = self._conn.execute(
            "SELECT id, role, content, timestamp FROM messages "
            "WHERE conversation_id = ? ORDER BY timestamp ASC",
            (conversation_id,),
        ).fetchall()
        return [
            Message(
                id=row["id"],
                role=MessageRole(row["role"]),
                content=row["content"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
            )
            for row in rows
        ]

    def save_message(self, conversation_id: str, user_id: str, message: Message) -> bool:
        if not self._owns(conversation_id, user_id):
            return False
        self._conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
            (message.id, conversation_id, message.role.value, message.content, message.timestamp.isoformat()),
        )
        self._conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), conversation_id),
        )
        self._conn.commit()
        return True

    def update_message_content(
        self, conversation_id: str, user_id: str, message_id: str, content: str
    ) -> bool:
        """Reescreve o texto de uma mensagem já persistida (v1.2 —
        "Regenerate"). Mantém id/role/timestamp: a resposta regenerada
        ocupa a mesma linha, então o histórico não duplica nem embaralha.

        Como todo método daqui, é escopado por `user_id` — regenerar não
        pode virar um caminho para escrever na conversa de outro usuário."""
        if not self._owns(conversation_id, user_id):
            return False
        cursor = self._conn.execute(
            "UPDATE messages SET content = ? WHERE id = ? AND conversation_id = ?",
            (content, message_id, conversation_id),
        )
        self._conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), conversation_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def rename_conversation(self, conversation_id: str, user_id: str, title: str) -> bool:
        """Rename MANUAL (item 18). Marca `manual_title = 1`, e a partir daí
        nenhum título automático sobrescreve este (item 23)."""
        if not self._owns(conversation_id, user_id):
            return False
        title = sanitize_title(title)
        self._conn.execute(
            "UPDATE conversations SET title = ?, manual_title = 1 WHERE id = ?",
            (title, conversation_id),
        )
        self._conn.commit()
        return True

    def set_automatic_title(self, conversation_id: str, user_id: str, title: str) -> bool:
        """Título gerado pelo `ChatTitleService`. **Recusa** sobrescrever um
        título definido à mão.

        A regra vive aqui, na persistência, e não numa checagem que o
        chamador precisa lembrar de fazer: o `WHERE manual_title = 0` é o que
        torna impossível um caminho novo furar o item 23 por esquecimento."""
        if not self._owns(conversation_id, user_id):
            return False
        cleaned = sanitize_title(title)
        if not cleaned or cleaned == _DEFAULT_TITLE:
            return False
        cursor = self._conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ? AND manual_title = 0",
            (cleaned, conversation_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def has_manual_title(self, conversation_id: str, user_id: str) -> bool:
        if not self._owns(conversation_id, user_id):
            return False
        row = self._conn.execute(
            "SELECT manual_title FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return bool(row["manual_title"]) if row is not None else False

    def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        if not self._owns(conversation_id, user_id):
            return False
        self._conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        self._conn.commit()
        return True

    def search_conversations(self, user_id: str, query: str) -> list[ConversationSummary]:
        query = query.strip()
        if not query:
            return self.list_conversations(user_id)
        like = f"%{query}%"
        rows = self._conn.execute(
            "SELECT DISTINCT c.id, c.title, c.created_at, c.updated_at, "
            "       (SELECT COUNT(*) FROM messages m2 WHERE m2.conversation_id = c.id) AS message_count "
            "FROM conversations c "
            "LEFT JOIN messages m ON m.conversation_id = c.id "
            "WHERE c.user_id = ? AND (c.title LIKE ? OR m.content LIKE ?) "
            "ORDER BY c.updated_at DESC",
            (user_id, like, like),
        ).fetchall()
        return [self._row_to_summary(row) for row in rows]

    def _owns(self, conversation_id: str, user_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)
        ).fetchone()
        return row is not None

    @staticmethod
    def _row_to_summary(row: sqlite3.Row) -> ConversationSummary:
        return ConversationSummary(
            id=row["id"],
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            message_count=row["message_count"],
        )
