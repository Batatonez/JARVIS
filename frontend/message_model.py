"""MessageListModel: expõe app.models.Message (histórico da Application
Layer) para um ListView do QML.

`sync()` atualiza o modelo a partir de `JarvisApplication.get_messages()`.
Quando a nova lista é só a lista antiga com mensagens novas no final (o caso
comum: usuário mandou uma mensagem, JARVIS respondeu), faz um insert
incremental — preserva posição de scroll e permite animações de entrada no
delegate. Só faz reset completo quando o histórico encolhe ou diverge (ex.:
`/new`).
"""

from enum import IntEnum

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

from app.models import Message, MessageRole


class MessageRoles(IntEnum):
    IdRole = Qt.UserRole + 1
    RoleRole = Qt.UserRole + 2
    ContentRole = Qt.UserRole + 3
    TimestampRole = Qt.UserRole + 4
    IsUserRole = Qt.UserRole + 5


_ROLE_NAMES = {
    MessageRoles.IdRole: b"messageId",
    MessageRoles.RoleRole: b"role",
    MessageRoles.ContentRole: b"content",
    MessageRoles.TimestampRole: b"timestamp",
    MessageRoles.IsUserRole: b"isUser",
}


class MessageListModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._messages: list[Message] = []

    def roleNames(self) -> dict:
        return _ROLE_NAMES

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._messages)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._messages)):
            return None
        message = self._messages[index.row()]
        if role == MessageRoles.IdRole:
            return message.id
        if role == MessageRoles.RoleRole:
            return message.role.value
        if role == MessageRoles.ContentRole:
            return message.content
        if role == MessageRoles.TimestampRole:
            return message.timestamp.strftime("%H:%M")
        if role == MessageRoles.IsUserRole:
            return message.role == MessageRole.USER
        return None

    def sync(self, messages: list[Message]) -> None:
        old_len = len(self._messages)
        new_len = len(messages)

        if new_len >= old_len and messages[:old_len] == self._messages:
            if new_len > old_len:
                self.beginInsertRows(QModelIndex(), old_len, new_len - 1)
                self._messages = list(messages)
                self.endInsertRows()
            return

        self.beginResetModel()
        self._messages = list(messages)
        self.endResetModel()
