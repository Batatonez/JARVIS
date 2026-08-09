"""Testes da Application Layer (JarvisApplication) — o contrato que um futuro
frontend vai depender. Tudo offline: fakes/mocks, arquivos temporários, sem
API key, sem chamada real ao Claude Agent SDK ou a qualquer rede.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from app.application import JarvisApplication
from app.commands import JarvisExit
from app.models import AppErrorCode, AppEvent, MessageRole, ResponseStatus
from app.state import JarvisState
from services.ai_service import AIServiceUnavailableError
from tests.fakes import FakeAIService
from tests.helpers import build_isolated_application


class ApplicationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = build_isolated_application(Path(self._tmp.name))

    async def test_start_marks_core_as_running(self) -> None:
        await self.app.start()

        self.assertTrue(self.app.get_status().running)

    async def test_start_is_idempotent(self) -> None:
        received = []
        self.app._core.event_bus.subscribe("jarvis.started", lambda: received.append(True))

        await self.app.start()
        await self.app.start()

        self.assertEqual(received, [True])  # não reiniciou de novo

    async def test_stop_marks_core_as_not_running(self) -> None:
        await self.app.start()

        await self.app.stop()

        self.assertFalse(self.app.get_status().running)

    async def test_stop_is_idempotent(self) -> None:
        await self.app.start()
        received = []
        self.app._core.event_bus.subscribe("jarvis.stopped", lambda: received.append(True))

        await self.app.stop()
        await self.app.stop()

        self.assertEqual(received, [True])

    async def test_stop_without_start_does_not_raise(self) -> None:
        await self.app.stop()  # não deve levantar exceção


class SendMessageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    async def _started_app(self, ai_service=None) -> JarvisApplication:
        app = build_isolated_application(Path(self._tmp.name), ai_service=ai_service)
        await app.start()
        self.addAsyncCleanup(app.stop)
        return app

    async def test_send_message_returns_success_response(self) -> None:
        app = await self._started_app(FakeAIService(available=True, reply="Olá, humano."))

        response = await app.send_message("olá")

        self.assertEqual(response.status, ResponseStatus.SUCCESS)
        self.assertEqual(response.content, "Olá, humano.")
        self.assertIsNotNone(response.message_id)

    async def test_empty_message_returns_none_and_is_not_recorded(self) -> None:
        app = await self._started_app(FakeAIService(available=True))

        response = await app.send_message("   ")

        self.assertIsNone(response)
        self.assertEqual(app.get_messages(), [])

    async def test_user_message_enters_history_even_without_ai(self) -> None:
        app = await self._started_app()  # UnavailableAIService (padrão)

        response = await app.send_message("preciso de ajuda")

        self.assertEqual(response.status, ResponseStatus.ERROR)
        self.assertEqual(response.error.code, AppErrorCode.AI_UNAVAILABLE)
        messages = app.get_messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].role, MessageRole.USER)
        self.assertEqual(messages[0].content, "preciso de ajuda")

    async def test_successful_response_enters_history_in_order(self) -> None:
        app = await self._started_app(FakeAIService(available=True, reply="tudo bem, e você?"))

        await app.send_message("oi, tudo bem?")

        messages = app.get_messages()
        self.assertEqual(len(messages), 2)
        self.assertEqual((messages[0].role, messages[0].content), (MessageRole.USER, "oi, tudo bem?"))
        self.assertEqual((messages[1].role, messages[1].content), (MessageRole.ASSISTANT, "tudo bem, e você?"))

    async def test_multiple_exchanges_preserve_order(self) -> None:
        app = await self._started_app(FakeAIService(available=True, reply="ok"))

        await app.send_message("primeira")
        await app.send_message("segunda")

        contents = [(m.role, m.content) for m in app.get_messages()]
        self.assertEqual(
            contents,
            [
                (MessageRole.USER, "primeira"),
                (MessageRole.ASSISTANT, "ok"),
                (MessageRole.USER, "segunda"),
                (MessageRole.ASSISTANT, "ok"),
            ],
        )

    async def test_ai_runtime_failure_is_a_structured_error_not_in_history(self) -> None:
        fake_ai = FakeAIService(
            available=True, ask_error=AIServiceUnavailableError("o provider falhou")
        )
        app = await self._started_app(fake_ai)

        response = await app.send_message("oi")

        self.assertEqual(response.status, ResponseStatus.ERROR)
        self.assertEqual(response.error.code, AppErrorCode.AI_UNAVAILABLE)
        messages = app.get_messages()
        self.assertEqual(len(messages), 1)  # só a mensagem do usuário, sem resposta "parcial"
        self.assertEqual(messages[0].role, MessageRole.USER)

    async def test_ai_failure_does_not_crash_application(self) -> None:
        fake_ai = FakeAIService(available=True, ask_error=RuntimeError("erro totalmente inesperado"))
        app = await self._started_app(fake_ai)

        # Mesmo um erro não mapeado (RuntimeError puro) deve virar um
        # AssistantResponse estruturado, nunca uma exceção não tratada.
        response = await app.send_message("oi")

        self.assertEqual(response.status, ResponseStatus.ERROR)

    async def test_state_returns_to_idle_after_success(self) -> None:
        app = await self._started_app(FakeAIService(available=True, reply="ok"))

        await app.send_message("oi")

        self.assertEqual(app.get_status().state, "idle")


class BusyAndConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    async def test_status_reports_busy_while_thinking(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok", delay=0.05)
        app = build_isolated_application(Path(self._tmp.name), ai_service=fake_ai)
        await app.start()
        self.addAsyncCleanup(app.stop)

        task = asyncio.ensure_future(app.send_message("mensagem lenta"))
        await asyncio.sleep(0)  # deixa a requisição A começar

        self.assertTrue(app.get_status().busy)
        await task
        self.assertFalse(app.get_status().busy)

    async def test_concurrent_message_is_rejected_while_busy(self) -> None:
        fake_ai = FakeAIService(available=True, reply="resposta A", delay=0.05)
        app = build_isolated_application(Path(self._tmp.name), ai_service=fake_ai)
        await app.start()
        self.addAsyncCleanup(app.stop)

        task_a = asyncio.ensure_future(app.send_message("mensagem A"))
        await asyncio.sleep(0)  # requisição A já está "em andamento"

        response_b = await app.send_message("mensagem B")

        self.assertEqual(response_b.status, ResponseStatus.ERROR)
        self.assertEqual(response_b.error.code, AppErrorCode.JARVIS_BUSY)

        response_a = await task_a
        self.assertEqual(response_a.status, ResponseStatus.SUCCESS)
        self.assertEqual(response_a.content, "resposta A")
        # "mensagem B" nunca deveria ter chegado ao provider de IA.
        self.assertEqual(fake_ai.asked_messages, ["mensagem A"])
        # ... nem deveria ter entrado no histórico.
        contents = [m.content for m in app.get_messages()]
        self.assertEqual(contents, ["mensagem A", "resposta A"])


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    async def test_cancel_current_request_returns_false_when_nothing_in_flight(self) -> None:
        app = build_isolated_application(Path(self._tmp.name), ai_service=FakeAIService())
        await app.start()
        self.addAsyncCleanup(app.stop)

        cancelled = await app.cancel_current_request()

        self.assertFalse(cancelled)

    async def test_cancel_in_flight_request_returns_cancelled_status(self) -> None:
        fake_ai = FakeAIService(available=True, reply="nunca deveria chegar aqui", delay=5.0)
        app = build_isolated_application(Path(self._tmp.name), ai_service=fake_ai)
        await app.start()
        self.addAsyncCleanup(app.stop)

        states: list[JarvisState] = []
        app._core.event_bus.subscribe("state.changed", lambda old, new: states.append(new))

        task = asyncio.ensure_future(app.send_message("mensagem que será cancelada"))
        await asyncio.sleep(0.01)  # deixa a requisição correr até o await dentro do fake (estado -> THINKING)

        self.assertTrue(app.get_status().busy)
        cancelled = await app.cancel_current_request()
        response = await task

        self.assertTrue(cancelled)
        self.assertEqual(response.status, ResponseStatus.CANCELLED)
        # Sem mensagem parcial marcada como completa no histórico:
        contents = [m.role for m in app.get_messages()]
        self.assertEqual(contents, [MessageRole.USER])
        # Estado voltou para IDLE, nada preso em THINKING/busy:
        self.assertEqual(app.get_status().state, "idle")
        self.assertFalse(app.get_status().busy)
        self.assertIn(JarvisState.THINKING, states)
        self.assertEqual(states[-1], JarvisState.IDLE)

    async def test_stop_cancels_pending_request_cleanly(self) -> None:
        fake_ai = FakeAIService(available=True, delay=5.0)
        app = build_isolated_application(Path(self._tmp.name), ai_service=fake_ai)
        await app.start()

        task = asyncio.ensure_future(app.send_message("mensagem pendente"))
        await asyncio.sleep(0)

        await app.stop()  # não deve travar nem levantar exceção
        response = await task

        self.assertEqual(response.status, ResponseStatus.CANCELLED)
        self.assertTrue(task.done())


class NewConversationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    async def test_new_conversation_clears_history(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok")
        app = build_isolated_application(Path(self._tmp.name), ai_service=fake_ai)
        await app.start()
        self.addAsyncCleanup(app.stop)
        await app.send_message("mensagem antiga")

        await app.new_conversation()

        self.assertEqual(app.get_messages(), [])

    async def test_new_conversation_does_not_touch_persistent_memory(self) -> None:
        tmp_path = Path(self._tmp.name)
        fake_ai = FakeAIService(available=True, reply="ok")
        app = build_isolated_application(tmp_path, ai_service=fake_ai)
        await app.start()
        self.addAsyncCleanup(app.stop)

        profile_path = tmp_path / "profile.md"
        preferences_path = tmp_path / "preferences.md"
        before = (profile_path.read_text(encoding="utf-8"), preferences_path.read_text(encoding="utf-8"))

        await app.send_message("mensagem")
        await app.new_conversation()

        after = (profile_path.read_text(encoding="utf-8"), preferences_path.read_text(encoding="utf-8"))
        self.assertEqual(before, after)

    async def test_new_conversation_resets_ai_session(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok")
        app = build_isolated_application(Path(self._tmp.name), ai_service=fake_ai)
        await app.start()
        self.addAsyncCleanup(app.stop)
        self.assertEqual(fake_ai.start_count, 1)

        await app.new_conversation()

        self.assertEqual(fake_ai.close_count, 1)
        self.assertEqual(fake_ai.start_count, 2)

    async def test_handle_input_new_command_reports_and_clears(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok")
        app = build_isolated_application(Path(self._tmp.name), ai_service=fake_ai)
        await app.start()
        self.addAsyncCleanup(app.stop)
        await app.send_message("mensagem antiga")

        response_text = await app.handle_input("/new")

        self.assertIn("Nova conversa", response_text)
        self.assertEqual(app.get_messages(), [])


class TerminalIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """`handle_input` é o método que `app/terminal.py` usa — comandos e
    mensagens continuam distintos, e nenhum comando é enviado à IA."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    async def _started_app(self, ai_service=None) -> JarvisApplication:
        app = build_isolated_application(Path(self._tmp.name), ai_service=ai_service)
        await app.start()
        self.addAsyncCleanup(app.stop)
        return app

    async def test_help_status_memory_clear_still_work(self) -> None:
        app = await self._started_app()

        help_text = await app.handle_input("/help")
        status_text = await app.handle_input("/status")
        memory_text = await app.handle_input("/memory")

        self.assertIn("/help", help_text)
        self.assertIn("/new", help_text)
        self.assertIn("JARVIS Core", status_text)
        self.assertIn("profile.md", memory_text)

    async def test_exit_still_raises_jarvis_exit(self) -> None:
        app = await self._started_app()

        with self.assertRaises(JarvisExit):
            await app.handle_input("/exit")

    async def test_commands_are_never_sent_to_ai(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok")
        app = await self._started_app(fake_ai)

        await app.handle_input("/status")
        await app.handle_input("/memory")
        await app.handle_input("/help")

        self.assertEqual(fake_ai.asked_messages, [])

    async def test_plain_message_gets_jarvis_prefix_for_terminal_display(self) -> None:
        fake_ai = FakeAIService(available=True, reply="tudo certo por aqui.")
        app = await self._started_app(fake_ai)

        response_text = await app.handle_input("oi, tudo bem?")

        self.assertEqual(response_text, "JARVIS: tudo certo por aqui.")

    async def test_empty_input_returns_none(self) -> None:
        app = await self._started_app()

        self.assertIsNone(await app.handle_input(""))
        self.assertIsNone(await app.handle_input("   "))


class EventStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    async def test_subscribe_receives_lifecycle_and_message_events(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok")
        app = build_isolated_application(Path(self._tmp.name), ai_service=fake_ai)
        queue = app.subscribe()

        await app.start()
        await app.send_message("oi")
        await app.stop()

        received_types = []
        while not queue.empty():
            event = queue.get_nowait()
            self.assertIsInstance(event, AppEvent)
            received_types.append(event.type)

        # Ordem lógica: início -> conversa -> mensagem -> resposta -> parada.
        self.assertEqual(
            received_types,
            [
                "jarvis.started",
                "ai.connected",  # fake AI disponível e conectada
                "conversation.started",
                "message.received",
                "response.started",
                "state.changed",  # IDLE -> THINKING
                "state.changed",  # THINKING -> IDLE
                "response.completed",
                "jarvis.stopping",
                "ai.disconnected",
                "jarvis.stopped",
            ],
        )

    async def test_unsubscribe_stops_receiving_events(self) -> None:
        app = build_isolated_application(Path(self._tmp.name))
        queue = app.subscribe()
        app.unsubscribe(queue)

        await app.start()
        self.addAsyncCleanup(app.stop)

        self.assertTrue(queue.empty())

    async def test_events_async_generator_yields_events_and_cleans_up(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok")
        app = build_isolated_application(Path(self._tmp.name), ai_service=fake_ai)
        await app.start()
        self.addAsyncCleanup(app.stop)

        generator = app.events()
        # Avança o generator até o primeiro `await queue.get()` — é aí que
        # `events()` chama `self.subscribe()` e registra a queue. Sem isso,
        # eventos emitidos antes do generator "acordar" seriam perdidos (o
        # generator é preguiçoso: nada roda até o primeiro `__anext__`).
        first_event_task = asyncio.ensure_future(generator.__anext__())
        await asyncio.sleep(0)
        self.assertEqual(len(app._subscribers), 1)

        await app.send_message("oi")
        first_event = await first_event_task

        self.assertIsInstance(first_event, AppEvent)
        await generator.aclose()
        self.assertEqual(app._subscribers, [])  # aclose() limpou a própria queue

    async def test_state_changed_event_payload_uses_plain_strings(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok")
        app = build_isolated_application(Path(self._tmp.name), ai_service=fake_ai)
        queue = app.subscribe()
        await app.start()
        self.addAsyncCleanup(app.stop)

        await app.send_message("oi")

        state_events = []
        while not queue.empty():
            event = queue.get_nowait()
            if event.type == "state.changed":
                state_events.append(event.payload)

        self.assertTrue(state_events)
        for payload in state_events:
            self.assertIsInstance(payload["old"], str)
            self.assertIsInstance(payload["new"], str)


class NoSdkLeakageTests(unittest.IsolatedAsyncioTestCase):
    """Nenhum consumidor externo deve receber um tipo do Claude Agent SDK."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    async def test_no_public_model_belongs_to_claude_agent_sdk(self) -> None:
        fake_ai = FakeAIService(available=True, reply="ok")
        app = build_isolated_application(Path(self._tmp.name), ai_service=fake_ai)
        queue = app.subscribe()
        await app.start()
        self.addAsyncCleanup(app.stop)

        response = await app.send_message("oi")
        status = app.get_status()
        messages = app.get_messages()

        candidates = [response, status, *messages]
        while not queue.empty():
            candidates.append(queue.get_nowait())

        for obj in candidates:
            module = type(obj).__module__
            self.assertFalse(
                module.startswith("claude_agent_sdk"),
                f"{obj!r} veio de {module}, um tipo do Claude Agent SDK vazou para o consumidor.",
            )


if __name__ == "__main__":
    unittest.main()
