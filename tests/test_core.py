import tempfile
import unittest
from pathlib import Path

from app.state import JarvisState
from tests.helpers import build_isolated_core


class JarvisCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.core = build_isolated_core(Path(self._tmp.name))

    def test_initial_state_is_idle(self) -> None:
        self.assertEqual(self.core.state, JarvisState.IDLE)

    def test_start_emits_jarvis_started_event(self) -> None:
        received = []
        self.core.event_bus.subscribe("jarvis.started", lambda: received.append(True))

        self.core.start()

        self.assertEqual(received, [True])

    def test_stop_emits_jarvis_stopped_event(self) -> None:
        received = []
        self.core.event_bus.subscribe("jarvis.stopped", lambda: received.append(True))

        self.core.stop()

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


if __name__ == "__main__":
    unittest.main()
