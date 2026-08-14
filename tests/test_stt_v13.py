"""STT da v1.3: providers, VAD, resampler e escolha de engine.

**Isolamento total (item 74):** nenhum teste aqui abre microfone real, baixa
modelo, carrega o Whisper de verdade ou toca a rede. O que é exercitado é a
lógica: acumulação de segmentos, limiares do VAD, conversão de taxa e a
política de fallback de `create_stt_service`.
"""

import asyncio
import unittest
import unittest.mock
from array import array
from pathlib import Path

from services.audio_capture import (
    AudioCapture,
    AudioCaptureError,
    LinearResampler,
    SilenceDetector,
    VadSettings,
    rms_level,
)
from services.audio_devices import AudioDevice, resolve_input_device
from services.stt_service import (
    BufferedSTTProvider,
    STTEngine,
    STTStatus,
    STTUnavailableError,
    UnavailableSTTService,
    create_stt_service,
)


def _device(name: str = "Mic", *, index: int = 0, default: bool = False, rate: int = 48000):
    return AudioDevice(
        index=index,
        name=name,
        host_api="WASAPI",
        max_input_channels=1,
        default_samplerate=rate,
        is_system_default=default,
    )


class _FakeSettings:
    """Settings mínimo, sem tocar `config.settings` real (que leria o `.env`)."""

    def __init__(self, *, tmp: Path, engine="auto", whisper_installed=False, vosk_installed=False):
        self.voice_input_enabled = True
        self.stt_engine_preference = engine
        self.whisper_models_dir = tmp / "whisper"
        self.whisper_model_size = "base"
        self.whisper_compute_type = "int8"
        self.stt_model_path = tmp / "vosk-model"
        if whisper_installed:
            (self.whisper_models_dir / "faster-whisper-base").mkdir(parents=True)
        if vosk_installed:
            self.stt_model_path.mkdir(parents=True)


# ----------------------------------------------------------------------
# Correção da causa raiz: acumulação de segmentos
# ----------------------------------------------------------------------


class _RecordingProvider(BufferedSTTProvider):
    """Provider concreto mínimo para exercitar a base sem engine real."""

    def __init__(self, text="", **kwargs):
        super().__init__(**kwargs)
        self._text = text
        self.received: bytes = b""

    @property
    def engine(self) -> STTEngine:
        return STTEngine.NONE

    def is_available(self) -> bool:
        return True

    def transcribe_pcm(self, pcm: bytes) -> str:
        self.received = pcm
        return self._text


class VoskSegmentAccumulationTests(unittest.TestCase):
    """O bug que motivou a v1.3: `AcceptWaveform` devolve `True` quando o
    endpointer do Kaldi fecha uma utterance, e o texto dela só existe em
    `Result()`. A v1.2 ignorava isso e lia apenas `FinalResult()`, então
    "Opa, tudo bem?" virava "bem"."""

    def _provider_with_fake_vosk(self, results, final):
        recognizer = unittest.mock.Mock()
        # `AcceptWaveform` devolve True nos chunks em que uma utterance fecha.
        recognizer.AcceptWaveform.side_effect = [True] * len(results) + [False]
        recognizer.Result.side_effect = [f'{{"text": "{text}"}}' for text in results]
        recognizer.FinalResult.return_value = f'{{"text": "{final}"}}'

        fake_vosk = unittest.mock.Mock()
        fake_vosk.KaldiRecognizer.return_value = recognizer
        fake_vosk.Model.return_value = unittest.mock.Mock()
        return fake_vosk

    def test_all_intermediate_segments_are_kept(self) -> None:
        from services import vosk_stt_provider

        fake_vosk = self._provider_with_fake_vosk(["opa", "tudo"], "bem")
        with unittest.mock.patch.object(vosk_stt_provider, "vosk", fake_vosk), \
             unittest.mock.patch.object(Path, "is_dir", return_value=True), \
             unittest.mock.patch(
                 "services.stt_service.resolve_input_device",
                 return_value=resolve_input_device(None),
             ):
            provider = vosk_stt_provider.VoskSTTProvider(model_path=Path("fake"))
            text = provider.transcribe_pcm(b"\x00\x00" * 24000)

        self.assertEqual(text, "opa tudo bem")

    def test_final_result_alone_would_lose_words(self) -> None:
        """Prova explícita da regressão: se só `FinalResult()` fosse lido, o
        resultado seria 'bem'. Este teste existe para que reintroduzir o bug
        quebre a suíte."""
        from services import vosk_stt_provider

        fake_vosk = self._provider_with_fake_vosk(["opa", "tudo"], "bem")
        with unittest.mock.patch.object(vosk_stt_provider, "vosk", fake_vosk), \
             unittest.mock.patch.object(Path, "is_dir", return_value=True):
            provider = vosk_stt_provider.VoskSTTProvider(model_path=Path("fake"))
            text = provider.transcribe_pcm(b"\x00\x00" * 24000)

        self.assertNotEqual(text, "bem")
        self.assertIn("opa", text)

    def test_empty_audio_returns_empty_string(self) -> None:
        from services import vosk_stt_provider

        with unittest.mock.patch.object(vosk_stt_provider, "vosk", unittest.mock.Mock()), \
             unittest.mock.patch.object(Path, "is_dir", return_value=True):
            provider = vosk_stt_provider.VoskSTTProvider(model_path=Path("fake"))
            self.assertEqual(provider.transcribe_pcm(b""), "")


