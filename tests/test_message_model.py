"""Testes de MessageListModel — offline, sem GUI/QML.

Cobre os papéis expostos ao QML (roleNames/data), o insert incremental de
`sync()` e `update_content()` (preparação para streaming futuro — ver
frontend/README.md, seção "Streaming"). Nenhum destes testes chama IA real.
"""

import unittest

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QGuiApplication

from app.models import Message, MessageRole
from frontend.message_model import MessageListModel, MessageRoles


def _ensure_qt_app() -> QGuiApplication:
    return QGuiApplication.instance() or QGuiApplication([])


def _message(role: MessageRole, content: str) -> Message:
    return Message(role=role, content=content)


class MessageListModelTests(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_qt_app()
        self.model = MessageListModel()

    def test_starts_empty(self) -> None:
        self.assertEqual(self.model.rowCount(), 0)

    def test_role_names_match_qml_contract(self) -> None:
        names = self.model.roleNames()
        self.assertEqual(names[MessageRoles.ContentRole], b"content")
        self.assertEqual(names[MessageRoles.IsUserRole], b"isUser")
        self.assertEqual(names[MessageRoles.TimestampRole], b"timestamp")

    def test_sync_populates_rows_with_correct_roles(self) -> None:
        messages = [_message(MessageRole.USER, "oi"), _message(MessageRole.ASSISTANT, "olá")]
        self.model.sync(messages)

        self.assertEqual(self.model.rowCount(), 2)
        first = self.model.index(0, 0)
        second = self.model.index(1, 0)
        self.assertTrue(self.model.data(first, MessageRoles.IsUserRole))
        self.assertFalse(self.model.data(second, MessageRoles.IsUserRole))
        self.assertEqual(self.model.data(second, MessageRoles.ContentRole), "olá")

    def test_sync_appends_incrementally_when_history_only_grew(self) -> None:
        first = _message(MessageRole.USER, "um")
        self.model.sync([first])
        second = _message(MessageRole.ASSISTANT, "dois")
        self.model.sync([first, second])

        self.assertEqual(self.model.rowCount(), 2)
        self.assertEqual(self.model.data(self.model.index(1, 0), MessageRoles.ContentRole), "dois")

    def test_sync_resets_when_history_shrinks(self) -> None:
        self.model.sync([_message(MessageRole.USER, "um"), _message(MessageRole.ASSISTANT, "dois")])
        self.model.sync([])

        self.assertEqual(self.model.rowCount(), 0)

    def test_data_returns_none_for_invalid_index(self) -> None:
        self.model.sync([_message(MessageRole.USER, "oi")])
        invalid = self.model.index(5, 0)
        self.assertIsNone(self.model.data(invalid, MessageRoles.ContentRole))
        self.assertIsNone(self.model.data(QModelIndex(), MessageRoles.ContentRole))

    def test_update_content_replaces_text_of_existing_message(self) -> None:
        message = _message(MessageRole.ASSISTANT, "parcial")
        self.model.sync([message])

        changed = self.model.update_content(message.id, "parcial completo")

        self.assertTrue(changed)
        self.assertEqual(
            self.model.data(self.model.index(0, 0), MessageRoles.ContentRole),
            "parcial completo",
        )

    def test_update_content_returns_false_for_unknown_id(self) -> None:
        self.model.sync([_message(MessageRole.ASSISTANT, "oi")])
        self.assertFalse(self.model.update_content("id-inexistente", "x"))


if __name__ == "__main__":
    unittest.main()
