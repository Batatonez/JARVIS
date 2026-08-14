"""Vocabulário de contexto e captura sem perder o começo (v1.3.1).

Bug de origem: falando "Opa Jarvis, tudo bem?" saía "Vou apagar a vizilha e
tudo bem?" — começo destruído, fim correto.

O diagnóstico isolou DUAS causas, e cada uma tem teste aqui:

1. **Começo perdido na captura.** `stream.start()` retorna antes de o
   dispositivo entregar amostras; o HUD mostrava LISTENING (o sinal para
   falar) enquanto ainda não havia captura.
2. **"JARVIS" fora do vocabulário comum do Whisper**, o que degrada muito
   sob ruído.

Nenhum teste aqui abre microfone real, carrega modelo ou toca a rede: o que é
verificado é o contrato — que a espera acontece, que o vocabulário é passado
ao engine, e que ele nunca vira substituição de texto.
"""

import asyncio
import unittest
import unittest.mock

from services import speech_vocabulary
from services.audio_devices import AudioDevice
from services.stt_service import BufferedSTTProvider, STTEngine


def _device():
    return AudioDevice(
        index=0, name="Mic", host_api="WASAPI", max_input_channels=1,
        default_samplerate=16000, is_system_default=True,
    )


class VocabularyContentTests(unittest.TestCase):
    def test_wake_word_is_the_assistant_name(self) -> None:
        self.assertEqual(speech_vocabulary.WAKE_WORD, "JARVIS")
        self.assertEqual(speech_vocabulary.hotwords(), "JARVIS")

    def test_prompt_contains_the_wake_word_and_domain_terms(self) -> None:
        prompt = speech_vocabulary.context_prompt()
        self.assertIn("JARVIS", prompt)
        for term in ("chat", "conversa", "microfone", "renomear"):
            self.assertIn(term, prompt)

    def test_prompt_has_no_example_sentences(self) -> None:
        """Frase de exemplo no prompt é armadilha dupla: contamina qualquer
        medição (o modelo pode estar só ecoando) e, em áudio sem fala, o
        Whisper regurgita o prompt como se fosse transcrição."""
        prompt = speech_vocabulary.context_prompt()
        for phrase in ("tudo bem", "bom dia", "opa", "?"):
            self.assertNotIn(phrase, prompt.lower())

    def test_prompt_has_no_meta_words(self) -> None:
        """"Vocabulário:" era ecoado literalmente em áudio de ruído."""
        self.assertNotIn("vocabul", speech_vocabulary.context_prompt().lower())

    def test_prompt_stays_short(self) -> None:
        """Prompt longo consome contexto do modelo e aumenta a chance de eco."""
        self.assertLess(len(speech_vocabulary.context_prompt()), 300)

    def test_hotwords_is_only_the_name(self) -> None:
        """`hotwords` é mais agressivo que `initial_prompt`: enviesar a lista
        inteira aumentaria falso positivo em palavras comuns."""
        self.assertNotIn(",", speech_vocabulary.hotwords())


class WhisperOptionsTests(unittest.TestCase):
    """O provider precisa REALMENTE passar o vocabulário ao engine. Testado
    com um modelo falso — nenhum peso é carregado."""

    def _provider(self):
        from services.faster_whisper_stt_provider import FasterWhisperSTTProvider

        with unittest.mock.patch("pathlib.Path.is_dir", return_value=True):
            provider = FasterWhisperSTTProvider(model_dir="fake")
        return provider

    def _transcribe(self, pcm: bytes):
        provider = self._provider()
        model = unittest.mock.Mock()
        model.transcribe.return_value = ([], None)
        provider._model = model
        provider.transcribe_pcm(pcm)
        return model.transcribe.call_args

    def test_hotwords_and_prompt_are_sent_to_the_engine(self) -> None:
        _args, kwargs = self._transcribe(b"\x01\x02" * 16000)
        self.assertEqual(kwargs["hotwords"], "JARVIS")
        self.assertIn("JARVIS", kwargs["initial_prompt"])

    def test_vad_filter_stays_on(self) -> None:
        """Sem `vad_filter`, ruído puro faz o Whisper ecoar o
        `initial_prompt` como se fosse transcrição — medido."""
        _args, kwargs = self._transcribe(b"\x01\x02" * 16000)
        self.assertTrue(kwargs["vad_filter"])
        self.assertGreaterEqual(kwargs["vad_parameters"]["speech_pad_ms"], 400)

    def test_audio_is_padded_on_both_edges(self) -> None:
        """Meio segundo de silêncio dá "embalo" ao encoder e recupera o
        começo da frase."""
        import numpy as np

        spoken_samples = 16000
        args, _kwargs = self._transcribe(b"\x01\x02" * spoken_samples)
        audio = args[0]
        self.assertIsInstance(audio, np.ndarray)
        self.assertGreater(len(audio), spoken_samples)
        # As pontas têm que ser silêncio de verdade.
        self.assertEqual(float(np.max(np.abs(audio[:4000]))), 0.0)
        self.assertEqual(float(np.max(np.abs(audio[-4000:]))), 0.0)

    def test_too_short_audio_never_reaches_the_engine(self) -> None:
        provider = self._provider()
        model = unittest.mock.Mock()
        provider._model = model
        self.assertEqual(provider.transcribe_pcm(b"\x00\x00" * 100), "")
        model.transcribe.assert_not_called()

    def test_no_text_substitution_anywhere(self) -> None:
        """Prova de que o vocabulário NÃO é troca de texto: o que o engine
        devolve é o que sai, mesmo sendo uma palavra que o vocabulário
        "gostaria" de corrigir."""
        provider = self._provider()
        model = unittest.mock.Mock()
        segment = unittest.mock.Mock()
        segment.text = " Vou apagar a vizilha e tudo bem?"
        model.transcribe.return_value = ([segment], None)
        provider._model = model
        self.assertEqual(
            provider.transcribe_pcm(b"\x01\x02" * 16000), "Vou apagar a vizilha e tudo bem?"
        )


