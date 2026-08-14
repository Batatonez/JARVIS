"""`VoskSTTProvider` — STT offline via Vosk (https://alphacephei.com/vosk/).

Desde a v1.3 este é o **fallback leve**, não o engine principal: 53 MB de
modelo, carga quase instantânea e pouca RAM, para máquinas fracas ou para
quem não quer baixar o modelo do faster-whisper. Ver
`services/stt_service.py` para a política de escolha.

Único módulo que importa `vosk`. A captura de áudio saiu daqui na v1.3 e
vive em `services/audio_capture.py`, compartilhada com o Whisper.

--------------------------------------------------------------------------
CORREÇÃO DA CAUSA RAIZ (v1.3) — por que "Opa, tudo bem?" virava "bem"
--------------------------------------------------------------------------
`KaldiRecognizer.AcceptWaveform()` devolve `True` quando o *endpointer* do
Kaldi decide que uma utterance terminou (tipicamente numa micro-pausa, como
a vírgula depois de "Opa"). Nesse momento o texto daquela utterance fica
disponível em `Result()` — e o reconhecedor **reinicia**, começando uma
utterance nova.

A v1.2 ignorava o retorno de `AcceptWaveform` e lia só `FinalResult()` no
fim. Ou seja: tudo que o endpointer fechou pelo caminho era descartado, e
sobrava apenas o último trecho. Uma frase com qualquer pausa interna perdia
o começo — exatamente o sintoma relatado.

A correção é acumular TODOS os segmentos: cada `AcceptWaveform() is True`
tem seu `Result()` lido e guardado, e no fim `FinalResult()` acrescenta o
que restou. Nenhuma palavra é descartada.
"""

import json
import logging
from pathlib import Path

import vosk

from services.stt_service import BufferedSTTProvider, STTEngine, STTUnavailableError

logger = logging.getLogger(__name__)

vosk.SetLogLevel(-1)  # silencia o log nativo do Kaldi — ruído irrelevante aqui

_SAMPLE_RATE = 16000
# Pedaços de ~0.5s ao alimentar o reconhecedor. Só afeta a granularidade do
# endpointing interno do Kaldi; o áudio já está todo em memória.
_FEED_CHUNK_BYTES = 16000


def _text_of(result_json: str) -> str:
    try:
        return str(json.loads(result_json).get("text", "")).strip()
    except (ValueError, AttributeError):
        return ""


class VoskSTTProvider(BufferedSTTProvider):
    def __init__(self, *, model_path: Path, device_key: str | None = None, vad=None) -> None:
        model_path = Path(model_path)
        if not model_path.is_dir():
            raise STTUnavailableError(f"Modelo Vosk não encontrado em: {model_path}")
        super().__init__(device_key=device_key, vad=vad)
        self._model = vosk.Model(str(model_path))
        self._model_path = model_path

    @property
    def engine(self) -> STTEngine:
        return STTEngine.VOSK

    def is_available(self) -> bool:
        return True

    @property
    def model_path(self) -> Path:
        return self._model_path

    def transcribe_pcm(self, pcm: bytes) -> str:
        """Transcreve o buffer inteiro acumulando TODOS os segmentos.

        Ver o cabeçalho do módulo: descartar os `Result()` intermediários era
        a causa raiz da perda de palavras. Nunca voltar a ignorar o retorno de
        `AcceptWaveform`."""
        if not pcm:
            return ""

        recognizer = vosk.KaldiRecognizer(self._model, _SAMPLE_RATE)
        recognizer.SetWords(False)

        segments: list[str] = []
        for offset in range(0, len(pcm), _FEED_CHUNK_BYTES):
            chunk = pcm[offset : offset + _FEED_CHUNK_BYTES]
            if recognizer.AcceptWaveform(chunk):
                segment = _text_of(recognizer.Result())
                if segment:
                    segments.append(segment)

        final = _text_of(recognizer.FinalResult())
        if final:
            segments.append(final)

        return " ".join(segments).strip()
