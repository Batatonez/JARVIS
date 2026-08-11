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


def derive_title(first_message_text: str) -> str:
    """Título a partir das primeiras palavras da primeira mensagem — sem IA
    (não temos Claude real, e mesmo com ele seria um passo futuro
    deliberadamente fora desta versão). Só corta e limpa."""
    text = " ".join(first_message_text.split())
    if not text:
        return _DEFAULT_TITLE
    if len(text) <= _MAX_TITLE_LENGTH:
        return text
    return text[:_MAX_TITLE_LENGTH].rstrip() + "…"


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

    def rename_conversation(self, conversation_id: str, user_id: str, title: str) -> bool:
        if not self._owns(conversation_id, user_id):
            return False
        title = title.strip() or _DEFAULT_TITLE
        self._conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))
        self._conn.commit()
        return True

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
