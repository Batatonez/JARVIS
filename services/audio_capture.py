"""Captura de áudio do microfone (v1.3) — camada única compartilhada por
todos os engines de STT.

Até a v1.2 a captura vivia dentro de `vosk_stt_provider.py`, misturada com o
reconhecedor. Isso produziu o bug que motivou esta versão: o provider
alimentava o Kaldi em streaming, o endpointer do Vosk fechava uma utterance
no meio da frase, e o texto dela era **descartado** (só `FinalResult()` era
lido). "Opa, tudo bem?" virava "bem".

A v1.3 separa as responsabilidades:

    AudioCapture  -> devolve um buffer PCM completo (int16 mono 16kHz)
    Provider      -> transcreve o buffer inteiro

Nenhum áudio é escrito em disco: os blocos vivem em `list[bytes]` na RAM e
morrem quando a captura termina. 16kHz mono int16 = 32 KB/s, então o teto de
`VadSettings.max_seconds` mantém o uso de memória na casa de poucos MB.

O nível de entrada (0.0-1.0) é calculado com `array` da biblioteca padrão —
`audioop` saiu do Python 3.13 e o `numpy` (que hoje entra junto com o
faster-whisper) não deve virar requisito da captura, que precisa funcionar
mesmo no caminho só-Vosk de máquinas fracas.
"""

import asyncio
import logging
from array import array
from collections.abc import Callable
from dataclasses import dataclass

from services.audio_devices import AudioDevice

logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2

# 30ms de bloco: ~33 callbacks/s. Menor que os 50ms da v1.2 (melhora a
# resolução do medidor de nível e do VAD) e ainda muito longe de sobrecarregar
# a CPU — cada callback só faz uma soma de quadrados sobre 480 amostras.
_BLOCK_SECONDS = 0.03
# Divisor empírico para converter RMS de int16 em 0.0-1.0 numa fala próxima ao
# microfone. Só afeta a barra do HUD e os limiares do VAD, que são relativos.
_LEVEL_NORMALIZATION = 9000.0


class AudioCaptureError(Exception):
    """Falha real ao abrir/ler o microfone — mensagem já apresentável ao usuário."""


def rms_level(samples: array) -> float:
    """Nível 0.0-1.0 de um bloco de amostras int16."""
    if not samples:
        return 0.0
    mean_square = sum(s * s for s in samples) / len(samples)
    return min(1.0, (mean_square**0.5) / _LEVEL_NORMALIZATION)


