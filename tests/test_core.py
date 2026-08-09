import tempfile
import unittest
from pathlib import Path

from app.state import JarvisState
from services.ai_service import AIServiceUnavailableError, UnavailableAIService
from tests.fakes import FakeAIService
from tests.helpers import build_isolated_core


class JarvisCoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.core = build_isolated_core(Path(self._tmp.name))

    def test_initial_state_is_idle(self) -> None:
        self.assertEqual(self.core.state, JarvisState.IDLE)

    async def test_start_emits_jarvis_started_event(self) -> None:
        received = []
        self.core.event_bus.subscribe("jarvis.started", lambda: received.append(True))

        await self.core.start()

        self.assertEqual(received, [True])

    async def test_stop_emits_jarvis_stopped_event(self) -> None:
        received = []
        self.core.event_bus.subscribe("jarvis.stopped", lambda: received.append(True))

        await self.core.stop()

        self.assertEqual(received, [True])

    def test_set_state_emits_state_changed_with_old_and_new(self) -> None:
        received = []
        self.core.event_bus.subscribe(
            "state.changed", lambda old, new: received.append((old, new))
        )

        self.core.set_state(JarvisState.THINKING)

        self.assertEqual(received, [(JarvisState.IDLE, JarvisState.THINKING)])
        self.assertEqual(self.core.state, JarvisState.THINKING)

    def test_set_state_to_same_state_does_not_emit_event(self) -> None:
        received = []
        self.core.event_bus.subscribe("state.changed", lambda **_: received.append(True))

        self.core.set_state(JarvisState.IDLE)  # já é o estado atual

        self.assertEqual(received, [])

    async def test_start_with_unavailable_ai_emits_disconnected(self) -> None:
        events = []
        self.core.event_bus.subscribe("ai.connecting", lambda: events.append("connecting"))
        self.core.event_bus.subscribe("ai.connected", lambda: events.append("connected"))
        self.core.event_bus.subscribe("ai.disconnected", lambda: events.append("disconnected"))

        await self.core.start()

        self.assertEqual(events, ["connecting", "disconnected"])

    async def test_start_with_available_fake_ai_emits_connected(self) -> None:
        tmp = Path(self._tmp.name)
        core = build_isolated_core(tmp, ai_service=FakeAIService(available=True))
        events = []
        core.event_bus.subscribe("ai.connecting", lambda: events.append("connecting"))
        core.event_bus.subscribe("ai.connected", lambda: events.append("connected"))

        await core.start()

        self.assertEqual(events, ["connecting", "connected"])
        self.assertTrue(core.ai_service.session_active)

    async def test_start_passes_profile_and_preferences_to_ai_service(self) -> None:
        tmp = Path(self._tmp.name)
        fake_ai = FakeAIService(available=True)
        core = build_isolated_core(tmp, ai_service=fake_ai)

        await core.start()

        self.assertIn("Perfil de teste", fake_ai.received_memory_context)
        self.assertIn("Preferências de teste", fake_ai.received_memory_context)

    async def test_memory_files_are_not_modified_by_a_full_lifecycle(self) -> None:
        tmp = Path(self._tmp.name)
        profile_path = tmp / "profile.md"
        preferences_path = tmp / "preferences.md"
        before = (profile_path.read_text(encoding="utf-8"), preferences_path.read_text(encoding="utf-8"))

        await self.core.start()
        await self.core.handle_input("olá")
        await self.core.stop()

        after = (profile_path.read_text(encoding="utf-8"), preferences_path.read_text(encoding="utf-8"))
        self.assertEqual(before, after)

    async def test_ai_connect_failure_falls_back_to_unavailable_without_crashing(self) -> None:
        failing_ai = FakeAIService(available=True)

        async def boom(*, memory_context: str = "") -> None:
            raise AIServiceUnavailableError("falha simulada de conexão")

        failing_ai.start = boom  # type: ignore[method-assign]
        tmp = Path(self._tmp.name)
        core = build_isolated_core(tmp, ai_service=failing_ai)

        await core.start()  # não deve levantar exceção

        self.assertIsInstance(core.ai_service, UnavailableAIService)


if __name__ == "__main__":
    unittest.main()