# ----------------------------------------------------------------------
# VAD
# ----------------------------------------------------------------------


class SilenceDetectorTests(unittest.TestCase):
    BLOCK = 0.03

    def _feed(self, detector, level, seconds):
        stopped = False
        for _ in range(int(seconds / self.BLOCK)):
            if detector.feed(level, self.BLOCK):
                stopped = True
                break
        return stopped

    def test_never_stops_during_calibration(self) -> None:
        """A calibração acontece no COMEÇO da fala — encerrar ali cortaria a
        primeira palavra (item 6)."""
        detector = SilenceDetector(VadSettings(calibration_seconds=0.5))
        self.assertFalse(self._feed(detector, 0.0, 0.45))

    def test_short_phrase_is_not_cut(self) -> None:
        """"Opa, tudo bem?" tem uma micro-pausa depois de "Opa". Ela não pode
        ser tratada como fim de fala."""
        detector = SilenceDetector(VadSettings(trailing_silence_seconds=1.2))
        self._feed(detector, 0.01, 0.4)      # calibração (silêncio)
        self._feed(detector, 0.5, 0.3)       # "Opa"
        cut = self._feed(detector, 0.01, 0.5)  # vírgula: 0.5s de pausa
        self.assertFalse(cut, "pausa curta no meio da frase não pode encerrar a captura")
        self.assertFalse(self._feed(detector, 0.5, 0.6))  # "tudo bem?"

    def test_stops_after_trailing_silence(self) -> None:
        detector = SilenceDetector(VadSettings(trailing_silence_seconds=1.0))
        self._feed(detector, 0.01, 0.4)
        self._feed(detector, 0.6, 1.0)
        self.assertTrue(self._feed(detector, 0.0, 2.0))

    def test_stops_at_hard_cap(self) -> None:
        """Nunca ouvir para sempre (item 6)."""
        detector = SilenceDetector(VadSettings(max_seconds=2.0))
        self.assertTrue(self._feed(detector, 0.9, 5.0))

    def test_stops_when_nobody_speaks(self) -> None:
        detector = SilenceDetector(VadSettings(silence_timeout_seconds=1.5))
        self.assertTrue(self._feed(detector, 0.0, 5.0))
        self.assertFalse(detector.speech_detected)

    def test_threshold_adapts_to_noisy_room(self) -> None:
        quiet = SilenceDetector(VadSettings())
        self._feed(quiet, 0.001, 0.4)
        quiet.feed(0.001, 0.03)

        noisy = SilenceDetector(VadSettings())
        self._feed(noisy, 0.05, 0.4)
        noisy.feed(0.05, 0.03)

        self.assertGreater(noisy.threshold, quiet.threshold)