class LinearResampler:
    """Resample int16 mono para a taxa alvo.

    **Caminho de exceção, não o normal.** Desde a v1.3 a `AudioCapture` tenta
    abrir o dispositivo direto em 16kHz (o PortAudio/WASAPI faz a conversão no
    motor de áudio do Windows, que é melhor que qualquer coisa que a gente
    faria aqui). Este resampler só entra quando o dispositivo recusa 16kHz.

    Duas correções em cima da versão da v1.2:

    1. **Borda de bloco.** A v1.2 guardava `self._pos = pos - n`, o que podia
       deixar a posição NEGATIVA no bloco seguinte (`int(-0.4) == 0` trunca em
       direção ao zero, e a fração virava negativa: extrapolação para trás,
       fora do sinal). Agora a cauda de cada bloco é preservada como âncora do
       próximo, então a interpolação atravessa a borda corretamente.

    2. **Anti-aliasing.** Decimar sem passa-baixa dobra as frequências acima
       de 8kHz de volta para dentro da banda. Um filtro de média móvel de
       largura ~`ratio` agora roda antes da interpolação (só quando
       `ratio >= 1.5`; abaixo disso a largura arredonda para 1 e o filtro é
       inerte por construção).

    **Limitação medida, não escondida.** No benchmark da v1.3, a frase "Abre
    um novo chat" saiu correta quando decodificada por um resampler polifásico
    de verdade (o do `av`) e como "chate" passando por este resampler — com e
    sem o filtro. Ou seja: interpolação linear perde qualidade contra um sinc
    janelado, e a média móvel não recupera essa diferença. É por isso que a
    correção principal da v1.3 é NÃO usar este caminho: a `AudioCapture`
    negocia 16kHz direto com o dispositivo e só cai aqui quando o driver
    recusa. Implementar um sinc janelado exigiria `numpy` na captura, que
    precisa continuar funcionando no caminho só-Vosk de máquinas fracas.
    """

    def __init__(self, from_rate: int, to_rate: int) -> None:
        if from_rate <= 0 or to_rate <= 0:
            raise ValueError("Taxas de amostragem precisam ser positivas.")
        self._ratio = from_rate / to_rate
        self._pos = 0.0
        self._tail: array = array("h")
        # Só faz sentido filtrar quando se está jogando amostras fora.
        self._filter_width = max(1, round(self._ratio)) if self._ratio > 1.0 else 1
        self._filter_state: array = array("h")

    @property
    def active(self) -> bool:
        return self._ratio != 1.0

    def _antialias(self, samples: array) -> array:
        """Média móvel de `_filter_width` amostras — passa-baixa barato que
        corta o essencial do aliasing sem `numpy`/`scipy`."""
        if self._filter_width <= 1:
            return samples
        window = self._filter_state + samples
        out = array("h")
        width = self._filter_width
        for index in range(len(self._filter_state), len(window)):
            start = max(0, index - width + 1)
            chunk = window[start : index + 1]
            out.append(int(sum(chunk) / len(chunk)))
        # Guarda as últimas (width-1) amostras para o filtro atravessar a
        # borda do próximo bloco sem descontinuidade.
        self._filter_state = window[-(width - 1) :] if width > 1 else array("h")
        return out

    def process(self, samples: array) -> array:
        if not self.active:
            return samples

        filtered = self._antialias(samples)
        # A cauda do bloco anterior entra na frente: sem ela, a interpolação
        # recomeçaria do zero a cada callback e perderia a amostra da borda.
        buffer = self._tail + filtered
        out = array("h")
        n = len(buffer)
        if n < 2:
            self._tail = buffer
            return out

        pos = self._pos
        while True:
            index = int(pos)
            if index + 1 >= n:
                break
            frac = pos - index
            a = buffer[index]
            b = buffer[index + 1]
            out.append(int(a + (b - a) * frac))
            pos += self._ratio

        consumed = int(pos)
        self._tail = buffer[consumed:]
        self._pos = pos - consumed
        return out

    def flush(self) -> array:
        """Últimas amostras pendentes. Chamado uma vez no fim da captura para
        que a sílaba final não fique presa na cauda do resampler."""
        tail, self._tail = self._tail, array("h")
        self._pos = 0.0
        self._filter_state = array("h")
        return tail


@dataclass(frozen=True)
class VadSettings:
    """Limiares do detector de fim de fala.

    Os valores default seguem o item 6 da v1.3: NÃO cortar a primeira palavra,
    nem palavras do meio, nem o final da frase — e ainda assim não ouvir para
    sempre.

    `trailing_silence_seconds` é deliberadamente generoso (1.2s). Um valor
    agressivo como 0.4s corta exatamente o tipo de frase que o usuário
    reclamou: quem diz "Opa, tudo bem?" faz uma micro-pausa depois de "Opa".

    `min_speech_seconds` impede que um estalo curto encerre a captura antes de
    a pessoa começar de fato.
    """

    calibration_seconds: float = 0.35
    min_speech_seconds: float = 0.4
    trailing_silence_seconds: float = 1.2
    max_seconds: float = 60.0
    # Sem fala nenhuma, encerra mesmo assim — senão "Test Microphone" ficaria
    # aberto indefinidamente num PC sem microfone funcionando.
    silence_timeout_seconds: float = 8.0
    # Limiar = max(absolute_floor, ruído_medido * multiplier). O multiplicador
    # cobre ambiente barulhento; o piso absoluto cobre um ambiente tão quieto
    # que o ruído medido é ~0 e qualquer múltiplo dele continuaria ~0.
    noise_multiplier: float = 3.0
    absolute_floor: float = 0.02


