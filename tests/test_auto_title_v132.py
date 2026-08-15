"""Título automático de chat (v1.3.2, itens 5-15).

Bug de origem: chats novos apareciam na sidebar com a própria primeira
mensagem como nome — "Opa! E aí, tudo bem?".

**Causa raiz**: `app/account_manager.py::_persist_new_messages` criava a
conversa com `derive_title(messages[0].content)`, ou seja, copiava a pergunta.
Como o título automático só roda depois da primeira resposta — e não roda de
jeito nenhum sem IA configurada — o nome copiado era o definitivo.

Banco temporário, IA falsa. Nenhum teste toca rede, `.env` real ou banco real.
"""

import asyncio
import tempfile
import unittest
import unittest.mock
from dataclasses import replace
from pathlib import Path

from app.account_manager import AccountManager
from app.models import Message, MessageRole
from config.settings import Settings
from services.ai_service import AIService, AIServiceUnavailableError
from services.chat_title_service import ChatTitleService, clean_title, echoes_message
from services.conversation_repository import ConversationRepository, default_title
from services.local_database import connect
from services.user_repository import UserRepository
from tests.fakes import FakeAIService
from tests.fakes_email import FakeEmailService


class _TitleAI(AIService):
    """IA falsa com `ask_isolated`. `ask()` levanta de propósito: se o título
    voltar a usar a sessão do usuário, o teste quebra (item 9)."""

    def __init__(self, reply="Conversa com JARVIS", *, fail=False):
        self.reply = reply
        self._fail = fail
        self.isolated_prompts: list[str] = []
        self.session_prompts: list[str] = []

    def is_available(self) -> bool:
        return True

    @property
    def session_active(self) -> bool:
        return True

    @property
    def backend_name(self) -> str:
        return "fake"

    async def start(self, *, memory_context: str = "", preferences=None) -> None:
        return None

    async def ask(self, message: str) -> str:
        self.session_prompts.append(message)
        return "resposta do assistente"

    async def close(self) -> None:
        return None

    @property
    def supports_isolated_requests(self) -> bool:
        return True

    async def ask_isolated(self, prompt: str, *, max_tokens: int = 64) -> str:
        self.isolated_prompts.append(prompt)
        if self._fail:
            raise AIServiceUnavailableError("sem rota gratuita")
        return self.reply


# ----------------------------------------------------------------------
# Itens 11-12 — validação e fallback
# ----------------------------------------------------------------------


class EchoDetectionTests(unittest.TestCase):
    def test_identical_title_is_an_echo(self) -> None:
        self.assertTrue(echoes_message("Opa! E aí, tudo bem?", "Opa! E aí, tudo bem?"))

    def test_case_and_punctuation_do_not_hide_an_echo(self) -> None:
        self.assertTrue(echoes_message("opa e ai tudo bem", "Opa! E aí, tudo bem?"))

    def test_truncated_message_is_an_echo(self) -> None:
        self.assertTrue(
            echoes_message(
                "Meu microfone está transcrevendo",
                "Meu microfone está transcrevendo tudo errado.",
            )
        )

    def test_legitimate_topic_title_is_not_an_echo(self) -> None:
        """Um título bom PODE reusar palavras do assunto — é o esperado."""
        self.assertFalse(
            echoes_message("Build de PC Branco", "Quero montar um PC branco até 15 mil.")
        )
        self.assertFalse(
            echoes_message(
                "Problemas no Reconhecimento de Voz",
                "Meu microfone está transcrevendo tudo errado.",
            )
        )
        self.assertFalse(echoes_message("2FA no JARVIS", "Me explica como funciona 2FA no JARVIS"))

    def test_empty_inputs_are_not_echoes(self) -> None:
        self.assertFalse(echoes_message("", "oi"))
        self.assertFalse(echoes_message("titulo", ""))


class TitleValidationTests(unittest.TestCase):
    def test_prefixes_are_stripped(self) -> None:
        self.assertEqual(clean_title("Título: Build de PC Branco"), "Build de PC Branco")
        self.assertEqual(clean_title("Title: Build de PC Branco"), "Build de PC Branco")

    def test_newlines_never_survive(self) -> None:
        self.assertNotIn("\n", clean_title("Build de PC\nBranco extra"))

    def test_markdown_and_quotes_are_stripped(self) -> None:
        self.assertEqual(clean_title('**"Build de PC Branco."**'), "Build de PC Branco")

    def test_word_count_is_capped(self) -> None:
        self.assertLessEqual(len(clean_title("a bb cc dd ee ff gg hh ii").split()), 6)


class ChatTitleServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_greeting_does_not_become_its_own_title(self) -> None:
        """Item 10, exemplo 1."""
        ai = _TitleAI("Opa! E aí, tudo bem?")  # modelo devolvendo a pergunta
        title = await ChatTitleService(ai).suggest(
            user_message="Opa! E aí, tudo bem?", assistant_message="Tudo ótimo!"
        )
        self.assertIsNone(title, "eco da mensagem tem que ser descartado")

    async def test_topic_title_is_accepted(self) -> None:
        ai = _TitleAI("Problemas no Reconhecimento de Voz")
        title = await ChatTitleService(ai).suggest(
            user_message="Meu microfone está transcrevendo tudo errado.",
            assistant_message="Vamos investigar o pipeline.",
        )
        self.assertEqual(title, "Problemas no Reconhecimento de Voz")

    async def test_long_message_truncation_is_rejected(self) -> None:
        message = "Quero montar um PC branco até 15 mil reais com placa de vídeo boa"
        ai = _TitleAI("Quero montar um PC branco")
        self.assertIsNone(await ChatTitleService(ai).suggest(user_message=message))

    async def test_title_that_answers_the_conversation_is_rejected(self) -> None:
        """O modelo respondeu a conversa em vez de nomeá-la — o "título" vira
        a resposta do assistente cortada. Caso encontrado no smoke test."""
        ai = _TitleAI("Opa! Tudo ótimo por aqui! E")
        title = await ChatTitleService(ai).suggest(
            user_message="Opa! E aí, tudo bem?",
            assistant_message="Opa! Tudo ótimo por aqui! E você, como tá?",
        )
        self.assertIsNone(title)

    async def test_title_request_never_touches_the_session(self) -> None:
        """Item 9: `ask_isolated`, nunca `ask`."""
        ai = _TitleAI()
        await ChatTitleService(ai).suggest(user_message="oi tudo bem", assistant_message="olá")
        self.assertEqual(len(ai.isolated_prompts), 1)
        self.assertEqual(ai.session_prompts, [])

    async def test_prompt_asks_for_the_title_only(self) -> None:
        """v1.6.0 — o prompt passou a ser escrito em inglês e a receber o
        idioma-alvo como parâmetro (o TÍTULO continua saindo no idioma do
        usuário; a instrução é que virou técnica). O teste mede a mesma
        propriedade de antes: pede título e proíbe copiar a mensagem."""
        ai = _TitleAI()
        await ChatTitleService(ai).suggest(user_message="oi tudo bem", assistant_message="olá")
        prompt = ai.isolated_prompts[0].lower()
        self.assertIn("title", prompt)
        self.assertIn("do not copy", prompt)
        # Sem preferências, o idioma-alvo é o padrão do projeto.
        self.assertIn("portuguese (brazil)", prompt)

    async def test_provider_failure_is_silent(self) -> None:
        """Item 14: falha nunca vira erro visível nem laço."""
        ai = _TitleAI(fail=True)
        self.assertIsNone(await ChatTitleService(ai).suggest(user_message="oi tudo bem"))


# ----------------------------------------------------------------------
# Itens 5-8, 13 — integração com o AccountManager
# ----------------------------------------------------------------------


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.settings = replace(
            Settings(),
            db_path=root / "test.db",
            users_dir=root / "users",
            session_token_path=root / "session.local",
        )
        self.ai = _TitleAI()

    async def _manager(self):
        manager = self._build_manager()
        await manager.register(
            username="alice", display_name="Alice", password="senha-forte-123",
            email="alice@example.com",
        )
        return manager

    def _build_manager(self):
        manager = AccountManager(
            self.settings,
            ai_service_factory=lambda: self.ai,
            email_service=FakeEmailService(configured=False),
        )
        # No Windows o diretório temporário não some enquanto o arquivo do
        # SQLite estiver aberto — fechar a conexão faz parte do teardown.
        self.addCleanup(manager._conn.close)
        return manager

    async def _send(self, manager, text):
        await manager.app.send_message(text)
        # O consumidor de eventos e a task de título rodam fora do await.
        for _ in range(40):
            await asyncio.sleep(0.01)


