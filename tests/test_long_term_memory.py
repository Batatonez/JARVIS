"""Memória de longo prazo por usuário (v1.1).

Cobre os cenários exigidos: memória atravessa chats, sobrevive a restart, é
isolada por usuário, não duplica, mensagem comum não vira memória, pedido
explícito vira, memória relevante entra no contexto da IA, e memória de
outro usuário nunca entra.

Tudo offline com provider fake — nenhuma chamada real.
"""

import tempfile
import unittest
from pathlib import Path

from app.account_manager import AccountManager
from services.ai_service import UnavailableAIService
from services.long_term_memory import (
    LongTermMemoryRepository,
    MAX_MEMORIES_PER_USER,
    MemoryCategory,
    extract_memories,
    format_memories_for_context,
)
from tests.fakes import FakeAIService
from tests.helpers import build_isolated_settings, build_isolated_voice_service


async def _settle() -> None:
    import asyncio

    for _ in range(10):
        await asyncio.sleep(0)


class MemoryExtractionTests(unittest.TestCase):
    """A extração é conservadora de propósito: lembrar de menos e estar
    certo é melhor que encher a memória de lixo."""

    def test_identity_statements_become_memory(self) -> None:
        for text in ("Meu nome é Davi", "me chamo Davi", "Pode me chamar de Davi"):
            found = extract_memories(text)
            self.assertTrue(found, f"{text!r} deveria virar memória")
            self.assertEqual(found[0][0], MemoryCategory.IDENTITY)
            self.assertIn("Davi", found[0][1])

    def test_preference_statements_become_memory(self) -> None:
        found = extract_memories("Eu prefiro respostas curtas")
        self.assertEqual(found[0][0], MemoryCategory.PREFERENCE)

    def test_project_statements_become_memory(self) -> None:
        found = extract_memories("Meu projeto se chama BatataMC")
        self.assertEqual(found[0][0], MemoryCategory.PROJECT)
        self.assertIn("BatataMC", found[0][1])

    def test_explicit_remember_becomes_memory(self) -> None:
        found = extract_memories("Lembre que eu odeio café")
        self.assertEqual(found[0][0], MemoryCategory.EXPLICIT)

    def test_statements_without_accents_are_recognized(self) -> None:
        """Em português informal a acentuação é omitida o tempo todo —
        "Meu nome e Davi" precisa funcionar igual a "Meu nome é Davi".
        Descoberto na validação funcional da v1.1."""
        for text in (
            "Meu nome e Davi",
            "Meu jogo favorito e Minecraft",
            "Meu projeto se chama BatataMC",
            "Eu prefiro respostas curtas",
        ):
            self.assertTrue(extract_memories(text), f"{text!r} deveria virar memória")

    def test_accented_and_unaccented_forms_produce_the_same_memory(self) -> None:
        com_acento = extract_memories("Meu nome é Davi")
        sem_acento = extract_memories("Meu nome e Davi")
        self.assertEqual(com_acento[0][0], sem_acento[0][0])
        self.assertIn("Davi", sem_acento[0][1])

    def test_captured_text_keeps_original_accents(self) -> None:
        """O casamento acontece no texto sem acento, mas o recorte vem do
        original — o nome guardado não pode perder a acentuação."""
        found = extract_memories("Meu nome é Ana Júlia")
        self.assertIn("Ana Júlia", found[0][1])

    def test_ordinary_message_does_not_become_memory(self) -> None:
        for text in (
            "Quanto é 5 + 5?",
            "oi tudo bem",
            "me ajuda com esse código",
            "obrigado!",
            "",
        ):
            self.assertEqual(extract_memories(text), [], f"{text!r} NÃO deveria virar memória")

    def test_question_about_a_fact_does_not_become_memory(self) -> None:
        """"Qual é meu nome?" contém "meu nome" mas é pergunta, não fato."""
        self.assertEqual(extract_memories("Qual é meu nome?"), [])
        self.assertEqual(extract_memories("qual o meu jogo favorito?"), [])


class MemoryRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        from services.local_database import connect

        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.conn = connect(Path(self._tmp.name) / "jarvis.db")
        self.addCleanup(self.conn.close)
        self.conn.execute(
            "INSERT INTO users (id, username, display_name, password_hash, plan, created_at) "
            "VALUES ('u1','alice','Alice','h','free','2026-01-01T00:00:00+00:00')"
        )
        self.conn.execute(
            "INSERT INTO users (id, username, display_name, password_hash, plan, created_at) "
            "VALUES ('u2','bob','Bob','h','free','2026-01-01T00:00:00+00:00')"
        )
        self.conn.commit()
        self.repo = LongTermMemoryRepository(self.conn)

    def test_duplicate_memory_is_upserted_not_duplicated(self) -> None:
        self.repo.remember(user_id="u1", category=MemoryCategory.IDENTITY, content="O nome do usuário é Davi.")
        self.repo.remember(user_id="u1", category=MemoryCategory.IDENTITY, content="O nome do usuário é Davi.")
        # Variação de caixa/acento/pontuação também deduplica.
        self.repo.remember(user_id="u1", category=MemoryCategory.IDENTITY, content="o nome do usuario e davi")

        memories = self.repo.list_memories("u1")
        self.assertEqual(len(memories), 1)

    def test_different_memories_coexist(self) -> None:
        self.repo.remember(user_id="u1", category=MemoryCategory.IDENTITY, content="O nome do usuário é Davi.")
        self.repo.remember(user_id="u1", category=MemoryCategory.PREFERENCE, content="O usuário prefere respostas curtas.")
        self.assertEqual(len(self.repo.list_memories("u1")), 2)

    def test_memory_is_isolated_by_user(self) -> None:
        self.repo.remember(user_id="u1", category=MemoryCategory.IDENTITY, content="O nome do usuário é Davi.")
        self.repo.remember(user_id="u2", category=MemoryCategory.IDENTITY, content="O nome do usuário é Bob.")

        alice = self.repo.list_memories("u1")
        bob = self.repo.list_memories("u2")

        self.assertEqual(len(alice), 1)
        self.assertEqual(len(bob), 1)
        self.assertIn("Davi", alice[0].content)
        self.assertIn("Bob", bob[0].content)
        self.assertNotIn("Bob", alice[0].content)

    def test_same_fact_for_two_users_does_not_collide(self) -> None:
        """O UNIQUE é (user_id, dedup_key) — dois usuários podem ter o mesmo
        fato sem um sobrescrever o outro."""
        self.repo.remember(user_id="u1", category=MemoryCategory.PREFERENCE, content="Prefere respostas curtas.")
        self.repo.remember(user_id="u2", category=MemoryCategory.PREFERENCE, content="Prefere respostas curtas.")
        self.assertEqual(len(self.repo.list_memories("u1")), 1)
        self.assertEqual(len(self.repo.list_memories("u2")), 1)

    def test_forget_is_scoped_to_the_owner(self) -> None:
        memory = self.repo.remember(
            user_id="u1", category=MemoryCategory.IDENTITY, content="O nome do usuário é Davi."
        )
        # Bob tentando apagar a memória de Alice pelo ID direto (IDOR).
        self.assertFalse(self.repo.forget(user_id="u2", memory_id=memory.id))
        self.assertEqual(len(self.repo.list_memories("u1")), 1)
        # O dono consegue.
        self.assertTrue(self.repo.forget(user_id="u1", memory_id=memory.id))
        self.assertEqual(self.repo.list_memories("u1"), [])

    def test_relevant_for_never_returns_another_users_memory(self) -> None:
        self.repo.remember(user_id="u2", category=MemoryCategory.IDENTITY, content="O nome do usuário é Bob.")
        relevant = self.repo.relevant_for(user_id="u1", query="qual é meu nome?")
        self.assertEqual(relevant, [])

    def test_relevant_for_ranks_matching_memory_first(self) -> None:
        self.repo.remember(user_id="u1", category=MemoryCategory.PROJECT, content="O projeto do usuário se chama BatataMC.")
        self.repo.remember(user_id="u1", category=MemoryCategory.PREFERENCE, content="O usuário gosta de pizza.")

        relevant = self.repo.relevant_for(user_id="u1", query="como vai o projeto BatataMC?")

        self.assertTrue(relevant)
        self.assertIn("BatataMC", relevant[0].content)

    def test_memory_count_is_capped(self) -> None:
        for i in range(MAX_MEMORIES_PER_USER + 10):
            self.repo.remember(user_id="u1", category=MemoryCategory.USER_FACT, content=f"Fato numero {i}.")
        self.assertLessEqual(len(self.repo.list_memories("u1")), MAX_MEMORIES_PER_USER)

    def test_format_for_context_contains_only_content(self) -> None:
        memory = self.repo.remember(
            user_id="u1", category=MemoryCategory.IDENTITY, content="O nome do usuário é Davi."
        )
        text = format_memories_for_context([memory])
        self.assertIn("O nome do usuário é Davi.", text)
        # Nada de internals vaza para o modelo.
        self.assertNotIn(memory.id, text)
        self.assertNotIn("user_memories", text)
        self.assertNotIn("u1", text)


