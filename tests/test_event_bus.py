import unittest

from services.event_bus import EventBus


class EventBusTests(unittest.TestCase):
    def test_emit_calls_subscribed_handler_with_payload(self) -> None:
        bus = EventBus()
        received = []
        bus.subscribe("jarvis.started", lambda **payload: received.append(payload))

        bus.emit("jarvis.started", version="0.1.0")

        self.assertEqual(received, [{"version": "0.1.0"}])

    def test_emit_with_no_subscribers_does_nothing(self) -> None:
        bus = EventBus()
        bus.emit("nobody.listening")  # não deve levantar exceção

    def test_multiple_handlers_are_all_called(self) -> None:
        bus = EventBus()
        calls = []
        bus.subscribe("event", lambda: calls.append("a"))
        bus.subscribe("event", lambda: calls.append("b"))

        bus.emit("event")

        self.assertEqual(sorted(calls), ["a", "b"])

    def test_handler_exception_does_not_break_emit(self) -> None:
        bus = EventBus()
        calls = []

        def bad_handler():
            raise RuntimeError("falha proposital")

        bus.subscribe("event", bad_handler)
        bus.subscribe("event", lambda: calls.append("still called"))

        bus.emit("event")  # não deve propagar a exceção do primeiro handler

        self.assertEqual(calls, ["still called"])

    def test_unsubscribe_stops_future_calls(self) -> None:
        bus = EventBus()
        calls = []

        def handler():
            calls.append("called")

        bus.subscribe("event", handler)
        bus.unsubscribe("event", handler)
        bus.emit("event")

        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
