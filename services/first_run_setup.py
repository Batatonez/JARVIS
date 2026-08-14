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
_VOICE_IMPORTS = (("vosk", "vosk"), ("sounddevice", "sounddevice"))


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
    return StepResult(StepStatus.OK, "vosk, sounddevice")


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
    `services/stt_service.py`). Aqui é só informação."""
    try:
        import sounddevice as sd
    except Exception:
        return StepResult(StepStatus.SKIPPED, "sounddevice não instalado")

    try:
        device = sd.query_devices(kind="input")
    except Exception as exc:
        # Sem placa de som, sem driver, host de áudio indisponível...
        return StepResult(StepStatus.NOT_DETECTED, f"nenhum dispositivo de entrada ({exc})")

    if not device:
        return StepResult(StepStatus.NOT_DETECTED, "nenhum dispositivo de entrada")

    name = device.get("name", "desconhecido") if isinstance(device, dict) else str(device)
    rate = device.get("default_samplerate") if isinstance(device, dict) else None
    # O sample rate real do dispositivo é justamente o que o provider usa
    # (detecção adaptativa da v0.9) — mostrar aqui ajuda a diagnosticar.
    detail = f"{name} ({rate:.0f} Hz)" if rate else str(name)
    return StepResult(StepStatus.DETECTED, detail)


# --- Relatório --------------------------------------------------------


@dataclass(frozen=True)
class SetupReport:
    launcher: StepResult
    dependencies: StepResult
    speech_to_text: StepResult
    microphone: StepResult

    @property
    def succeeded(self) -> bool:
        """O microfone NUNCA reprova o setup — ver `detect_microphone`."""
        return self.launcher.ok and self.dependencies.ok and self.speech_to_text.ok


def build_model_manager(settings) -> VoiceModelManager:
    """Manager apontando para o diretório de modelos do projeto
    (`settings.stt_models_dir`, dentro de `data/`, já ignorado pelo Git)."""
    return VoiceModelManager(models_dir=Path(settings.stt_models_dir))