class _FakeCapture:
    """Captura falsa que só entrega áudio depois de N consultas — simula a
    latência de arranque do WASAPI sem abrir dispositivo nenhum."""

    def __init__(self, blocks_until_audio: int = 3) -> None:
        self._remaining = blocks_until_audio
        self.active = True
        self.started = False

    def start(self) -> None:
        self.started = True

    @property
    def receiving(self) -> bool:
        if self._remaining > 0:
            self._remaining -= 1
            return False
        return True

    def stop(self) -> bytes:
        return b""

    def cancel(self) -> None:
        self.active = False


class _Provider(BufferedSTTProvider):
    @property
    def engine(self) -> STTEngine:
        return STTEngine.NONE

    def is_available(self) -> bool:
        return True

    def transcribe_pcm(self, pcm: bytes) -> str:
        return ""


class FirstAudioWaitTests(unittest.IsolatedAsyncioTestCase):
    """Causa raiz #1: o HUD mostrava LISTENING antes de existir captura."""

    async def _start(self, capture):
        from services.audio_devices import DeviceResolution

        with unittest.mock.patch(
            "services.stt_service.resolve_input_device",
            return_value=DeviceResolution(device=_device()),
        ):
            provider = _Provider()
        with unittest.mock.patch("services.stt_service.AudioCapture", return_value=capture):
            await provider.start_listening()
        return provider

    async def test_start_listening_waits_for_the_first_block(self) -> None:
        capture = _FakeCapture(blocks_until_audio=3)
        await self._start(capture)
        self.assertTrue(capture.started)
        # Consumiu as consultas "ainda não chegou" antes de retornar.
        self.assertTrue(capture.receiving)

    async def test_wait_is_bounded_when_device_never_delivers(self) -> None:
        """Um dispositivo mudo não pode travar a UI: a espera tem teto."""
        capture = _FakeCapture(blocks_until_audio=10**9)
        loop = asyncio.get_running_loop()
        started = loop.time()
        await self._start(capture)
        elapsed = loop.time() - started
        self.assertLess(elapsed, 2.0, "a espera pelo primeiro áudio precisa ter teto")

    async def test_capture_that_is_immediately_ready_does_not_delay(self) -> None:
        capture = _FakeCapture(blocks_until_audio=0)
        loop = asyncio.get_running_loop()
        started = loop.time()
        await self._start(capture)
        self.assertLess(loop.time() - started, 0.2)


class CaptureReceivingFlagTests(unittest.TestCase):
    def test_receiving_is_false_before_any_callback(self) -> None:
        from services.audio_capture import AudioCapture

        capture = AudioCapture(device=_device())
        self.assertFalse(capture.receiving)

    def test_callback_marks_receiving(self) -> None:
        from services.audio_capture import AudioCapture

        capture = AudioCapture(device=_device())
        capture._on_audio(b"\x00\x00" * 480, 480, None, None)
        self.assertTrue(capture.receiving)

    def test_closing_clears_receiving(self) -> None:
        from services.audio_capture import AudioCapture

        capture = AudioCapture(device=_device())
        capture._on_audio(b"\x00\x00" * 480, 480, None, None)
        capture.cancel()
        self.assertFalse(capture.receiving)


if __name__ == "__main__":
    unittest.main()
