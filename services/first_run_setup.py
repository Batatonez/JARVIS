"""Passos de preparação do JARVIS — a lógica por trás de `python setup.py`.

Fica aqui (e não dentro do `setup.py`) por dois motivos: é testável sem
executar o script, e o `setup.py` continua sendo uma casca fina.

**Reusa a arquitetura existente, não a duplica.** Todo o download do modelo
de voz continua sendo feito por `services/vosk_model_manager.py`, que já
implementa HTTPS obrigatório, arquivo temporário, progresso real,
cancelamento, proteção contra Zip Slip e limpeza em caso de falha. Este
módulo só decide *se* precisa baixar e reporta o resultado — nenhuma
proteção foi reescrita nem afrouxada para o setup funcionar.

Nada aqui instala pacotes: `pip` é responsabilidade do `setup.py`, que roda
como processo. Assim os testes deste módulo nunca tocam a rede.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from services.vosk_model_manager import (
    ModelDownloadCancelled,
    ModelDownloadError,
    VoiceModelManager,
)

logger = logging.getLogger(__name__)


class StepStatus(str, Enum):
    OK = "ok"
    READY = "ready"
    ALREADY_PRESENT = "already_present"
    DOWNLOADED = "downloaded"
    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    MISSING = "missing"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class StepResult:
    status: StepStatus
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status not in (StepStatus.FAILED,)


# --- Dependências -----------------------------------------------------

# Módulos que a voz precisa em runtime. Os nomes de import, não os de
# pacote: é o import que falha quando falta algo (`PySide6-Essentials`
# instala `PySide6`, por exemplo).
_VOICE_IMPORTS = (("sounddevice", "sounddevice"), ("vosk", "vosk"))
# v1.3 — engine principal. Ausente, o JARVIS ainda funciona (cai no Vosk),
# então isto NÃO reprova o setup; só é reportado.
_WHISPER_IMPORTS = (("faster_whisper", "faster-whisper"), ("numpy", "numpy"))


def check_voice_dependencies() -> StepResult:
    """As dependências de áudio/STT estão importáveis?

    Não instala nada — quem instala é o `setup.py` via `requirements.txt`,
    a fonte única de versões (o `pyproject.toml` não declara dependências
    de propósito, ver comentário lá)."""
    missing: list[str] = []
    for import_name, package_name in _VOICE_IMPORTS:
        try:
            __import__(import_name)
        except Exception:  # ImportError, mas também erros de binário nativo
            missing.append(package_name)
    if missing:
        return StepResult(
            StepStatus.MISSING,
            f"faltando: {', '.join(missing)} (instale com: pip install -r requirements.txt)",
        )
    return StepResult(StepStatus.OK, "sounddevice, vosk")


def check_whisper_dependencies() -> StepResult:
    """`faster-whisper` disponível? Ausente não é falha — é o caminho de
    fallback (item 7 da v1.3)."""
    missing = []
    for import_name, package_name in _WHISPER_IMPORTS:
        try:
            __import__(import_name)
        except Exception:
            missing.append(package_name)
    if missing:
        return StepResult(StepStatus.MISSING, f"faltando: {', '.join(missing)}")
    return StepResult(StepStatus.OK, "faster-whisper, numpy")


def ensure_whisper_model(
    manager, *, on_progress=None, allow_download: bool = True
) -> StepResult:
    """Garante o modelo do faster-whisper instalado.

    Mesma política do Vosk (item 10): **não baixa de novo** se o modelo já
    está completo. `WhisperModelManager.is_installed` já é a checagem
    rigorosa (arquivos obrigatórios + tamanho mínimo), então não há um
    `is_complete` separado a consultar."""
    from services.whisper_model_manager import ModelDownloadError as WhisperDownloadError

    if manager.is_installed:
        size_mb = manager.installed_size_bytes() / 1_000_000
        return StepResult(
            StepStatus.ALREADY_PRESENT, f"{manager.model_path.name} ({size_mb:.0f} MB)"
        )

    if not allow_download:
        return StepResult(StepStatus.MISSING, "modelo ausente (download não solicitado)")

    try:
        manager.download_and_install(on_progress=on_progress)
    except WhisperDownloadError as exc:
        return StepResult(StepStatus.FAILED, str(exc))

    if not manager.is_installed:
        return StepResult(
            StepStatus.FAILED, "o download terminou, mas o modelo não passou na validação"
        )
    size_mb = manager.installed_size_bytes() / 1_000_000
    return StepResult(StepStatus.DOWNLOADED, f"{manager.model_path.name} ({size_mb:.0f} MB)")


def build_whisper_manager(settings):
    """Manager do modelo Whisper apontando para `data/models/whisper/`."""
    from services.whisper_model_manager import WhisperModelManager

    return WhisperModelManager(
        models_dir=Path(settings.whisper_models_dir), model_size=settings.whisper_model_size
    )


# --- Modelo de reconhecimento de fala ---------------------------------


def ensure_stt_model(
    manager: VoiceModelManager,
    *,
    on_progress=None,
    allow_download: bool = True,
) -> StepResult:
    """Garante o modelo Vosk instalado.

    Idempotente: se o modelo já está completo, **não baixa de novo** — é a
    diferença entre `is_installed` (existe algo?) e `is_complete` (existe
    por inteiro?). Uma extração interrompida deixa a pasta existindo, e só
    a segunda checagem percebe isso.

    `allow_download=False` permite inspecionar sem tocar a rede (usado no
    modo de verificação e nos testes)."""
    if manager.is_complete:
        size_mb = manager.installed_size_bytes() / 1_000_000
        return StepResult(StepStatus.ALREADY_PRESENT, f"{manager.model_path.name} ({size_mb:.0f} MB)")

    if not allow_download:
        return StepResult(StepStatus.MISSING, "modelo ausente (download não solicitado)")

    if manager.is_installed:
        # Pasta existe mas está incompleta: avisa antes de refazer, para o
        # usuário não achar que o download "repetiu à toa".
        logger.info("Modelo de voz incompleto em %s — refazendo o download.", manager.model_path)

    try:
        manager.download_and_install(on_progress=on_progress)
    except ModelDownloadCancelled:
        return StepResult(StepStatus.FAILED, "download cancelado")
    except ModelDownloadError as exc:
        return StepResult(StepStatus.FAILED, str(exc))

    if not manager.is_complete:
        # Download terminou mas o resultado não passa na validação —
        # melhor falhar claro do que declarar pronto algo que o Vosk não
        # vai conseguir carregar.
        return StepResult(
            StepStatus.FAILED,
            "o download terminou, mas o modelo extraído não parece completo",
        )

    size_mb = manager.installed_size_bytes() / 1_000_000
    return StepResult(StepStatus.DOWNLOADED, f"{manager.model_path.name} ({size_mb:.0f} MB)")


# --- Microfone --------------------------------------------------------


def detect_microphone() -> StepResult:
    """Há um dispositivo de entrada disponível?

    **Nunca falha o setup**: sem microfone o JARVIS continua utilizável por
    texto, e o HUD já sabe reportar `NO_MICROPHONE` (ver
    `services/stt_service.py`). Aqui é só informação.

    v1.3 — usa a mesma enumeração do resto do sistema
    (`services/audio_devices.py`), então o nome que o setup mostra é
    exatamente o que vai aparecer no seletor do HUD."""
    from services.audio_devices import default_input_device, list_input_devices

    try:
        import sounddevice  # noqa: F401
    except Exception:
        return StepResult(StepStatus.SKIPPED, "sounddevice não instalado")

    devices = list_input_devices()
    if not devices:
        return StepResult(StepStatus.NOT_DETECTED, "nenhum dispositivo de entrada")

    default = default_input_device()
    name = default.name if default is not None else devices[0].name
    extra = f" (+{len(devices) - 1} outro(s))" if len(devices) > 1 else ""
    return StepResult(StepStatus.DETECTED, f"{name}{extra}")


# --- Relatório --------------------------------------------------------


@dataclass(frozen=True)
class SetupReport:
    launcher: StepResult
    dependencies: StepResult
    speech_to_text: StepResult
    microphone: StepResult
    # v1.3 — engine principal e fallback reportados separadamente.
    engine: str = "—"
    fallback: str = "—"

    @property
    def succeeded(self) -> bool:
        """O microfone NUNCA reprova o setup — ver `detect_microphone`.

        O engine também não: com o Whisper indisponível mas o Vosk pronto, o
        STT está funcional, e é `speech_to_text` que carrega esse veredito."""
        return self.launcher.ok and self.dependencies.ok and self.speech_to_text.ok


def build_model_manager(settings) -> VoiceModelManager:
    """Manager apontando para o diretório de modelos do projeto
    (`settings.stt_models_dir`, dentro de `data/`, já ignorado pelo Git)."""
    return VoiceModelManager(models_dir=Path(settings.stt_models_dir))
