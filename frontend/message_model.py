"""MessageListModel: expõe app.models.Message (histórico da Application
Layer) para um ListView do QML.

`sync()` atualiza o modelo a partir de `JarvisApplication.get_messages()`.
Quando a nova lista é só a lista antiga com mensagens novas no final (o caso
comum: usuário mandou uma mensagem, JARVIS respondeu), faz um insert
incremental — preserva posição de scroll e permite animações de entrada no
delegate. Só faz reset completo quando o histórico encolhe ou diverge (ex.:
`/new`).
"""

import dataclasses
from datetime import datetime, timezone, tzinfo
from enum import IntEnum

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

from app.models import Message, MessageRole
from services.markdown_safety import sanitize_markdown


def to_local_display_time(moment: datetime, *, tz: tzinfo | None = None) -> str:
    """Formata um instante para exibição, no fuso LOCAL da máquina.

    O JARVIS grava tudo em UTC (`app/models.py::_utcnow`, persistido com
    `isoformat()`), o que é a arquitetura certa: um instante absoluto não
    depende de onde o app está rodando. O erro estava só aqui, na ponta:
    `timestamp.strftime("%H:%M")` formatava o horário UTC direto, então uma
    mensagem enviada às 21:11 em UTC-3 aparecia como 00:11.

    `astimezone()` sem argumento converte para o fuso do sistema — nada de
    offset fixo nem `America/Sao_Paulo` no código, então isto continua
    correto se o JARVIS for usado em outro país (ou depois do horário de
    verão mudar).

    `tz` existe para os testes fixarem um fuso e provarem que a conversão é
    de verdade; em produção nunca é passado.

    Datetime ingênuo (sem tzinfo) é tratado como UTC, não como local: essa é
    a convenção de armazenamento do projeto, e adivinhar "local" aqui
    deslocaria silenciosamente qualquer registro antigo gravado sem offset.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz).strftime("%H:%M")


class MessageRoles(IntEnum):
    IdRole = Qt.UserRole + 1
    RoleRole = Qt.UserRole + 2
    ContentRole = Qt.UserRole + 3
    TimestampRole = Qt.UserRole + 4
    IsUserRole = Qt.UserRole + 5
    MarkdownRole = Qt.UserRole + 6


_ROLE_NAMES = {
    MessageRoles.IdRole: b"messageId",
    # `content` é sempre o texto RAW, exatamente como foi enviado/recebido e
    # como está no banco — é o que o botão Copy entrega.
    MessageRoles.ContentRole: b"content",
    MessageRoles.RoleRole: b"role",
    MessageRoles.TimestampRole: b"timestamp",
    MessageRoles.IsUserRole: b"isUser",
    # `markdown` é o mesmo texto, sanitizado para renderização (v1.2). Só a
    # exibição usa este papel; nada disso é persistido.
    MessageRoles.MarkdownRole: b"markdown",
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
            # Convertido para o fuso local — ver `to_local_display_time`.
            return to_local_display_time(message.timestamp)
        if role == MessageRoles.IsUserRole:
            return message.role == MessageRole.USER
        if role == MessageRoles.MarkdownRole:
            return sanitize_markdown(message.content)
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

    def update_content(self, message_id: str, content: str) -> bool:
        """Atualiza o texto de uma mensagem já existente sem recriar a linha.

        Nenhum backend chama isto ainda (`response.delta` não existe) — é só
        o ponto de extensão para quando streaming real existir: o Bridge
        poderia chamar isto a cada delta em vez de esperar `sync()`.
        """
        for row, message in enumerate(self._messages):
            if message.id == message_id:
                self._messages[row] = dataclasses.replace(message, content=content)
                index = self.index(row, 0)
                self.dataChanged.emit(index, index, [MessageRoles.ContentRole])
                return True
        return False