class SilenceDetector:
    """Decide QUANDO parar de gravar, a partir da sequência de níveis.

    Lógica pura, sem áudio nem threads: os testes alimentam níveis
    sintéticos e verificam o comportamento sem nenhum microfone real
    (item 63/64 da v1.3).
    """

    def __init__(self, settings: VadSettings | None = None) -> None:
        self._settings = settings or VadSettings()
        self._elapsed = 0.0
        self._noise_sum = 0.0
        self._noise_blocks = 0
        self._threshold: float | None = None
        self._speech_started_at: float | None = None
        self._silence_run = 0.0
        self._last_speech_at = 0.0

    @property
    def settings(self) -> VadSettings:
        return self._settings

    @property
    def speech_detected(self) -> bool:
        return self._speech_started_at is not None

    @property
    def threshold(self) -> float | None:
        """`None` enquanto a calibração de ruído ainda não terminou."""
        return self._threshold

    def feed(self, level: float, duration: float) -> bool:
        """Consome um bloco. Devolve `True` quando a captura deve terminar."""
        self._elapsed += duration

        if self._elapsed >= self._settings.max_seconds:
            logger.info("Captura encerrada pelo teto de %.0fs.", self._settings.max_seconds)
            return True

        # Fase 1: mede o ruído de fundo antes de julgar qualquer coisa. Nunca
        # encerra durante a calibração — é justamente o começo da frase.
        if self._elapsed <= self._settings.calibration_seconds:
            self._noise_sum += level
            self._noise_blocks += 1
            return False

        if self._threshold is None:
            noise = (self._noise_sum / self._noise_blocks) if self._noise_blocks else 0.0
            self._threshold = max(
                self._settings.absolute_floor, noise * self._settings.noise_multiplier
            )

        if level >= self._threshold:
            if self._speech_started_at is None:
                self._speech_started_at = self._elapsed
            self._last_speech_at = self._elapsed
            self._silence_run = 0.0
            return False

        self._silence_run += duration

        if self._speech_started_at is None:
            # Ninguém falou ainda: só o timeout de silêncio se aplica.
            return self._elapsed >= self._settings.silence_timeout_seconds

        speech_duration = self._last_speech_at - self._speech_started_at
        if speech_duration + self._silence_run < self._settings.min_speech_seconds:
            return False
        return self._silence_run >= self._settings.trailing_silence_seconds


