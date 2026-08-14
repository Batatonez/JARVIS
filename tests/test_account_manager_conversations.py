"""Testes de chats persistentes do `AccountManager` (v0.9) — 11 cenários
pedidos explicitamente: criar conversa, salvar mensagens, sobreviver a um
reinício da Application Layer, persistir entre sessões, carregar conversa
antiga, listar, ordenar, buscar, renomear, excluir, e isolamento entre
usuários. Sempre por cima de `FakeAIService` (nunca IA real) e SQLite
temporário (nunca `data/jarvis.db` real).
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from app.account_manager import AccountManager
from services.ai_service import UnavailableAIService
from tests.fakes import FakeAIService
from tests.helpers import build_isolated_account_manager, build_isolated_settings, build_isolated_voice_service


async def _settle() -> None:
    """`_consume_events()` roda em background (task separada) — dá algumas
    voltas do event loop para a persistência de mensagens (assinante do
    AccountManager) drenar a fila antes de checar o banco."""
    for _ in range(10):
        await asyncio.sleep(0)


def _account_manager(tmp_path: Path, *, reply: str = "ok") -> AccountManager:
    return build_isolated_account_manager(
        tmp_path,
        ai_service_factory=lambda: FakeAIService(available=True, reply=reply),
        voice_service_factory=build_isolated_voice_service,
    )


class AccountManagerConversationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    # 1. Criar conversa ------------------------------------------------
    async def test_start_new_conversation_creates_empty_conversation(self) -> None:
        account = _account_manager(self.tmp_path)
        try:
            await account.register(username="alice", display_name="Alice", password="senha-forte-123")

            conversation_id = await account.start_new_conversation()

            self.assertIsNotNone(conversation_id)
            self.assertEqual(account.current_conversation_id, conversation_id)
            conversations = account.list_conversations()
            self.assertEqual(len(conversations), 1)
            self.assertEqual(conversations[0].message_count, 0)
        finally:
            await account.shutdown()

    # 2. Salvar mensagens -------------------------------------------------
    async def test_sending_message_persists_it_via_event_subscription(self) -> None:
        account = _account_manager(self.tmp_path, reply="Olá, humano.")
        try:
            user = await account.register(username="alice", display_name="Alice", password="senha-forte-123")

            await account.app.send_message("oi, tudo bem?")
            await _settle()

            self.assertIsNotNone(account.current_conversation_id)
            messages = account._conversations.get_conversation(account.current_conversation_id, user.id)
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0].content, "oi, tudo bem?")
            self.assertEqual(messages[1].content, "Olá, humano.")
        finally:
            await account.shutdown()

    # 3. Sobrevive a um reinício da Application Layer -----------------
    async def test_conversation_survives_application_layer_restart(self) -> None:
        account = _account_manager(self.tmp_path, reply="Olá, humano.")
        try:
            user = await account.register(username="alice", display_name="Alice", password="senha-forte-123")
            await account.app.send_message("mensagem antes do restart")
            await _settle()
            conversation_id = account.current_conversation_id

            # "Reinício da Application Layer": para e recria por cima do
            # mesmo Core/DB, sem passar por logout — a persistência não pode
            # depender do objeto `JarvisApplication` em RAM continuar vivo.
            await account.app.stop()
            from app.application import JarvisApplication

            account.app = JarvisApplication(account.core, voice_service=account.app.voice)
            await account.app.start()

            messages_from_db = account._conversations.get_conversation(conversation_id, user.id)
            self.assertEqual(len(messages_from_db), 2)
            self.assertEqual(messages_from_db[0].content, "mensagem antes do restart")
        finally:
            await account.shutdown()

    # 4. Persiste entre sessões (logout/login) --------------------------
    async def test_conversation_persists_across_logout_and_login(self) -> None:
        account = _account_manager(self.tmp_path, reply="Olá, humano.")
        try:
            await account.register(username="alice", display_name="Alice", password="senha-forte-123")
            await account.app.send_message("mensagem antes do logout")
            await _settle()
            conversation_id = account.current_conversation_id

            await account.logout()
            await account.login(identifier="alice", password="senha-forte-123")

            # `_open_session` reabre automaticamente a conversa mais recente.
            self.assertEqual(account.current_conversation_id, conversation_id)
            loaded_messages = account.app.get_messages()
            self.assertEqual(len(loaded_messages), 2)
            self.assertEqual(loaded_messages[0].content, "mensagem antes do logout")
        finally:
            await account.shutdown()

    # 5. Carregar conversa antiga -----------------------------------------
    async def test_open_conversation_loads_history_into_application(self) -> None:
        account = _account_manager(self.tmp_path, reply="resposta A")
        try:
            await account.register(username="alice", display_name="Alice", password="senha-forte-123")
            await account.app.send_message("primeira conversa")
            await _settle()
            first_conversation_id = account.current_conversation_id

            await account.start_new_conversation()
            await account.app.send_message("segunda conversa")
            await _settle()
            self.assertEqual(len(account.app.get_messages()), 2)

            opened = await account.open_conversation(first_conversation_id)

            self.assertTrue(opened)
            self.assertEqual(account.current_conversation_id, first_conversation_id)
            reloaded = account.app.get_messages()
            self.assertEqual(len(reloaded), 2)
            self.assertEqual(reloaded[0].content, "primeira conversa")
        finally:
            await account.shutdown()

    # 6. Listar -------------------------------------------------------------
    async def test_list_conversations_returns_all_for_user(self) -> None:
        account = _account_manager(self.tmp_path)
        try:
            await account.register(username="alice", display_name="Alice", password="senha-forte-123")
            await account.app.send_message("conversa 1")
            await _settle()
            await account.start_new_conversation()
            await account.app.send_message("conversa 2")
            await _settle()

            conversations = account.list_conversations()

            self.assertEqual(len(conversations), 2)
        finally:
            await account.shutdown()

    # 7. Ordenar (mais recente primeiro) --------------------------------
    async def test_list_conversations_orders_by_most_recently_updated(self) -> None:
        account = _account_manager(self.tmp_path)
        try:
            await account.register(username="alice", display_name="Alice", password="senha-forte-123")
            await account.app.send_message("conversa mais antiga")
            await _settle()
            older_id = account.current_conversation_id

            await account.start_new_conversation()
            await account.app.send_message("conversa mais nova")
            await _settle()
            newer_id = account.current_conversation_id

            conversations = account.list_conversations()

            self.assertEqual(conversations[0].id, newer_id)
            self.assertEqual(conversations[-1].id, older_id)
        finally:
            await account.shutdown()

    # 8. Buscar ---------------------------------------------------------
    async def test_search_conversations_filters_by_title_and_content(self) -> None:
        account = _account_manager(self.tmp_path)
        try:
            await account.register(username="alice", display_name="Alice", password="senha-forte-123")
            await account.app.send_message("quero configurar meu servidor de jogos")
            await _settle()
            await account.start_new_conversation()
            await account.app.send_message("qual é a previsão do tempo amanhã")
            await _settle()

            results = account.search_conversations("servidor")

            self.assertEqual(len(results), 1)
            self.assertIn("servidor", results[0].title.lower())
        finally:
            await account.shutdown()

    # 9. Renomear -------------------------------------------------------
    async def test_rename_conversation_updates_title(self) -> None:
        account = _account_manager(self.tmp_path)
        try:
            await account.register(username="alice", display_name="Alice", password="senha-forte-123")
            await account.app.send_message("oi")
            await _settle()
            conversation_id = account.current_conversation_id

            renamed = account.rename_conversation(conversation_id, "Nome escolhido pelo usuário")

            self.assertTrue(renamed)
            conversations = account.list_conversations()
            self.assertEqual(conversations[0].title, "Nome escolhido pelo usuário")
        finally:
            await account.shutdown()

    # 10. Excluir -------------------------------------------------------
    async def test_delete_conversation_removes_it_and_resets_current_if_active(self) -> None:
        account = _account_manager(self.tmp_path)
        try:
            await account.register(username="alice", display_name="Alice", password="senha-forte-123")
            await account.app.send_message("oi")
            await _settle()
            conversation_id = account.current_conversation_id

            deleted = await account.delete_conversation(conversation_id)

            self.assertTrue(deleted)
            self.assertEqual(account.list_conversations(), [])
            self.assertIsNone(account.current_conversation_id)
        finally:
            await account.shutdown()

    # 11. Usuário A não vê chats de B ------------------------------------
    async def test_user_a_cannot_see_or_access_user_b_conversations(self) -> None:
        settings = build_isolated_settings(self.tmp_path)
        alice_account = AccountManager(
            settings, ai_service_factory=UnavailableAIService, voice_service_factory=build_isolated_voice_service
        )
        bob_account = AccountManager(
            settings, ai_service_factory=UnavailableAIService, voice_service_factory=build_isolated_voice_service
        )
        try:
            await alice_account.register(username="alice", display_name="Alice", password="senha-forte-123")
            alice_conversation_id = await alice_account.start_new_conversation()

            bob = await bob_account.register(username="bob", display_name="Bob", password="outra-senha-456")

            self.assertEqual(bob_account.list_conversations(), [])
            self.assertEqual(
                bob_account._conversations.get_conversation(alice_conversation_id, bob.id), None
            )
            self.assertFalse(bob_account.rename_conversation(alice_conversation_id, "invadido"))
            self.assertFalse(await bob_account.delete_conversation(alice_conversation_id))
        finally:
            await alice_account.shutdown()
            await bob_account.shutdown()


if __name__ == "__main__":
    unittest.main()
