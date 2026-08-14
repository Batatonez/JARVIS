"""Abstração de Speech-to-Text (STT) do JARVIS.

    SpeechToTextService
        ├── UnavailableSTTService       (sem microfone/modelo/dependência)
        └── BufferedSTTProvider         (captura em buffer, transcreve no fim)
                ├── FasterWhisperSTTProvider  (services/faster_whisper_stt_provider.py)
                └── VoskSTTProvider           (services/vosk_stt_provider.py)

**v1.3 — por que a arquitetura mudou.** Até a v1.2 havia um único engine
(Vosk) alimentado em streaming, e o resultado era ruim em português: "Opa,
tudo bem?" saía como "bem". A causa raiz não era o modelo — era o modo de
uso (ver `services/vosk_stt_provider.py`). Corrigido isso, o Vosk pequeno de
pt-BR ainda é limitado, então a v1.3 promove o **faster-whisper** a engine
principal e mantém o **Vosk como fallback leve** para máquinas fracas ou
para quem não quer baixar o modelo do Whisper.

Ambos os engines herdam de `BufferedSTTProvider`: a captura acontece uma vez
só, em `services/audio_capture.py`, e o provider recebe o buffer PCM inteiro
para transcrever. Isso elimina por construção toda a classe de bug de
"pedaço da frase perdido entre resultados parciais e finais".

`create_stt_service()` nunca levanta exceção e **nunca baixa nada**: sem
modelo instalado o JARVIS segue funcionando por texto (voz é capability
opcional). Este módulo não importa `faster_whisper`, `vosk` nem
`sounddevice` — só os providers concretos fazem isso, e só quando de fato
vão ser construídos.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING

from services.audio_capture import AudioCapture, AudioCaptureError, VadSettings
from services.audio_devices import AudioDevice, DeviceResolution, resolve_input_device

if TYPE_CHECKING:
    from config.settings import Settings

logger = logging.getLogger(__name__)


class STTStatus(Enum):
    READY = "ready"  # engine carregado + microfone presente
    SETUP_REQUIRED = "setup_required"  # dependências ok, mas nenhum modelo instalado
    NO_MICROPHONE = "no_microphone"  # engine pronto, mas nenhum microfone detectado
    UNAVAILABLE = "unavailable"  # dependência ausente, kill-switch desligado, ou falha real


class STTEngine(Enum):
    """Qual engine está de fato atendendo. O HUD mostra isto ao usuário —
    saber que caiu no fallback é informação legítima, não detalhe interno."""

    NONE = "none"
    FASTER_WHISPER = "faster_whisper"
    VOSK = "vosk"

    @property
    def label(self) -> str:
        return {
            STTEngine.NONE: "—",
            STTEngine.FASTER_WHISPER: "Faster Whisper",
            STTEngine.VOSK: "Vosk",
        }[self]


class STTUnavailableError(Exception):
    """Levantada quando não é possível capturar ou transcrever áudio."""


class SpeechToTextService(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        """Engine/modelo prontos para uso (independente de haver microfone)."""

    @property
    @abstractmethod
    def status(self) -> STTStatus:
        """Diagnóstico real do porquê — usado pelo HUD para decidir o que
        mostrar/fazer (nunca um "unavailable" genérico quando dá pra ser
        específico)."""

    @property
    @abstractmethod
    def microphone_available(self) -> bool:
        """Existe um dispositivo de entrada de áudio utilizável."""

    @abstractmethod
    async def start_listening(self, *, on_level: Callable[[float], None] | None = None) -> None:
        """Abre o microfone e começa a capturar. `on_level` (opcional) é
        chamado com um valor 0.0-1.0 de nível de áudio, já throttled pelo
        provider (nunca em alta frequência)."""

    @abstractmethod
    async def stop_and_transcribe(self) -> str:
        """Para a captura e devolve o texto reconhecido (pode ser string
        vazia se nada foi entendido). Levanta `STTUnavailableError` em falha
        real (dispositivo caiu, engine travou etc.)."""

    @abstractmethod
    async def cancel(self) -> None:
        """Para a captura sem transcrever, descartando o áudio capturado."""

    # --- v1.3: seleção de dispositivo -----------------------------------
    # Concretos de propósito (e não `@abstractmethod`): um serviço de STT que
    # não gerencia dispositivos — o placeholder, ou um fake de teste —
    # continua válido sem precisar reimplementar nada. Adicionar isto como
    # abstrato quebraria todo fake existente sem ganho real.

    @property
    def engine(self) -> STTEngine:
        return STTEngine.NONE

    def available_devices(self) -> list[AudioDevice]:
        return []

    @property
    def current_device(self) -> AudioDevice | None:
        return None

    @property
    def device_fell_back(self) -> bool:
        """`True` quando o microfone salvo não existe mais e caímos no padrão
        do sistema — o HUD avisa discretamente (item 14 da v1.3)."""
        return False

    def select_device(self, device_key: str | None) -> bool:
        """Troca o dispositivo de captura. `False` se não foi possível."""
        return False

    def refresh_devices(self) -> list[AudioDevice]:
        """Re-enumera (item 15 — microfone conectado depois do app aberto)."""
        return self.available_devices()


class UnavailableSTTService(SpeechToTextService):
    """Placeholder: nenhum engine de reconhecimento de fala está pronto.
    `status` carrega o motivo real (setup pendente vs. indisponível de
    verdade) — nunca escondido atrás de um booleano só."""

    def __init__(self, status: STTStatus = STTStatus.UNAVAILABLE) -> None:
        self._status = status

    def is_available(self) -> bool:
        return False

    @property
    def status(self) -> STTStatus:
        return self._status

    @property
    def microphone_available(self) -> bool:
        return False

    async def start_listening(self, *, on_level: Callable[[float], None] | None = None) -> None:
        raise STTUnavailableError("Reconhecimento de fala não está disponível.")

    async def stop_and_transcribe(self) -> str:
        raise STTUnavailableError("Reconhecimento de fala não está disponível.")

    async def cancel(self) -> None:
        return None

    def available_devices(self) -> list[AudioDevice]:
        # Mesmo sem engine, listar microfones é informação verdadeira e útil:
        # o HUD consegue mostrar o seletor enquanto o modelo ainda não foi
        # instalado, em vez de uma tela vazia sem explicação.
        from services.audio_devices import list_input_devices

        return list_input_devices()


class BufferedSTTProvider(SpeechToTextService):
    """Base dos engines reais: cuida de dispositivo, captura e ciclo de vida;
    a subclasse só precisa saber transcrever um buffer PCM.

    A transcrição roda em executor (`run_in_executor`) porque tanto o
    faster-whisper quanto o Vosk são síncronos e pesados de CPU — rodar isso
    direto no event loop congelaria o HUD durante a transcrição.
    """

    def __init__(self, *, device_key: str | None = None, vad: VadSettings | None = None) -> None:
        self._vad = vad or VadSettings()
        self._resolution: DeviceResolution = resolve_input_device(device_key)
        self._capture: AudioCapture | None = None

    # --- dispositivo ----------------------------------------------------

    @property
    def microphone_available(self) -> bool:
        return self._resolution.device is not None

    @property
    def current_device(self) -> AudioDevice | None:
        return self._resolution.device

    @property
    def device_fell_back(self) -> bool:
        return self._resolution.fell_back

    def available_devices(self) -> list[AudioDevice]:
        from services.audio_devices import list_input_devices

        return list_input_devices()

    def select_device(self, device_key: str | None) -> bool:
        if self._capture is not None and self._capture.active:
            return False  # nunca trocar o dispositivo no meio de uma captura
        self._resolution = resolve_input_device(device_key)
        return self._resolution.device is not None

    def refresh_devices(self) -> list[AudioDevice]:
        devices = self.available_devices()
        # Re-resolve mantendo a preferência original: um microfone que voltou
        # a existir deve ser readotado, não ignorado até reiniciar o app.
        self._resolution = resolve_input_device(self._resolution.requested_key or None)
        return devices

    @property
    def status(self) -> STTStatus:
        return STTStatus.READY if self.microphone_available else STTStatus.NO_MICROPHONE

    # --- captura --------------------------------------------------------

    async def start_listening(
        self,
        *,
        on_level: Callable[[float], None] | None = None,
        auto_stop: bool = False,
    ) -> None:
        if self._capture is not None and self._capture.active:
            return
        if self._resolution.device is None:
            raise STTUnavailableError("Nenhum microfone disponível.")

        capture = AudioCapture(
            device=self._resolution.device,
            on_level=on_level,
            vad=self._vad,
            auto_stop=auto_stop,
        )
        try:
            capture.start()
        except AudioCaptureError as exc:
            raise STTUnavailableError(str(exc)) from exc
        self._capture = capture

    async def stop_and_transcribe(self) -> str:
        capture = self._capture
        if capture is None:
            raise STTUnavailableError("Nenhuma captura de áudio em andamento.")
        self._capture = None

        audio = capture.stop()
        if not audio:
            return ""
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self.transcribe_pcm, audio)
        except Exception as exc:
            logger.exception("Falha ao transcrever áudio.")
            raise STTUnavailableError("Não foi possível transcrever o áudio capturado.") from exc

    async def cancel(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.cancel()

    @property
    def listening(self) -> bool:
        return self._capture is not None and self._capture.active

    @abstractmethod
    def transcribe_pcm(self, pcm: bytes) -> str:
        """Transcreve PCM int16 mono 16kHz. Roda numa thread de executor —
        pode ser lento e bloqueante."""


def create_stt_service(
    settings: "Settings", *, device_key: str | None = None
) -> SpeechToTextService:
    """Escolhe o engine segundo a política do item 7 da v1.3:

        voice_input_enabled=False        -> UnavailableSTTService(UNAVAILABLE)
        faster-whisper pronto            -> FasterWhisperSTTProvider
        senão, Vosk pronto               -> VoskSTTProvider          (fallback)
        nenhum modelo instalado          -> UnavailableSTTService(SETUP_REQUIRED)
        dependência ausente/falha real   -> UnavailableSTTService(UNAVAILABLE)

    Nunca baixa modelo e nunca levanta exceção — um modelo ausente é um
    estado normal do sistema, não um erro.
    """
    if not settings.voice_input_enabled:
        return UnavailableSTTService(STTStatus.UNAVAILABLE)

    any_model_present = False

    if settings.stt_engine_preference != "vosk":
        from services.whisper_model_manager import WhisperModelManager

        whisper_manager = WhisperModelManager(
            models_dir=settings.whisper_models_dir, model_size=settings.whisper_model_size
        )
        if whisper_manager.is_installed:
            any_model_present = True
            try:
                from services.faster_whisper_stt_provider import FasterWhisperSTTProvider

                return FasterWhisperSTTProvider(
                    model_dir=whisper_manager.model_path,
                    compute_type=settings.whisper_compute_type,
                    device_key=device_key,
                )
            except Exception as exc:
                logger.warning("faster-whisper indisponível (%s); tentando o fallback Vosk.", exc)

    if settings.stt_model_path.is_dir():
        any_model_present = True
        try:
            from services.vosk_stt_provider import VoskSTTProvider

            return VoskSTTProvider(model_path=settings.stt_model_path, device_key=device_key)
        except Exception as exc:
            logger.info("Vosk indisponível (%s); JARVIS segue funcionando por texto.", exc)

    if not any_model_present:
        logger.info("STT: nenhum modelo instalado (setup necessário).")
        return UnavailableSTTService(STTStatus.SETUP_REQUIRED)
    return UnavailableSTTService(STTStatus.UNAVAILABLE)