# ----------------------------------------------------------------------
# Resampler
# ----------------------------------------------------------------------


class LinearResamplerTests(unittest.TestCase):
    def test_identity_when_rates_match(self) -> None:
        resampler = LinearResampler(16000, 16000)
        self.assertFalse(resampler.active)
        samples = array("h", [1, 2, 3])
        self.assertIs(resampler.process(samples), samples)

    def test_downsampling_produces_expected_length(self) -> None:
        resampler = LinearResampler(48000, 16000)
        out = resampler.process(array("h", list(range(-1500, 1500))))
        # 3000 amostras a 48kHz -> ~1000 a 16kHz (tolerância de borda).
        self.assertAlmostEqual(len(out), 1000, delta=5)

    def test_block_boundary_never_produces_negative_position(self) -> None:
        """Bug da v1.2: `self._pos = pos - n` podia ficar negativo e a
        interpolação extrapolava para trás, fora do sinal."""
        resampler = LinearResampler(44100, 16000)
        total = 0
        for _ in range(10):
            total += len(resampler.process(array("h", [100] * 1323)))
        self.assertGreater(total, 0)
        # Sinal constante: toda amostra convertida tem que continuar constante.
        out = resampler.process(array("h", [100] * 1323))
        self.assertTrue(all(abs(value - 100) <= 1 for value in out), list(out)[:10])

    def test_flush_returns_pending_tail(self) -> None:
        resampler = LinearResampler(44100, 16000)
        resampler.process(array("h", [10] * 100))
        self.assertIsInstance(resampler.flush(), array)

    def test_antialias_filter_engages_only_when_decimating(self) -> None:
        self.assertEqual(LinearResampler(16000, 16000)._filter_width, 1)
        self.assertEqual(LinearResampler(22050, 16000)._filter_width, 1)  # ratio 1.38
        self.assertEqual(LinearResampler(48000, 16000)._filter_width, 3)  # ratio 3.0

    def test_rejects_invalid_rates(self) -> None:
        with self.assertRaises(ValueError):
            LinearResampler(0, 16000)


class RmsLevelTests(unittest.TestCase):
    def test_silence_is_zero(self) -> None:
        self.assertEqual(rms_level(array("h", [0] * 100)), 0.0)

    def test_empty_block_is_zero(self) -> None:
        self.assertEqual(rms_level(array("h")), 0.0)

    def test_level_is_clamped_to_one(self) -> None:
        self.assertLessEqual(rms_level(array("h", [32767] * 100)), 1.0)


# ----------------------------------------------------------------------
# Captura (sem abrir microfone real)
# ----------------------------------------------------------------------


class AudioCaptureTests(unittest.TestCase):
    def test_start_without_device_raises(self) -> None:
        with self.assertRaises(AudioCaptureError):
            AudioCapture(device=None).start()

    def test_prefers_16k_when_device_accepts_it(self) -> None:
        """A correção principal da v1.3: quando o PortAudio aceita 16kHz,
        NENHUM resample caseiro acontece."""
        capture = AudioCapture(device=_device(rate=44100))
        fake_sd = unittest.mock.Mock()
        fake_sd.check_input_settings.return_value = None
        self.assertEqual(capture._negotiate_samplerate(fake_sd), 16000)

    def test_falls_back_to_native_rate_when_16k_refused(self) -> None:
        capture = AudioCapture(device=_device(rate=44100))
        fake_sd = unittest.mock.Mock()
        fake_sd.check_input_settings.side_effect = RuntimeError("não suportado")
        self.assertEqual(capture._negotiate_samplerate(fake_sd), 44100)

    def test_stop_returns_captured_audio(self) -> None:
        capture = AudioCapture(device=_device())
        capture._blocks = [b"\x01\x02", b"\x03\x04"]
        self.assertEqual(capture.stop(), b"\x01\x02\x03\x04")

    def test_cancel_discards_audio(self) -> None:
        capture = AudioCapture(device=_device())
        capture._blocks = [b"\x01\x02"]
        capture.cancel()
        self.assertEqual(capture.stop(), b"")


