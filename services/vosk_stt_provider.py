"""`VoskSTTProvider` — STT offline via Vosk (https://alphacephei.com/vosk/).

Único módulo que importa `vosk`/`sounddevice` (ver `services/stt_service.py`
para o porquê). Captura em streaming direto do microfone para o
reconhecedor — **nenhum áudio é escrito em disco**, nem mesmo em arquivo
temporário: os frames PCM vivem só em memória, processados incrementalmente
pelo `KaldiRecognizer`, e descartados assim que consumidos.

**Sample rate (v0.9):** o Vosk precisa de 16kHz, mas nem todo microfone
expõe 16kHz nativamente — pedir uma taxa que o dispositivo não suporta de
verdade pode falhar alto (`PortAudioError`) ou, pior, ser aceito e produzir
áudio distorcido/errado silenciosamente, dependendo do driver. Por isso a
captura sempre abre o stream na taxa NATIVA do dispositivo
(`default_samplerate`, via `sd.query_devices`) e resample para 16kHz é
feito aqui mesmo (`_LinearResampler`, interpolação linear simples — sem
`numpy`/`scipy`) só quando a taxa nativa é diferente de 16kHz.

O nível de áudio (0.0-1.0, para o HUD reagir visualmente) é calculado com
`array` da biblioteca padrão, não `numpy`/`audioop` — `audioop` foi removido
do Python 3.13+, e `numpy` seria uma dependência nova só para uma média
quadrática simples. O callback do PortAudio roda a ~20 Hz (blocos de 50ms),
dentro do orçamento de "algumas dezenas de updates por segundo".
"""

import asyncio
import json
import logging
from array import array
from collections.abc import Callable
from pathlib import Path

import sounddevice as sd
import vosk

from services.stt_service import STTStatus, STTUnavailableError, SpeechToTextService

logger = logging.getLogger(__name__)

vosk.SetLogLevel(-1)  # silencia o log nativo do Kaldi — ruído irrelevante para o JARVIS

_VOSK_SAMPLE_RATE = 16000
_BLOCK_SECONDS = 0.05  # ~50ms de bloco -> callback a ~20 Hz, dentro do orçamento de updates/s
_LEVEL_NORMALIZATION = 9000.0  # divisor empírico para RMS de fala próxima ao microfone


def _rms_level(samples: array) -> float:
    if not samples:
        return 0.0
    mean_square = sum(s * s for s in samples) / len(samples)
    return min(1.0, (mean_square**0.5) / _LEVEL_NORMALIZATION)


class _LinearResampler:
    """Resample int16 mono por interpolação linear — simples, sem
    dependência pesada, "boa o suficiente" para reconhecimento de fala (não
    é reprodução de áudio de alta fidelidade). Mantém a posição fracionária
    entre blocos para não introduzir um "salto" a cada callback; aceita
    perder no máximo uma amostra na borda de cada bloco (~inaudível/
    irrelevante para o reconhecedor em blocos de 50ms)."""

    def __init__(self, from_rate: int, to_rate: int) -> None:
        self._ratio = from_rate / to_rate
        self._pos = 0.0

    @property
    def active(self) -> bool:
        return self._ratio != 1.0

    def process(self, samples: array) -> array:
        if not self.active:
            return samples
        out = array("h")
        n = len(samples)
        if n < 2:
            return out
        pos = self._pos
        while True:
            index = int(pos)
            if index + 1 >= n:
                break
            frac = pos - index
            a = samples[index]
            b = samples[index + 1]
            out.append(int(a + (b - a) * frac))
            pos += self._ratio
        self._pos = pos - n
        return out


class VoskSTTProvider(SpeechToTextService):
    def __init__(self, *, model_path: Path) -> None:
        if not model_path.is_dir():
            raise STTUnavailableError(f"Modelo Vosk não encontrado em: {model_path}")
        self._model = vosk.Model(str(model_path))
        self._device_samplerate = self._detect_device_samplerate()
        self._microphone_available = self._device_samplerate is not None

        self._stream: sd.RawInputStream | None = None
        self._recognizer: "vosk.KaldiRecognizer | None" = None
        self._resampler: _LinearResampler | None = None
        self._listening = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_level: Callable[[float], None] | None = None

    @staticmethod
    def _detect_device_samplerate() -> int | None:
        """Consulta o dispositivo de entrada PADRÃO do sistema (nunca um
        device hardcoded) e devolve a taxa de amostragem NATIVA dele —
        `None` se não houver microfone."""
        try:
            device = sd.query_devices(kind="input")
        except Exception:
            return None
        if device is None:
            return None
        rate = device.get("default_samplerate")
        return int(round(rate)) if rate else _VOSK_SAMPLE_RATE

    def is_available(self) -> bool:
        return True

    @property
    def status(self) -> STTStatus:
        return STTStatus.READY if self._microphone_available else STTStatus.NO_MICROPHONE

    @property
    def microphone_available(self) -> bool:
        return self._microphone_available

    async def start_listening(self, *, on_level: Callable[[float], None] | None = None) -> None:
        if self._listening:
            return
        if not self._microphone_available or self._device_samplerate is None:
            raise STTUnavailableError("Nenhum microfone disponível.")

        self._loop = asyncio.get_running_loop()
        self._on_level = on_level
        self._recognizer = vosk.KaldiRecognizer(self._model, _VOSK_SAMPLE_RATE)
        self._recognizer.SetWords(False)
        self._resampler = _LinearResampler(self._device_samplerate, _VOSK_SAMPLE_RATE)

        block_size = max(1, int(self._device_samplerate * _BLOCK_SECONDS))
        try:
            self._stream = sd.RawInputStream(
                samplerate=self._device_samplerate,
                blocksize=block_size,
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
        samples = array("h")
        samples.frombytes(bytes(indata))

        if self._on_level is not None and self._loop is not None:
            level = _rms_level(samples)
            try:
                self._loop.call_soon_threadsafe(self._on_level, level)
            except RuntimeError:
                pass  # loop já encerrado (shutdown em andamento)

        if self._recognizer is not None:
            to_feed = self._resampler.process(samples) if self._resampler is not None else samples
            if to_feed:
                self._recognizer.AcceptWaveform(to_feed.tobytes())

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
        self._resampler = None
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
        self._resampler = None

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logger.debug("Falha ao fechar o stream de áudio (pode já estar fechado).")
            self._stream = None