class MemoryAcrossChatsTests(unittest.IsolatedAsyncioTestCase):
    """O cenário do bug relatado: dizer o nome no chat 1 e perguntar no chat 2."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def _account(self, settings=None, *, ai=None) -> AccountManager:
        return AccountManager(
            settings or build_isolated_settings(self.tmp_path),
            ai_service_factory=(lambda: ai) if ai is not None else UnavailableAIService,
            voice_service_factory=build_isolated_voice_service,
        )

    async def test_memory_survives_a_new_chat(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok")
        account = self._account(ai=fake_ai)
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.app.send_message("Meu nome é Davi")
            await _settle()

            # Chat NOVO — a memória precisa atravessar.
            await account.start_new_conversation()
            await account.app.send_message("Qual é meu nome?")
            await _settle()

            # O que a IA recebeu na segunda conversa contém a memória.
            sent_to_ai = fake_ai.asked_messages[-1]
            self.assertIn("Davi", sent_to_ai)
            self.assertIn("Qual é meu nome?", sent_to_ai)
        finally:
            await account.shutdown()

    async def test_stored_message_stays_clean_even_though_ai_gets_memory(self) -> None:
        """A memória entra no que vai para a IA, nunca no que é exibido e
        persistido — senão o chat mostraria o bloco de contexto."""
        fake_ai = FakeAIService(available=True, reply="ok")
        account = self._account(ai=fake_ai)
        try:
            user = await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.app.send_message("Meu nome é Davi")
            await _settle()
            await account.start_new_conversation()
            await account.app.send_message("Qual é meu nome?")
            await _settle()

            stored = account._conversations.get_conversation(account.current_conversation_id, user.id)
            self.assertEqual(stored[0].content, "Qual é meu nome?")
            self.assertNotIn("Fatos que o usuário", stored[0].content)
        finally:
            await account.shutdown()

    async def test_memory_survives_restart(self) -> None:
        settings = build_isolated_settings(self.tmp_path)
        first = self._account(settings, ai=FakeAIService(available=True, reply="ok"))
        try:
            await first.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await first.app.send_message("Meu nome é Davi")
            await _settle()
        finally:
            await first.shutdown()

        fake_ai = FakeAIService(available=True, reply="ok")
        second = self._account(settings, ai=fake_ai)
        try:
            await second.login(identifier="alice", password="senha-forte-123")
            self.assertTrue(second.list_memories())

            await second.start_new_conversation()
            await second.app.send_message("Qual é meu nome?")
            await _settle()

            self.assertIn("Davi", fake_ai.asked_messages[-1])
        finally:
            await second.shutdown()

    async def test_memory_from_another_user_never_enters_context(self) -> None:
        settings = build_isolated_settings(self.tmp_path)
        alice_ai = FakeAIService(available=True, reply="ok")
        bob_ai = FakeAIService(available=True, reply="ok")
        alice_account = self._account(settings, ai=alice_ai)
        bob_account = self._account(settings, ai=bob_ai)
        try:
            await alice_account.register(
                username="alice", display_name="Alice", password="senha-a-123456", email="a@example.com"
            )
            await alice_account.app.send_message("Meu nome é Davi")
            await _settle()

            await bob_account.register(
                username="bob", display_name="Bob", password="senha-b-456789", email="b@example.com"
            )
            await bob_account.app.send_message("Qual é meu nome?")
            await _settle()

            # O contexto do Bob não pode conter nada da Alice.
            self.assertNotIn("Davi", bob_ai.asked_messages[-1])
            self.assertEqual(bob_account.list_memories(), [])
        finally:
            await alice_account.shutdown()
            await bob_account.shutdown()

    async def test_ordinary_message_creates_no_memory(self) -> None:
        account = self._account(ai=FakeAIService(available=True, reply="ok"))
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.app.send_message("Quanto é 5 + 5?")
            await _settle()

            self.assertEqual(account.list_memories(), [])
        finally:
            await account.shutdown()

    async def test_explicit_remember_creates_memory(self) -> None:
        account = self._account(ai=FakeAIService(available=True, reply="ok"))
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.app.send_message("Lembre que meu projeto se chama BatataMC")
            await _settle()

            memories = account.list_memories()
            self.assertEqual(len(memories), 1)
            self.assertIn("BatataMC", memories[0].content)
        finally:
            await account.shutdown()

    async def test_repeating_a_fact_does_not_duplicate_memory(self) -> None:
        account = self._account(ai=FakeAIService(available=True, reply="ok"))
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.app.send_message("Meu nome é Davi")
            await _settle()
            await account.app.send_message("me chamo Davi")
            await _settle()

            self.assertEqual(len(account.list_memories()), 1)
        finally:
            await account.shutdown()

    async def test_assistant_replies_never_become_memory(self) -> None:
        """Só o usuário afirma fatos sobre si. Memorizar o que a IA disse
        realimentaria alucinação."""
        account = self._account(ai=FakeAIService(available=True, reply="Meu nome é JARVIS"))
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.app.send_message("oi")
            await _settle()

            self.assertEqual(account.list_memories(), [])
        finally:
            await account.shutdown()

    async def test_commands_are_never_augmented_with_memory(self) -> None:
        """Prefixar memória num comando faria o CommandRegistry deixar de
        reconhecê-lo."""
        account = self._account(ai=FakeAIService(available=True, reply="ok"))
        try:
            await account.register(
                username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
            )
            await account.app.send_message("Meu nome é Davi")
            await _settle()

            self.assertEqual(account.app._build_ai_input("/status"), "/status")
        finally:
            await account.shutdown()


if __name__ == "__main__":
    unittest.main()