# ----------------------------------------------------------------------
# Escolha de engine e fallback (item 7)
# ----------------------------------------------------------------------


class EngineSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = unittest.mock.MagicMock()

    def test_disabled_voice_input_returns_unavailable(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            settings = _FakeSettings(tmp=Path(tmp))
            settings.voice_input_enabled = False
            service = create_stt_service(settings)
        self.assertIsInstance(service, UnavailableSTTService)
        self.assertEqual(service.status, STTStatus.UNAVAILABLE)

    def test_no_model_installed_reports_setup_required(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            service = create_stt_service(_FakeSettings(tmp=Path(tmp)))
        self.assertEqual(service.status, STTStatus.SETUP_REQUIRED)

    def test_vosk_preference_skips_whisper_entirely(self) -> None:
        """`JARVIS_STT_ENGINE=vosk` numa máquina fraca não deve nem tentar
        carregar o Whisper."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            settings = _FakeSettings(
                tmp=Path(tmp), engine="vosk", whisper_installed=True, vosk_installed=True
            )
            with unittest.mock.patch(
                "services.vosk_stt_provider.VoskSTTProvider"
            ) as vosk_cls, unittest.mock.patch(
                "services.faster_whisper_stt_provider.FasterWhisperSTTProvider"
            ) as whisper_cls:
                create_stt_service(settings)

        vosk_cls.assert_called_once()
        whisper_cls.assert_not_called()

    def test_falls_back_to_vosk_when_whisper_fails_to_load(self) -> None:
        """Item 7: Whisper indisponível não pode derrubar o JARVIS — cai no
        Vosk."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            settings = _FakeSettings(tmp=Path(tmp), whisper_installed=True, vosk_installed=True)
            with unittest.mock.patch(
                "services.whisper_model_manager.WhisperModelManager.is_installed",
                new_callable=unittest.mock.PropertyMock,
                return_value=True,
            ), unittest.mock.patch(
                "services.faster_whisper_stt_provider.FasterWhisperSTTProvider",
                side_effect=STTUnavailableError("pacote ausente"),
            ), unittest.mock.patch(
                "services.vosk_stt_provider.VoskSTTProvider"
            ) as vosk_cls:
                create_stt_service(settings)

        vosk_cls.assert_called_once()

    def test_never_raises_when_everything_is_broken(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            settings = _FakeSettings(tmp=Path(tmp), vosk_installed=True)
            with unittest.mock.patch(
                "services.vosk_stt_provider.VoskSTTProvider",
                side_effect=RuntimeError("modelo corrompido"),
            ):
                service = create_stt_service(settings)

        self.assertFalse(service.is_available())
        self.assertEqual(service.status, STTStatus.UNAVAILABLE)


class BufferedProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_without_start_raises(self) -> None:
        with unittest.mock.patch(
            "services.stt_service.resolve_input_device", return_value=resolve_input_device(None)
        ):
            provider = _RecordingProvider()
        with self.assertRaises(STTUnavailableError):
            await provider.stop_and_transcribe()

    async def test_empty_capture_returns_empty_without_calling_engine(self) -> None:
        provider = _RecordingProvider(text="não deveria aparecer")
        capture = unittest.mock.Mock()
        capture.stop.return_value = b""
        provider._capture = capture
        self.assertEqual(await provider.stop_and_transcribe(), "")
        self.assertEqual(provider.received, b"")

    async def test_transcription_runs_off_the_event_loop(self) -> None:
        provider = _RecordingProvider(text="olá")
        capture = unittest.mock.Mock()
        capture.stop.return_value = b"\x01\x02"
        provider._capture = capture
        self.assertEqual(await provider.stop_and_transcribe(), "olá")
        self.assertEqual(provider.received, b"\x01\x02")

    async def test_cancel_discards_without_transcribing(self) -> None:
        provider = _RecordingProvider(text="nunca")
        capture = unittest.mock.Mock()
        provider._capture = capture
        await provider.cancel()
        capture.cancel.assert_called_once()
        self.assertEqual(provider.received, b"")


if __name__ == "__main__":
    unittest.main()