class AutoTitleFlowTests(_Base):
    async def test_new_conversation_starts_with_the_default_title(self) -> None:
        """Item 7."""
        manager = await self._manager()
        await manager.start_new_conversation()
        self.assertEqual(manager.list_conversations()[0].title, default_title())
        await manager.shutdown()

    async def test_first_message_alone_never_becomes_the_title(self) -> None:
        """Item 5 — a regressão exata que motivou a v1.3.2."""
        manager = await self._manager()
        self.ai.reply = "Conversa com JARVIS"
        await self._send(manager, "Opa! E aí, tudo bem?")
        titles = [c.title for c in manager.list_conversations()]
        self.assertNotIn("Opa! E aí, tudo bem?", titles)
        await manager.shutdown()

    async def test_auto_title_runs_after_the_first_answer(self) -> None:
        """Item 8."""
        manager = await self._manager()
        self.ai.reply = "Conversa com JARVIS"
        await self._send(manager, "Opa! E aí, tudo bem?")
        self.assertEqual(manager.list_conversations()[0].title, "Conversa com JARVIS")
        await manager.shutdown()

    async def test_technical_question_gets_a_topic_title(self) -> None:
        manager = await self._manager()
        self.ai.reply = "Problemas no Reconhecimento de Voz"
        await self._send(manager, "Meu microfone está transcrevendo tudo errado.")
        self.assertEqual(
            manager.list_conversations()[0].title, "Problemas no Reconhecimento de Voz"
        )
        await manager.shutdown()

    async def test_title_falls_back_to_default_when_model_echoes(self) -> None:
        """Item 12: melhor "Nova conversa" do que um título ruim."""
        manager = await self._manager()
        self.ai.reply = "Opa! E aí, tudo bem?"
        await self._send(manager, "Opa! E aí, tudo bem?")
        self.assertEqual(manager.list_conversations()[0].title, default_title())
        await manager.shutdown()

    async def test_provider_failure_keeps_the_default_title(self) -> None:
        manager = await self._manager()
        self.ai._fail = True
        await self._send(manager, "Opa! E aí, tudo bem?")
        self.assertEqual(manager.list_conversations()[0].title, default_title())
        await manager.shutdown()

    async def test_auto_title_runs_only_once_per_conversation(self) -> None:
        """Item 8: não executar de novo a cada mensagem."""
        manager = await self._manager()
        self.ai.reply = "Conversa com JARVIS"
        await self._send(manager, "Primeira mensagem sobre alguma coisa")
        await self._send(manager, "Segunda mensagem sobre outra coisa")
        await self._send(manager, "Terceira mensagem")
        self.assertEqual(len(self.ai.isolated_prompts), 1)
        await manager.shutdown()

    async def test_title_survives_a_restart(self) -> None:
        manager = await self._manager()
        self.ai.reply = "Conversa com JARVIS"
        await self._send(manager, "Opa! E aí, tudo bem?")
        await manager.shutdown()

        again = self._build_manager()
        await again.try_auto_login()
        self.assertEqual(again.list_conversations()[0].title, "Conversa com JARVIS")
        await again.shutdown()

    async def test_title_prompt_never_enters_the_chat_history(self) -> None:
        """Item 9."""
        manager = await self._manager()
        self.ai.reply = "Conversa com JARVIS"
        await self._send(manager, "Opa! E aí, tudo bem?")
        contents = [m.content for m in manager.app.get_messages()]
        for content in contents:
            self.assertNotIn("Gere um título", content)
        self.assertEqual(len(contents), 2)  # só a pergunta e a resposta
        await manager.shutdown()


class ManualTitleTests(_Base):
    async def test_manual_rename_is_never_overwritten(self) -> None:
        """Item 13."""
        manager = await self._manager()
        self.ai.reply = "Titulo Automatico Qualquer"
        await self._send(manager, "Primeira mensagem sobre alguma coisa")
        conversation_id = manager.current_conversation_id
        manager.rename_conversation(conversation_id, "Nome do Davi")
        await self._send(manager, "Segunda mensagem")
        self.assertEqual(manager.list_conversations()[0].title, "Nome do Davi")
        await manager.shutdown()

    async def test_race_manual_rename_during_generation_wins(self) -> None:
        """Item 13, a corrida explícita:

            auto-title começa -> usuário renomeia -> auto-title termina

        O nome manual TEM que permanecer. A garantia é o
        `WHERE manual_title = 0` do UPDATE, no banco — não uma checagem em
        Python que uma task concorrente poderia ultrapassar."""
        manager = await self._manager()
        conversation_id = await manager.start_new_conversation()

        renamed = asyncio.Event()

        async def slow_title(prompt, *, max_tokens=64):
            # O rename manual acontece ENQUANTO isto está pendente.
            await renamed.wait()
            return "Titulo Automatico Atrasado"

        self.ai.ask_isolated = slow_title

        send = asyncio.ensure_future(manager.app.send_message("Mensagem sobre um assunto"))
        await asyncio.sleep(0.05)
        manager.rename_conversation(conversation_id, "Nome do Davi")
        renamed.set()
        await send
        for _ in range(40):
            await asyncio.sleep(0.01)

        self.assertEqual(manager.list_conversations()[0].title, "Nome do Davi")
        await manager.shutdown()

    async def test_repository_refuses_to_overwrite_manual_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            connection = connect(Path(tmp) / "t.db")
            try:
                users = UserRepository(connection)
                conversations = ConversationRepository(connection)
                user = users.create_user(
                    username="bob", display_name="Bob", password="senha-forte-123",
                    email="b@example.com",
                )
                conversation_id = conversations.create_conversation(user.id)
                conversations.rename_conversation(conversation_id, user.id, "Manual")
                self.assertFalse(
                    conversations.set_automatic_title(conversation_id, user.id, "Automatico")
                )
                self.assertEqual(
                    conversations.list_conversations(user.id)[0].title, "Manual"
                )
            finally:
                connection.close()


class FreeOnlyTests(unittest.TestCase):
    def test_title_service_inherits_free_only_from_the_ai_service(self) -> None:
        """Item 15: o título não escolhe provider nem modelo — usa o
        `AIService` que o app já usa, com o mesmo kill-switch de custo."""
        import inspect

        from services import chat_title_service

        source = inspect.getsource(chat_title_service)
        for forbidden in ("free_only=False", "preferred_model", "ProviderRouter("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
