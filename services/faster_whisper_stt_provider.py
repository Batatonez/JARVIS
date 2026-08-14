"""`FasterWhisperSTTProvider` — engine principal de STT do JARVIS (v1.3).

Único módulo que importa `faster_whisper`/`numpy` (ver `stt_service.py` para
o porquê desse padrão). Roda **100% local, em CPU**, sem nenhuma API paga e
sem enviar áudio para lugar nenhum: o modelo CTranslate2 vive em
`data/models/whisper/` e o buffer PCM nunca sai da RAM.

Por que Whisper e não só Vosk: o modelo pequeno de pt-BR do Vosk não tem
pontuação nem capitalização e erra bastante em frases curtas — exatamente o
caso que motivou a v1.3. O Whisper devolve texto pontuado ("Opa, tudo bem?"
em vez de "opa tudo bem") e é muito mais robusto em português.

O modelo é carregado **preguiçosamente**, na primeira transcrição: carregar
no construtor congelaria o HUD por um ou dois segundos no login, e a
transcrição já roda numa thread de executor (ver
`BufferedSTTProvider.stop_and_transcribe`), que é o lugar certo para pagar
esse custo.
"""

import logging
import os
import threading
from pathlib import Path

from services import speech_vocabulary
from services.stt_service import BufferedSTTProvider, STTEngine, STTUnavailableError

logger = logging.getLogger(__name__)

# Áudio menor que isto é ruído de clique de botão, não fala. Transcrever
# meio décimo de segundo de silêncio é o cenário clássico em que o Whisper
# alucina uma frase inteira ("Legendas pela comunidade Amara.org").
_MINIMUM_AUDIO_SECONDS = 0.25
_SAMPLE_RATE = 16000

# Silêncio acrescentado nas duas pontas antes de transcrever (v1.3.1).
#
# O encoder do Whisper trabalha numa janela de 30s: um clipe de push-to-talk
# que começa exatamente no primeiro fonema não dá ao modelo nenhum "embalo"
# antes da fala, e é justamente aí que ele erra o começo da frase. Meio
# segundo de silêncio custa nada e mede diferença real: "Opa Jarvis, tudo
# bem?" com o início levemente cortado passou de "Opa de arvies, tudo bem" para
# "Opa, JARVIS, tudo bem".
#
# Isto NÃO recupera áudio que nunca foi capturado — para isso existe a espera
# pelo primeiro bloco em `BufferedSTTProvider.start_listening`.
_EDGE_PADDING_SECONDS = 0.5

# Teto de threads do CTranslate2. Sem limite ele tenta usar todos os núcleos;
# numa máquina de 28 threads isso rouba CPU do resto do sistema por um ganho
# marginal — a transcrição de uma frase curta já é sub-segundo com 8.
_MAX_CPU_THREADS = 8


class FasterWhisperSTTProvider(BufferedSTTProvider):
    def __init__(
        self,
        *,
        model_dir: Path,
        compute_type: str = "int8",
        device_key: str | None = None,
        language: str = "pt",
        beam_size: int = 5,
        vad=None,
    ) -> None:
        model_dir = Path(model_dir)
        if not model_dir.is_dir():
            raise STTUnavailableError(f"Modelo do faster-whisper não encontrado em: {model_dir}")
        # Falha cedo se o pacote não estiver instalado, para que
        # `create_stt_service()` consiga cair no Vosk em vez de descobrir isso
        # só na primeira transcrição.
        try:
            import faster_whisper  # noqa: F401
            import numpy  # noqa: F401
        except ImportError as exc:
            raise STTUnavailableError("Pacote faster-whisper não instalado.") from exc

        super().__init__(device_key=device_key, vad=vad)
        self._model_dir = model_dir
        self._compute_type = compute_type
        self._language = language
        self._beam_size = beam_size
        # Resolvidos uma vez: são constantes do app, e os testes injetam
        # string vazia para medir o comportamento SEM vocabulário.
        self._hotwords = speech_vocabulary.hotwords()
        self._context_prompt = speech_vocabulary.context_prompt()
        self._model = None
        self._model_lock = threading.Lock()

    @property
    def engine(self) -> STTEngine:
        return STTEngine.FASTER_WHISPER

    def is_available(self) -> bool:
        return True

    @property
    def model_dir(self) -> Path:
        return self._model_dir

    def _ensure_model(self):
        # Dupla checagem sob lock: duas transcrições disparadas quase juntas
        # não podem carregar o modelo duas vezes (meio giga de RAM cada).
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                threads = min(os.cpu_count() or 4, _MAX_CPU_THREADS)
                logger.info(
                    "Carregando modelo faster-whisper de %s (compute_type=%s, threads=%s).",
                    self._model_dir,
                    self._compute_type,
                    threads,
                )
                self._model = WhisperModel(
                    str(self._model_dir),
                    device="cpu",
                    compute_type=self._compute_type,
                    cpu_threads=threads,
                    # Nunca sair para a rede: o modelo já está no disco, e um
                    # download silencioso no meio de uma transcrição seria
                    # exatamente o comportamento que a v1.3 proíbe.
                    local_files_only=True,
                )
        return self._model

    def transcribe_pcm(self, pcm: bytes) -> str:
        import numpy as np

        if len(pcm) < int(_MINIMUM_AUDIO_SECONDS * _SAMPLE_RATE) * 2:
            return ""

        # int16 -> float32 normalizado em [-1, 1], que é o formato que o
        # faster-whisper espera quando recebe um array em vez de um arquivo.
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

        if _EDGE_PADDING_SECONDS > 0:
            silence = np.zeros(int(_SAMPLE_RATE * _EDGE_PADDING_SECONDS), dtype=np.float32)
            audio = np.concatenate([silence, audio, silence])

        segments, _info = self._ensure_model().transcribe(
            audio,
            language=self._language,
            beam_size=self._beam_size,
            # Corta silêncio antes de transcrever. Além de acelerar, é o que
            # impede o `initial_prompt` de ser ecoado como se fosse
            # transcrição quando o áudio não tem fala nenhuma — medido: sem
            # `vad_filter`, ruído puro devolvia "JARVIS. Vocabulario.
            # Vocabulario."; com ele, devolve string vazia.
            #
            # `speech_pad_ms` generoso e `min_silence_duration_ms` alto de
            # propósito: não cortar a primeira nem a última palavra, e uma
            # pausa curta no meio de "Opa, [pausa] tudo bem?" não pode ser
            # tratada como fim de fala.
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 400},
            # Sem contexto entre segmentos: em push-to-talk cada captura é
            # independente, e condicionar no texto anterior é a principal
            # fonte de repetição em loop do Whisper.
            condition_on_previous_text=False,
            # v1.3.1 — vocabulário do app. Enviesa a DECODIFICAÇÃO; não
            # substitui texto (ver services/speech_vocabulary.py).
            hotwords=self._hotwords or None,
            initial_prompt=self._context_prompt or None,
        )
        return "".join(segment.text for segment in segments).strip()