class AudioCapture:
    """Abre um dispositivo de entrada e acumula PCM int16 mono 16kHz.

    Só este módulo importa `sounddevice` (import preguiçoso, dentro de
    `start()`), pelo mesmo motivo de `stt_service.py` não importar `vosk`.
    """

    def __init__(
        self,
        *,
        device: AudioDevice | None = None,
        on_level: Callable[[float], None] | None = None,
        vad: VadSettings | None = None,
        auto_stop: bool = False,
    ) -> None:
        self._device = device
        self._on_level = on_level
        self._detector = SilenceDetector(vad)
        self._auto_stop = auto_stop

        self._stream = None
        self._resampler: LinearResampler | None = None
        self._blocks: list[bytes] = []
        self._active = False
        self._auto_stopped = False
        self._receiving = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def receiving(self) -> bool:
        """`True` depois que o PRIMEIRO bloco de áudio chegou de verdade.

        `stream.start()` retornar não significa que o dispositivo já está
        entregando amostras — no WASAPI há uma latência de arranque. Quem
        avisa o usuário que pode falar precisa esperar por isto, senão o
        começo da frase acontece antes de existir captura (v1.3.1)."""
        return self._receiving

    @property
    def auto_stopped(self) -> bool:
        """`True` quando o VAD encerrou sozinho (silêncio/teto), e não o usuário."""
        return self._auto_stopped

    @property
    def device(self) -> AudioDevice | None:
        return self._device

    def captured_seconds(self) -> float:
        total = sum(len(block) for block in self._blocks)
        return total / (TARGET_SAMPLE_RATE * BYTES_PER_SAMPLE)

    def start(self) -> None:
        if self._active:
            return
        if self._device is None:
            raise AudioCaptureError("Nenhum microfone disponível.")

        try:
            import sounddevice as sd
        except Exception as exc:
            raise AudioCaptureError("Captura de áudio indisponível neste ambiente.") from exc

        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        # Preferir 16kHz DIRETO do dispositivo (v1.3): quando o PortAudio
        # aceita a taxa alvo, quem converte é o motor de áudio do Windows, com
        # qualidade muito acima do resampler caseiro — e o áudio chega pronto,
        # sem nenhuma conversão nossa. O resampler só entra se o dispositivo
        # recusar 16kHz.
        capture_rate = self._negotiate_samplerate(sd)
        self._resampler = (
            LinearResampler(capture_rate, TARGET_SAMPLE_RATE)
            if capture_rate != TARGET_SAMPLE_RATE
            else None
        )
        self._blocks = []
        self._auto_stopped = False
        self._receiving = False

        block_size = max(1, int(capture_rate * _BLOCK_SECONDS))
        try:
            self._stream = sd.RawInputStream(
                device=self._device.index,
                samplerate=capture_rate,
                blocksize=block_size,
                dtype="int16",
                channels=1,
                # `latency="low"` reduz o atraso entre `start()` e o primeiro
                # callback — é o que evita perder o ataque da primeira palavra
                # em push-to-talk (item 6).
                latency="low",
                callback=self._on_audio,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise AudioCaptureError(f"Não foi possível abrir o microfone: {exc}") from exc

        self._active = True

    def _negotiate_samplerate(self, sd) -> int:
        """16kHz se o dispositivo aceitar; senão a taxa nativa dele.

        Nunca pedimos uma taxa sem checar antes: um driver que "aceita"
        qualquer coisa e devolve áudio distorcido em silêncio é o pior caso
        possível para reconhecimento de fala (foi o motivo do comentário de
        sample rate na v0.9)."""
        assert self._device is not None
        try:
            sd.check_input_settings(
                device=self._device.index, samplerate=TARGET_SAMPLE_RATE, channels=1, dtype="int16"
            )
            return TARGET_SAMPLE_RATE
        except Exception:
            native = self._device.default_samplerate or TARGET_SAMPLE_RATE
            logger.info(
                "Dispositivo não aceita %sHz; capturando a %sHz e convertendo.",
                TARGET_SAMPLE_RATE,
                native,
            )
            return int(native)

    def _on_audio(self, indata, frames, time_info, status) -> None:
        """Roda na thread de áudio do PortAudio. Nunca toca asyncio/Qt
        diretamente — só `call_soon_threadsafe`."""
        samples = array("h")
        samples.frombytes(bytes(indata))
        self._receiving = True

        level = rms_level(samples)
        if self._on_level is not None:
            self._emit_level(level)

        if self._resampler is not None:
            converted = self._resampler.process(samples)
            if converted:
                self._blocks.append(converted.tobytes())

        if self._auto_stop and self._detector.feed(level, _BLOCK_SECONDS):
            self._auto_stopped = True
            self._request_stop()

    def _emit_level(self, level: float) -> None:
        callback = self._on_level
        if callback is None:
            return
        if self._loop is None:
            callback(level)
            return
        try:
            self._loop.call_soon_threadsafe(callback, level)
        except RuntimeError:
            pass  # event loop já encerrado (shutdown em andamento)

    def _request_stop(self) -> None:
        """Sinaliza fim de captura a partir da thread de áudio. Não fecha o
        stream aqui: `stream.stop()` chamado de dentro do próprio callback
        trava no PortAudio. Quem fecha é `stop()`, na thread normal."""
        self._active = False

    def stop(self) -> bytes:
        """Encerra a captura e devolve todo o PCM (int16 mono 16kHz)."""
        self._close_stream()
        if self._resampler is not None:
            tail = self._resampler.flush()
            if tail:
                self._blocks.append(tail.tobytes())
            self._resampler = None
        audio = b"".join(self._blocks)
        self._blocks = []
        return audio

    def cancel(self) -> None:
        """Encerra descartando o áudio — usado quando o usuário cancela."""
        self._close_stream()
        self._resampler = None
        self._blocks = []

    def _close_stream(self) -> None:
        self._active = False
        self._receiving = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logger.debug("Falha ao fechar o stream de áudio (pode já estar fechado).")
            self._stream = None
