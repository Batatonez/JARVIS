"""Roles e persistência do chat (v1.1) — o lado backend do bug das
"mensagens vazias". O lado QML (delegates recebendo `content`/`isUser`) é
coberto em `tests/test_qml_smoke.py`.

Garante que o texto e a role sobrevivem a cada fronteira do caminho:

    Conversation (RAM) -> SQLite -> reload -> MessageListModel -> roles Qt
"""

import tempfile
import unittest
from pathlib import Path

from app.models import Message, MessageRole
from frontend.message_model import MessageListModel, MessageRoles
from services.ai_service import UnavailableAIService
from tests.fakes import FakeAIService
from tests.helpers import build_isolated_account_manager, build_isolated_voice_service


async def _settle() -> None:
    import asyncio

    for _ in range(10):
        await asyncio.sleep(0)


class MessageModelRoleTests(unittest.TestCase):
    """O `MessageListModel` é o que o QML consome — os nomes de role aqui
    são o contrato com o delegate."""

    def setUp(self) -> None:
        self.model = MessageListModel()
        self.model.sync(
            [
                Message(role=MessageRole.USER, content="Meu nome é Davi"),
                Message(role=MessageRole.ASSISTANT, content="Olá, Davi!"),
            ]
        )

    def test_role_names_match_what_the_delegate_requires(self) -> None:
        """`MessageItem` declara `required property` com estes nomes; se um
        deles mudar aqui sem mudar lá, o delegate volta a ficar vazio."""
        names = {value.decode() for value in self.model.roleNames().values()}
        self.assertTrue({"content", "isUser", "timestamp", "role", "messageId"} <= names)

    def test_user_message_exposes_user_role_and_content(self) -> None:
        index = self.model.index(0, 0)
        self.assertEqual(self.model.data(index, MessageRoles.ContentRole), "Meu nome é Davi")
        self.assertEqual(self.model.data(index, MessageRoles.RoleRole), "user")
        self.assertTrue(self.model.data(index, MessageRoles.IsUserRole))

    def test_assistant_message_exposes_assistant_role_and_content(self) -> None:
        index = self.model.index(1, 0)
        self.assertEqual(self.model.data(index, MessageRoles.ContentRole), "Olá, Davi!")
        self.assertEqual(self.model.data(index, MessageRoles.RoleRole), "assistant")
        self.assertFalse(self.model.data(index, MessageRoles.IsUserRole))

    def test_no_message_is_exposed_with_empty_content(self) -> None:
        for row in range(self.model.rowCount()):
            content = self.model.data(self.model.index(row, 0), MessageRoles.ContentRole)
            self.assertTrue(content)


class ChatPersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def _account(self, *, reply: str = "Olá, Davi!"):
        return build_isolated_account_manager(
            self.tmp_path,
            ai_service_factory=lambda: FakeAIService(available=True, reply=reply),
            voice_service_factory=build_isolated_voice_service,
        )

    async def test_roles_and_content_survive_sqlite_roundtrip(self) -> None:
        account = self._account()
        try:
            user = await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.app.send_message("Meu nome é Davi")
            await _settle()

            stored = account._conversations.get_conversation(account.current_conversation_id, user.id)

            self.assertEqual(len(stored), 2)
            self.assertEqual(stored[0].role, MessageRole.USER)
            self.assertEqual(stored[0].content, "Meu nome é Davi")
            self.assertEqual(stored[1].role, MessageRole.ASSISTANT)
            self.assertEqual(stored[1].content, "Olá, Davi!")
        finally:
            await account.shutdown()

    async def test_messages_reappear_when_switching_conversations(self) -> None:
        account = self._account()
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.app.send_message("Meu nome é Davi")
            await _settle()
            first_conversation = account.current_conversation_id

            await account.start_new_conversation()
            await account.app.send_message("outra conversa")
            await _settle()
            self.assertEqual(len(account.app.get_messages()), 2)

            # Volta para a primeira: texto e roles precisam reaparecer.
            self.assertTrue(await account.open_conversation(first_conversation))
            reloaded = account.app.get_messages()

            self.assertEqual(len(reloaded), 2)
            self.assertEqual(reloaded[0].role, MessageRole.USER)
            self.assertEqual(reloaded[0].content, "Meu nome é Davi")
            self.assertEqual(reloaded[1].role, MessageRole.ASSISTANT)
            self.assertTrue(reloaded[1].content)
        finally:
            await account.shutdown()

    async def test_messages_survive_restart(self) -> None:
        from tests.helpers import build_isolated_settings
        from app.account_manager import AccountManager

        settings = build_isolated_settings(self.tmp_path)
        first = AccountManager(
            settings,
            ai_service_factory=lambda: FakeAIService(available=True, reply="Olá, Davi!"),
            voice_service_factory=build_isolated_voice_service,
        )
        try:
            await first.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await first.app.send_message("Meu nome é Davi")
            await _settle()
        finally:
            await first.shutdown()

        second = AccountManager(
            settings,
            ai_service_factory=UnavailableAIService,
            voice_service_factory=build_isolated_voice_service,
        )
        try:
            await second.login(username="alice", password="senha-forte-123")
            # `_open_session` reabre a conversa mais recente automaticamente.
            messages = second.app.get_messages()

            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0].role, MessageRole.USER)
            self.assertEqual(messages[0].content, "Meu nome é Davi")
            self.assertEqual(messages[1].role, MessageRole.ASSISTANT)
            self.assertEqual(messages[1].content, "Olá, Davi!")
        finally:
            await second.shutdown()

    async def test_model_sync_from_application_preserves_roles(self) -> None:
        """O caminho exato que o Bridge usa: `get_messages()` -> `sync()`."""
        account = self._account()
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.app.send_message("Meu nome é Davi")
            await _settle()

            model = MessageListModel()
            model.sync(account.app.get_messages())

            self.assertEqual(model.rowCount(), 2)
            self.assertTrue(model.data(model.index(0, 0), MessageRoles.IsUserRole))
            self.assertEqual(model.data(model.index(0, 0), MessageRoles.ContentRole), "Meu nome é Davi")
            self.assertFalse(model.data(model.index(1, 0), MessageRoles.IsUserRole))
            self.assertEqual(model.data(model.index(1, 0), MessageRoles.ContentRole), "Olá, Davi!")
        finally:
            await account.shutdown()


if __name__ == "__main__":
    unittest.main()
