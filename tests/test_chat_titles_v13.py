"""Rename de conversa e título automático (v1.3, itens 18-23, 65).

Nenhuma chamada de IA real: o `AIService` é um fake que devolve texto fixo,
falha ou se declara indisponível. Banco temporário.
"""

import tempfile
import unittest
from pathlib import Path

from services.ai_service import AIService, AIServiceUnavailableError
from services.chat_title_service import ChatTitleService, clean_title
from services.conversation_repository import ConversationRepository, default_title, sanitize_title
from services.local_database import connect
from services.user_repository import UserRepository


class _FakeTitleAI(AIService):
    """Fake com `ask_isolated` — o caminho que o título usa. `ask()` levanta
    de propósito: se o serviço de título voltar a usar a sessão do usuário,
    o teste quebra."""

    def __init__(self, reply="Melhorias no Reconhecimento de Voz", *, available=True,
                 isolated=True, fail=False):
        self.reply = reply
        self._available = available
        self._isolated = isolated
        self._fail = fail
        self.isolated_calls: list[str] = []

    def is_available(self) -> bool:
        return self._available

    @property
    def session_active(self) -> bool:
        return True

    @property
    def backend_name(self) -> str:
        return "fake"

    async def start(self, *, memory_context: str = "", preferences=None) -> None:
        return None

    async def ask(self, message: str) -> str:
        raise AssertionError("o título nunca pode usar a sessão do usuário")

    async def close(self) -> None:
        return None

    @property
    def supports_isolated_requests(self) -> bool:
        return self._isolated

    async def ask_isolated(self, prompt: str, *, max_tokens: int = 64) -> str:
        self.isolated_calls.append(prompt)
        if self._fail:
            raise AIServiceUnavailableError("sem rota gratuita")
        return self.reply


class TitleSanitizationTests(unittest.TestCase):
    def test_collapses_whitespace_and_strips_control_chars(self) -> None:
        self.assertEqual(sanitize_title("  Meu\n\tChat  "), "Meu Chat")

    def test_empty_becomes_default(self) -> None:
        self.assertEqual(sanitize_title("   "), "Nova conversa")

    def test_long_titles_are_truncated(self) -> None:
        result = sanitize_title("palavra " * 40)
        self.assertLessEqual(len(result), 61)
        self.assertTrue(result.endswith("…"))

    def test_default_title_never_derives_from_the_message(self) -> None:
        """v1.3.2: `derive_title(first_message)` foi REMOVIDO. Um chat novo
        nasce com o título padrão, nunca com a pergunta copiada."""
        self.assertEqual(default_title(), "Nova conversa")


class CleanTitleTests(unittest.TestCase):
    """Item 21: 2-6 palavras, sem markdown/aspas/ponto final/emoji."""

    def test_strips_quotes_markdown_and_final_period(self) -> None:
        self.assertEqual(clean_title('**"Build de PC Branco."**'), "Build de PC Branco")

    def test_strips_label_prefix(self) -> None:
        self.assertEqual(clean_title("Título: Build de PC Branco"), "Build de PC Branco")

    def test_strips_emoji(self) -> None:
        self.assertEqual(clean_title("🚀 Build de PC Branco"), "Build de PC Branco")

    def test_keeps_only_the_first_line(self) -> None:
        self.assertEqual(
            clean_title("Build de PC Branco\nEsse título resume o assunto."), "Build de PC Branco"
        )

    def test_caps_at_six_words(self) -> None:
        result = clean_title("um dois tres quatro cinco seis sete oito")
        self.assertEqual(len(result.split()), 6)

    def test_rejects_single_word(self) -> None:
        self.assertEqual(clean_title("PC"), "")

    def test_rejects_empty(self) -> None:
        self.assertEqual(clean_title("   "), "")


class ChatTitleServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_infers_a_title_from_the_first_exchange(self) -> None:
        ai = _FakeTitleAI("Melhorias no Reconhecimento de Voz")
        title = await ChatTitleService(ai).suggest(
            user_message="Meu reconhecimento de voz está entendendo tudo errado",
            assistant_message="Vamos investigar o pipeline de áudio.",
        )
        self.assertEqual(title, "Melhorias no Reconhecimento de Voz")

    async def test_uses_isolated_request_with_small_budget(self) -> None:
        """Item 22: prompt curto, orçamento pequeno — e fora da sessão."""
        ai = _FakeTitleAI()
        await ChatTitleService(ai).suggest(user_message="oi tudo bem", assistant_message="olá")
        self.assertEqual(len(ai.isolated_calls), 1)
        self.assertLess(len(ai.isolated_calls[0]), 1500)

    async def test_returns_none_without_ai(self) -> None:
        ai = _FakeTitleAI(available=False)
        self.assertIsNone(await ChatTitleService(ai).suggest(user_message="oi"))

    async def test_returns_none_when_provider_lacks_isolated_requests(self) -> None:
        ai = _FakeTitleAI(isolated=False)
        self.assertIsNone(await ChatTitleService(ai).suggest(user_message="oi"))
        self.assertEqual(ai.isolated_calls, [])

    async def test_provider_failure_is_silent(self) -> None:
        """Item 22: falhar não pode quebrar o chat nem entrar em laço."""
        ai = _FakeTitleAI(fail=True)
        self.assertIsNone(await ChatTitleService(ai).suggest(user_message="oi tudo bem"))

    async def test_unusable_reply_is_discarded(self) -> None:
        ai = _FakeTitleAI("PC")  # uma palavra só
        self.assertIsNone(await ChatTitleService(ai).suggest(user_message="quero montar um pc"))

    async def test_empty_user_message_short_circuits(self) -> None:
        ai = _FakeTitleAI()
        self.assertIsNone(await ChatTitleService(ai).suggest(user_message="  "))
        self.assertEqual(ai.isolated_calls, [])


class ManualTitleTests(unittest.TestCase):
    """Item 23: título manual NUNCA é sobrescrito pelo automático. A regra
    mora no `WHERE manual_title = 0` do UPDATE, não numa checagem que alguém
    pode esquecer de fazer antes de chamar."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.connection = connect(Path(self._tmp.name) / "test.db")
        self.addCleanup(self.connection.close)
        self.users = UserRepository(self.connection)
        self.conversations = ConversationRepository(self.connection)
        self.alice = self.users.create_user(
            username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
        )
        self.bob = self.users.create_user(
            username="bob", display_name="Bob", password="senha-forte-123", email="b@example.com"
        )
        self.conversation_id = self.conversations.create_conversation(self.alice.id)

    def test_rename_persists_and_marks_manual(self) -> None:
        self.assertTrue(
            self.conversations.rename_conversation(self.conversation_id, self.alice.id, "Meu Chat")
        )
        self.assertTrue(self.conversations.has_manual_title(self.conversation_id, self.alice.id))
        summary = self.conversations.list_conversations(self.alice.id)[0]
        self.assertEqual(summary.title, "Meu Chat")

    def test_rename_survives_reconnection(self) -> None:
        """Item 18: reiniciar mantém o título."""
        self.conversations.rename_conversation(self.conversation_id, self.alice.id, "Meu Chat")
        again = ConversationRepository(self.connection)
        self.assertEqual(again.list_conversations(self.alice.id)[0].title, "Meu Chat")

    def test_rename_trims_and_limits(self) -> None:
        self.conversations.rename_conversation(
            self.conversation_id, self.alice.id, "  " + "x" * 200 + "  "
        )
        self.assertLessEqual(len(self.conversations.list_conversations(self.alice.id)[0].title), 61)

    def test_empty_rename_falls_back_to_default(self) -> None:
        self.conversations.rename_conversation(self.conversation_id, self.alice.id, "   ")
        self.assertEqual(self.conversations.list_conversations(self.alice.id)[0].title, "Nova conversa")

    def test_automatic_title_applies_when_not_manual(self) -> None:
        self.assertTrue(
            self.conversations.set_automatic_title(
                self.conversation_id, self.alice.id, "Build de PC Branco"
            )
        )
        self.assertEqual(
            self.conversations.list_conversations(self.alice.id)[0].title, "Build de PC Branco"
        )

    def test_automatic_title_never_overwrites_manual(self) -> None:
        self.conversations.rename_conversation(self.conversation_id, self.alice.id, "Nome do Davi")
        self.assertFalse(
            self.conversations.set_automatic_title(
                self.conversation_id, self.alice.id, "Titulo Automatico Qualquer"
            )
        )
        self.assertEqual(
            self.conversations.list_conversations(self.alice.id)[0].title, "Nome do Davi"
        )

    def test_automatic_title_rejects_default_and_empty(self) -> None:
        self.assertFalse(
            self.conversations.set_automatic_title(self.conversation_id, self.alice.id, "  ")
        )
        self.assertFalse(
            self.conversations.set_automatic_title(
                self.conversation_id, self.alice.id, "Nova conversa"
            )
        )

    def test_rename_of_another_users_conversation_is_refused(self) -> None:
        self.assertFalse(
            self.conversations.rename_conversation(self.conversation_id, self.bob.id, "Invadido")
        )
        self.assertFalse(
            self.conversations.set_automatic_title(self.conversation_id, self.bob.id, "Invadido")
        )
        self.assertEqual(
            self.conversations.list_conversations(self.alice.id)[0].title, "Nova conversa"
        )


if __name__ == "__main__":
    unittest.main()
