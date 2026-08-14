"""Ações de mensagem (v1.2): Copy e Regenerate.

Copy precisa entregar o texto RAW (com o Markdown cru), nunca o renderizado
nem a metadata visual. Regenerate precisa achar a mensagem de usuário que
originou aquela resposta específica — inclusive quando ela está no MEIO do
histórico, que é onde um "pega a última mensagem do usuário" erraria.
"""

import tempfile
import unittest
from pathlib import Path

from PySide6.QtGui import QGuiApplication

from app.models import Message, MessageRole, ResponseStatus
from frontend.message_model import MessageListModel, MessageRoles
from tests.fakes import FakeAIService
from tests.helpers import build_isolated_account_manager, build_isolated_application, build_isolated_voice_service


def _ensure_qt_app() -> QGuiApplication:
    return QGuiApplication.instance() or QGuiApplication([])


async def _settle() -> None:
    import asyncio

    for _ in range(10):
        await asyncio.sleep(0)


class CopyContentTests(unittest.TestCase):
    """O que o botão Copy entrega vem do papel `content` (RAW)."""

    def setUp(self) -> None:
        self.raw = "# Título\n\n**negrito** e `código`"
        self.model = MessageListModel()
        self.model.sync(
            [
                Message(role=MessageRole.USER, content="minha pergunta"),
                Message(role=MessageRole.ASSISTANT, content=self.raw),
            ]
        )

    def test_assistant_copy_returns_raw_markdown(self) -> None:
        content = self.model.data(self.model.index(1, 0), MessageRoles.ContentRole)
        self.assertEqual(content, self.raw)
        self.assertIn("**negrito**", content)  # marcação crua, não renderizada

    def test_user_copy_returns_raw_text(self) -> None:
        self.assertEqual(
            self.model.data(self.model.index(0, 0), MessageRoles.ContentRole), "minha pergunta"
        )

    def test_copy_never_includes_role_label_or_timestamp(self) -> None:
        for row in range(self.model.rowCount()):
            content = self.model.data(self.model.index(row, 0), MessageRoles.ContentRole)
            self.assertNotIn("YOU", content)
            self.assertNotIn("JARVIS", content)
            timestamp = self.model.data(self.model.index(row, 0), MessageRoles.TimestampRole)
            self.assertNotIn(timestamp, content)

    def test_content_and_markdown_are_different_roles(self) -> None:
        raw = "<b>x</b>"
        model = MessageListModel()
        model.sync([Message(role=MessageRole.ASSISTANT, content=raw)])
        index = model.index(0, 0)

        self.assertEqual(model.data(index, MessageRoles.ContentRole), raw)
        self.assertNotEqual(model.data(index, MessageRoles.MarkdownRole), raw)


class BridgeClipboardTests(unittest.TestCase):
    def setUp(self) -> None:
        _ensure_qt_app()
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)

    def _bridge(self):
        from tests.helpers import build_isolated_bridge

        return build_isolated_bridge(
            Path(self._tmp.name), voice_service_factory=build_isolated_voice_service
        )

    def test_copy_puts_exact_text_on_the_clipboard(self) -> None:
        bridge = self._bridge()
        raw = "# Título\n**negrito**"

        self.assertTrue(bridge.copyToClipboard(raw))
        self.assertEqual(QGuiApplication.clipboard().text(), raw)

    def test_copy_rejects_empty_text(self) -> None:
        self.assertFalse(self._bridge().copyToClipboard(""))


class PromptLookupTests(unittest.TestCase):
    """`Conversation.prompt_for` é o que faz o Regenerate acertar o prompt."""

    def setUp(self) -> None:
        from app.conversation import Conversation

        self.conversation = Conversation()
        self.first_question = self.conversation.add(MessageRole.USER, "primeira pergunta")
        self.first_answer = self.conversation.add(MessageRole.ASSISTANT, "primeira resposta")
        self.second_question = self.conversation.add(MessageRole.USER, "segunda pergunta")
        self.second_answer = self.conversation.add(MessageRole.ASSISTANT, "segunda resposta")

    def test_finds_the_prompt_of_the_last_answer(self) -> None:
        found = self.conversation.prompt_for(self.second_answer.id)
        self.assertEqual(found.id, self.second_question.id)

    def test_finds_the_prompt_of_an_answer_in_the_middle(self) -> None:
        """O caso que um "pega a última mensagem do usuário" erraria."""
        found = self.conversation.prompt_for(self.first_answer.id)
        self.assertEqual(found.id, self.first_question.id)
        self.assertEqual(found.content, "primeira pergunta")

    def test_user_message_has_no_prompt(self) -> None:
        self.assertIsNone(self.conversation.prompt_for(self.second_question.id))

    def test_unknown_id_returns_none(self) -> None:
        self.assertIsNone(self.conversation.prompt_for("id-inexistente"))

    def test_replace_content_keeps_identity_and_position(self) -> None:
        updated = self.conversation.replace_content(self.first_answer.id, "resposta nova")

        self.assertEqual(updated.id, self.first_answer.id)
        self.assertEqual(updated.role, MessageRole.ASSISTANT)
        self.assertEqual(updated.timestamp, self.first_answer.timestamp)
        messages = self.conversation.messages
        self.assertEqual(len(messages), 4)  # nada duplicado
        self.assertEqual(messages[1].content, "resposta nova")
        self.assertEqual(messages[1].id, self.first_answer.id)


class RegenerateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    async def test_regenerate_replaces_the_answer_without_duplicating_the_prompt(self) -> None:
        fake = FakeAIService(available=True, reply="resposta A")
        application = build_isolated_application(self.tmp_path, ai_service=fake)
        await application.start()
        try:
            first = await application.send_message("minha pergunta")
            self.assertEqual(len(application.get_messages()), 2)

            fake._reply = "resposta B"
            response = await application.regenerate(first.message_id)

            self.assertEqual(response.status, ResponseStatus.SUCCESS)
            messages = application.get_messages()
            self.assertEqual(len(messages), 2, "regenerar não pode criar mensagens novas")
            self.assertEqual(messages[0].content, "minha pergunta")
            self.assertEqual(messages[1].content, "resposta B")
            self.assertEqual(messages[1].id, first.message_id, "a resposta mantém o id")
        finally:
            await application.stop()

    async def test_regenerate_uses_the_prompt_of_that_specific_answer(self) -> None:
        fake = FakeAIService(available=True, reply="resposta 1")
        application = build_isolated_application(self.tmp_path, ai_service=fake)
        await application.start()
        try:
            first = await application.send_message("pergunta 1")
            fake._reply = "resposta 2"
            await application.send_message("pergunta 2")

            fake.asked_messages.clear()
            await application.regenerate(first.message_id)

            # Reperguntou "pergunta 1", não "pergunta 2".
            self.assertEqual(fake.asked_messages, ["pergunta 1"])
        finally:
            await application.stop()

    async def test_regenerating_a_user_message_is_refused(self) -> None:
        fake = FakeAIService(available=True, reply="ok")
        application = build_isolated_application(self.tmp_path, ai_service=fake)
        await application.start()
        try:
            await application.send_message("pergunta")
            user_message = application.get_messages()[0]

            self.assertIsNone(await application.regenerate(user_message.id))
        finally:
            await application.stop()

    async def test_regenerating_an_unknown_id_is_refused(self) -> None:
        application = build_isolated_application(
            self.tmp_path, ai_service=FakeAIService(available=True, reply="ok")
        )
        await application.start()
        try:
            self.assertIsNone(await application.regenerate("id-que-nao-existe"))
        finally:
            await application.stop()

    async def test_regenerated_answer_is_persisted_in_place(self) -> None:
        account = build_isolated_account_manager(
            self.tmp_path,
            ai_service_factory=lambda: FakeAIService(available=True, reply="resposta A"),
            voice_service_factory=build_isolated_voice_service,
        )
        try:
            user = await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            first = await account.app.send_message("minha pergunta")
            await _settle()

            account.core.ai_service._reply = "resposta B"
            await account.app.regenerate(first.message_id)
            await _settle()

            stored = account._conversations.get_conversation(account.current_conversation_id, user.id)
            self.assertEqual(len(stored), 2, "o banco não pode ganhar uma linha nova")
            self.assertEqual(stored[1].id, first.message_id)
            self.assertEqual(stored[1].content, "resposta B")
            self.assertEqual(stored[1].role, MessageRole.ASSISTANT)
        finally:
            await account.shutdown()

    async def test_regenerate_cannot_write_into_another_users_conversation(self) -> None:
        """`update_message_content` é escopado por usuário, como todo o resto
        do repositório."""
        from app.account_manager import AccountManager
        from services.ai_service import UnavailableAIService
        from tests.helpers import build_isolated_settings

        settings = build_isolated_settings(self.tmp_path)
        alice = AccountManager(
            settings,
            ai_service_factory=lambda: FakeAIService(available=True, reply="resposta A"),
            voice_service_factory=build_isolated_voice_service,
        )
        bob = AccountManager(
            settings,
            ai_service_factory=UnavailableAIService,
            voice_service_factory=build_isolated_voice_service,
        )
        try:
            await alice.register(
                username="alice", display_name="Alice", password="senha-a-123", email="a@example.com"
            )
            answer = await alice.app.send_message("pergunta da alice")
            await _settle()
            alice_conversation = alice.current_conversation_id

            bob_user = await bob.register(
                username="bob", display_name="Bob", password="senha-b-456", email="b@example.com"
            )

            changed = bob._conversations.update_message_content(
                alice_conversation, bob_user.id, answer.message_id, "invadido"
            )

            self.assertFalse(changed)
            stored = alice._conversations.get_conversation(alice_conversation, alice.current_user.id)
            self.assertNotIn("invadido", [m.content for m in stored])
        finally:
            await alice.shutdown()
            await bob.shutdown()


if __name__ == "__main__":
    unittest.main()
