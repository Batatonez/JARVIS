"""Testes de `VoiceService` — offline, com fakes de STT/TTS (nunca um
microfone ou engine de voz real). Cobre disponibilidade, push-to-talk,
transcrição, fala, cancelamento e os eventos `voice.*` no EventBus interno.
"""

import asyncio
import unittest

from services.event_bus import EventBus
from services.stt_service import STTUnavailableError
from services.tts_service import TTSUnavailableError
from services.voice_service import VoiceService
from tests.fakes import FakeSTTService, FakeTTSService


class _Settings:
    def __init__(self, voice_output_enabled: bool = False) -> None:
        self.voice_output_enabled = voice_output_enabled


def _events(bus: EventBus, *names: str) -> list[tuple[str, dict]]:
    received: list[tuple[str, dict]] = []
    for name in names:
        bus.subscribe(name, lambda __name=name, **payload: received.append((__name, payload)))
    return received


class VoiceServiceAvailabilityTests(unittest.TestCase):
    def test_voice_available_when_stt_and_microphone_ready(self) -> None:
        voice = VoiceService(_Settings(), EventBus(), stt=FakeSTTService(), tts=FakeTTSService())
        self.assertTrue(voice.voice_available)
        self.assertTrue(voice.stt_ready)
        self.assertTrue(voice.tts_ready)

    def test_voice_unavailable_when_stt_engine_not_ready(self) -> None:
        voice = VoiceService(_Settings(), EventBus(), stt=FakeSTTService(available=False), tts=FakeTTSService())
        self.assertFalse(voice.voice_available)
        self.assertFalse(voice.stt_ready)

    def test_voice_unavailable_when_no_microphone(self) -> None:
        voice = VoiceService(_Settings(), EventBus(), stt=FakeSTTService(microphone=False), tts=FakeTTSService())
        self.assertFalse(voice.voice_available)
        self.assertFalse(voice.microphone_available)

    def test_tts_unavailable_does_not_affect_voice_available(self) -> None:
        # voice_available cobre só a entrada (push-to-talk); TTS é independente.
        voice = VoiceService(_Settings(), EventBus(), stt=FakeSTTService(), tts=FakeTTSService(available=False))
        self.assertTrue(voice.voice_available)
        self.assertFalse(voice.tts_ready)


class VoiceServiceListeningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.stt = FakeSTTService(transcript="olá jarvis")
        self.tts = FakeTTSService()
        self.voice = VoiceService(_Settings(), self.bus, stt=self.stt, tts=self.tts)

    async def test_start_listening_raises_when_unavailable(self) -> None:
        voice = VoiceService(_Settings(), self.bus, stt=FakeSTTService(available=False), tts=self.tts)
        with self.assertRaises(STTUnavailableError):
            await voice.start_listening()

    async def test_start_listening_marks_listening_and_emits_event(self) -> None:
        events = _events(self.bus, "voice.listening.started", "voice.level")

        await self.voice.start_listening()

        self.assertTrue(self.voice.listening)
        self.assertEqual(self.stt.start_calls, 1)
        self.assertIn(("voice.listening.started", {}), events)
        self.assertTrue(any(name == "voice.level" for name, _ in events))

    async def test_stop_and_transcribe_returns_text_and_emits_events(self) -> None:
        events = _events(
            self.bus, "voice.listening.stopped", "voice.transcription.started", "voice.transcription.completed"
        )
        await self.voice.start_listening()

        result = await self.voice.stop_and_transcribe()

        self.assertEqual(result.text, "olá jarvis")
        self.assertFalse(self.voice.listening)
        names = [name for name, _ in events]
        self.assertEqual(names, ["voice.listening.stopped", "voice.transcription.started", "voice.transcription.completed"])

    async def test_stop_and_transcribe_failure_emits_failed_event_and_raises(self) -> None:
        self.stt = FakeSTTService(fail_transcription=True)
        self.voice = VoiceService(_Settings(), self.bus, stt=self.stt, tts=self.tts)
        events = _events(self.bus, "voice.transcription.failed")
        await self.voice.start_listening()

        with self.assertRaises(STTUnavailableError):
            await self.voice.stop_and_transcribe()

        self.assertEqual(len(events), 1)

    async def test_cancel_listening_discards_without_transcription_events(self) -> None:
        events = _events(self.bus, "voice.transcription.started", "voice.transcription.completed")
        await self.voice.start_listening()

        await self.voice.cancel_listening()

        self.assertFalse(self.voice.listening)
        self.assertEqual(self.stt.cancel_calls, 1)
        self.assertEqual(events, [])

    async def test_cancel_listening_without_active_capture_is_noop(self) -> None:
        await self.voice.cancel_listening()  # não deve levantar
        self.assertEqual(self.stt.cancel_calls, 0)


class VoiceServiceSpeakingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.tts = FakeTTSService()
        self.voice = VoiceService(_Settings(), self.bus, stt=FakeSTTService(), tts=self.tts)

    async def test_speak_success_emits_events_and_updates_speaking_flag(self) -> None:
        events = _events(self.bus, "voice.speaking.started", "voice.speaking.stopped")

        await self.voice.speak("Olá, humano.")

        self.assertFalse(self.voice.speaking)  # já terminou
        self.assertEqual(self.tts.spoken, ["Olá, humano."])
        self.assertEqual([name for name, _ in events], ["voice.speaking.started", "voice.speaking.stopped"])

    async def test_speak_raises_when_tts_unavailable(self) -> None:
        voice = VoiceService(_Settings(), self.bus, stt=FakeSTTService(), tts=FakeTTSService(available=False))
        with self.assertRaises(TTSUnavailableError):
            await voice.speak("oi")

    async def test_speak_failure_emits_failed_event_and_resets_speaking(self) -> None:
        voice = VoiceService(_Settings(), self.bus, stt=FakeSTTService(), tts=FakeTTSService(fail=True))
        events = _events(self.bus, "voice.speaking.failed")

        with self.assertRaises(TTSUnavailableError):
            await voice.speak("oi")

        self.assertFalse(voice.speaking)
        self.assertEqual(len(events), 1)

    async def test_stop_speaking_calls_provider_stop_and_interrupts(self) -> None:
        slow_tts = FakeTTSService(delay=5.0)
        voice = VoiceService(_Settings(), self.bus, stt=FakeSTTService(), tts=slow_tts)
        task = asyncio.ensure_future(voice.speak("frase longa"))
        await asyncio.sleep(0.01)
        self.assertTrue(voice.speaking)

        await voice.stop_speaking()
        await task

        self.assertEqual(slow_tts.stop_calls, 1)
        self.assertEqual(slow_tts.spoken, [])  # interrompido antes de "terminar"

    async def test_stop_speaking_without_active_speech_is_noop(self) -> None:
        await self.voice.stop_speaking()
        self.assertEqual(self.tts.stop_calls, 0)


class VoiceServiceShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_cancels_listening_and_stops_speaking(self) -> None:
        bus = EventBus()
        stt = FakeSTTService()
        tts = FakeTTSService(delay=5.0)
        voice = VoiceService(_Settings(), bus, stt=stt, tts=tts)
        await voice.start_listening()
        speak_task = asyncio.ensure_future(voice.speak("algo"))
        await asyncio.sleep(0.01)

        await voice.shutdown()
        await speak_task

        self.assertFalse(voice.listening)
        self.assertEqual(stt.cancel_calls, 1)
        self.assertEqual(tts.stop_calls, 1)

    async def test_shutdown_with_nothing_active_does_not_raise(self) -> None:
        voice = VoiceService(_Settings(), EventBus(), stt=FakeSTTService(), tts=FakeTTSService())
        await voice.shutdown()  # não deve levantar


if __name__ == "__main__":
    unittest.main()
