"""`VoskSTTProvider` — STT offline via Vosk (https://alphacephei.com/vosk/).

Único módulo que importa `vosk`/`sounddevice` (ver `services/stt_service.py`
para o porquê). Captura em streaming direto do microfone para o
reconhecedor — **nenhum áudio é escrito em disco**, nem mesmo em arquivo
temporário: os frames PCM vivem só em memória, processados incrementalmente
pelo `KaldiRecognizer`, e descartados assim que consumidos.

O nível de áudio (0.0-1.0, para o HUD reagir visualmente) é calculado com
`array` da biblioteca padrão, não `numpy`/`audioop` — `audioop` foi removido
do Python 3.13+, e `numpy` seria uma dependência nova só para uma média
quadrática simples. O callback do PortAudio roda a ~20 Hz (blocksize de
50ms), dentro do orçamento de "algumas dezenas de updates por segundo".
"""

import asyncio
import json
import logging
from array import array
from collections.abc import Callable
from pathlib import Path

import sounddevice as sd
import vosk

from services.stt_service import STTUnavailableError, SpeechToTextService

logger = logging.getLogger(__name__)

vosk.SetLogLevel(-1)  # silencia o log nativo do Kaldi — ruído irrelevante para o JARVIS

_SAMPLE_RATE = 16000
_BLOCK_SIZE = 800  # 50ms @ 16kHz -> callback a ~20 Hz
_LEVEL_NORMALIZATION = 9000.0  # divisor empírico para RMS de fala próxima ao microfone


def _rms_level(raw: bytes) -> float:
    samples = array("h")
    samples.frombytes(raw)
    if not samples:
        return 0.0
    mean_square = sum(s * s for s in samples) / len(samples)
    return min(1.0, (mean_square**0.5) / _LEVEL_NORMALIZATION)


class VoskSTTProvider(SpeechToTextService):
    def __init__(self, *, model_path: Path) -> None:
        if not model_path.is_dir():
            raise STTUnavailableError(f"Modelo Vosk não encontrado em: {model_path}")
        self._model = vosk.Model(str(model_path))
        self._microphone_available = self._detect_microphone()

        self._stream: sd.RawInputStream | None = None
        self._recognizer: "vosk.KaldiRecognizer | None" = None
        self._listening = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_level: Callable[[float], None] | None = None

    @staticmethod
    def _detect_microphone() -> bool:
        try:
            device = sd.query_devices(kind="input")
        except Exception:
            return False
        return device is not None

    def is_available(self) -> bool:
        return True

    @property
    def microphone_available(self) -> bool:
        return self._microphone_available

    async def start_listening(self, *, on_level: Callable[[float], None] | None = None) -> None:
        if self._listening:
            return
        if not self._microphone_available:
            raise STTUnavailableError("Nenhum microfone disponível.")

        self._loop = asyncio.get_running_loop()
        self._on_level = on_level
        self._recognizer = vosk.KaldiRecognizer(self._model, _SAMPLE_RATE)
        self._recognizer.SetWords(False)

        try:
            self._stream = sd.RawInputStream(
                samplerate=_SAMPLE_RATE,
                blocksize=_BLOCK_SIZE,
                dtype="int16",
                channels=1,
                callback=self._on_audio,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            self._recognizer = None
            raise STTUnavailableError(f"Não foi possível abrir o microfone: {exc}") from exc

        self._listening = True

    def _on_audio(self, indata, frames: int, time_info, status) -> None:
        # Roda na thread de áudio do PortAudio, não na thread do event loop —
        # nunca toca estruturas do asyncio/Qt diretamente, só via
        # `call_soon_threadsafe`.
        raw = bytes(indata)
        if self._recognizer is not None:
            self._recognizer.AcceptWaveform(raw)
        if self._on_level is not None and self._loop is not None:
            level = _rms_level(raw)
            try:
                self._loop.call_soon_threadsafe(self._on_level, level)
            except RuntimeError:
                pass  # loop já encerrado (shutdown em andamento)

    async def stop_and_transcribe(self) -> str:
        if not self._listening:
            raise STTUnavailableError("Nenhuma captura de áudio em andamento.")
        self._listening = False
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._stop_and_finalize)

    def _stop_and_finalize(self) -> str:
        self._close_stream()
        result_json = self._recognizer.FinalResult() if self._recognizer is not None else "{}"
        self._recognizer = None
        try:
            data = json.loads(result_json)
        except ValueError:
            data = {}
        return str(data.get("text", "")).strip()

    async def cancel(self) -> None:
        if not self._listening:
            return
        self._listening = False
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._close_stream)
        self._recognizer = None

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logger.debug("Falha ao fechar o stream de áudio (pode já estar fechado).")
            self._stream = None
