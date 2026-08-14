"""`WhisperModelManager` — sabe tudo sobre o modelo local do faster-whisper:
se está instalado, onde, quanto ocupa e como baixar.

Mesmo contrato do `VoiceModelManager` (Vosk): **nunca** baixa sozinho, só sob
ação explícita do usuário (HUD) ou do `setup.py`. Um modelo ausente é um
estado normal — o JARVIS cai para o Vosk ou segue por texto.

Fonte: repositórios `Systran/faster-whisper-*` no Hugging Face — são os
modelos oficiais convertidos para CTranslate2 que o próprio `faster-whisper`
usa por padrão. O download passa pelo `huggingface_hub`, que valida hash de
cada arquivo; não há `.zip` envolvido, então não existe superfície de Zip
Slip aqui (diferente do Vosk — ver `vosk_model_manager._extract_safely`).

**Instalação atômica:** baixamos para um diretório temporário irmão do
destino e só renomeamos depois de validar. Uma queda de rede no meio deixa
lixo no temporário (removido no `finally`), nunca um modelo pela metade que
`is_installed` consideraria pronto.
"""

import logging
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Tamanhos aproximados em disco (float16, como publicado). Servem para a
# barra de progresso e para o aviso de "isto vai baixar X MB" — o valor real
# vem do próprio download.
_MODEL_CATALOG: dict[str, tuple[str, int]] = {
    "tiny": ("Systran/faster-whisper-tiny", 75_000_000),
    "base": ("Systran/faster-whisper-base", 145_000_000),
    "small": ("Systran/faster-whisper-small", 484_000_000),
    "medium": ("Systran/faster-whisper-medium", 1_530_000_000),
}

# Arquivos que um modelo CTranslate2 precisa ter para ser utilizável.
_REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
# Piso de tamanho: descarta um diretório com só os JSONs de metadata (o
# `model.bin` é ordens de magnitude maior que qualquer um deles).
_MINIMUM_VALID_MODEL_BYTES = 20 * 1024 * 1024

_PROGRESS_POLL_SECONDS = 0.4


class ModelDownloadError(Exception):
    """Falha real no download/instalação — mensagem já segura para o usuário."""


@dataclass(frozen=True)
class WhisperModelInfo:
    size: str
    repo_id: str
    approximate_size_bytes: int
    language: str = "Multilíngue (inclui português)"
    license: str = "MIT"
    source: str = "Hugging Face — Systran (modelos oficiais convertidos para CTranslate2)"


class WhisperModelManager:
    def __init__(self, *, models_dir: Path, model_size: str = "small") -> None:
        if model_size not in _MODEL_CATALOG:
            raise ValueError(
                f"Tamanho de modelo desconhecido: {model_size!r}. "
                f"Use um de: {', '.join(_MODEL_CATALOG)}."
            )
        self._models_dir = Path(models_dir)
        self._model_size = model_size
        self._model_path = self._models_dir / f"faster-whisper-{model_size}"
        self._cancel_requested = False

    @property
    def model_size(self) -> str:
        return self._model_size

    @property
    def model_path(self) -> Path:
        return self._model_path

    @property
    def is_installed(self) -> bool:
        """Rigoroso de propósito (diferente do `is_installed` frouxo do Vosk):
        aqui um diretório incompleto faria o `WhisperModel` estourar no meio
        de uma transcrição, então checamos os arquivos obrigatórios."""
        if not self._model_path.is_dir():
            return False
        for name in _REQUIRED_FILES:
            if not (self._model_path / name).is_file():
                return False
        return self.installed_size_bytes() >= _MINIMUM_VALID_MODEL_BYTES

    # Alias: o `setup.py` e o HUD falam a mesma língua para Vosk e Whisper.
    @property
    def is_complete(self) -> bool:
        return self.is_installed

    def installed_size_bytes(self) -> int:
        if not self._model_path.is_dir():
            return 0
        total = 0
        for path in self._model_path.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def info(self) -> WhisperModelInfo:
        repo_id, size_bytes = _MODEL_CATALOG[self._model_size]
        return WhisperModelInfo(
            size=self._model_size, repo_id=repo_id, approximate_size_bytes=size_bytes
        )

    def cancel_download(self) -> None:
        self._cancel_requested = True

    def download_and_install(
        self, *, on_progress: Callable[[int, int], None] | None = None
    ) -> None:
        """Baixa e instala o modelo. Só deve ser chamado em resposta a uma
        ação explícita do usuário. `on_progress` recebe
        `(bytes_baixados, bytes_totais_aproximados)`."""
        repo_id, approximate_total = _MODEL_CATALOG[self._model_size]
        self._cancel_requested = False
        self._models_dir.mkdir(parents=True, exist_ok=True)

        staging = self._models_dir / f".{self._model_path.name}.partial"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

        try:
            self._download_snapshot(repo_id, staging, approximate_total, on_progress)
            self._validate(staging)
            if self._model_path.exists():
                shutil.rmtree(self._model_path)
            staging.rename(self._model_path)
        except ModelDownloadError:
            raise
        except Exception as exc:
            logger.exception("Falha ao baixar/instalar o modelo do faster-whisper.")
            raise ModelDownloadError(
                "Não foi possível baixar o modelo de reconhecimento de fala. "
                "Verifique sua conexão e tente novamente."
            ) from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def _download_snapshot(
        self,
        repo_id: str,
        staging: Path,
        approximate_total: int,
        on_progress: Callable[[int, int], None] | None,
    ) -> None:
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise ModelDownloadError(
                "O pacote faster-whisper não está instalado. Rode `python setup.py`."
            ) from exc

        error: list[BaseException] = []

        def _work() -> None:
            try:
                snapshot_download(
                    repo_id=repo_id,
                    local_dir=str(staging),
                    # Só o que o CTranslate2 lê. Sem isto viriam também os
                    # pesos originais em PyTorch, que dobrariam o download
                    # sem nenhum uso.
                    allow_patterns=["*.json", "*.txt", "*.bin"],
                )
            except BaseException as exc:  # noqa: BLE001 — repassado à thread principal
                error.append(exc)

        worker = threading.Thread(target=_work, name="whisper-model-download", daemon=True)
        worker.start()

        # Progresso por tamanho em disco: o `snapshot_download` não expõe um
        # callback de bytes, e trocar a barra interna dele por uma nossa
        # dependeria de detalhe de implementação do huggingface_hub.
        while worker.is_alive():
            if self._cancel_requested:
                # Não há como interromper o download no meio com segurança; o
                # que garantimos é não INSTALAR o resultado.
                raise ModelDownloadError("Download cancelado.")
            if on_progress is not None:
                on_progress(_directory_size(staging), approximate_total)
            time.sleep(_PROGRESS_POLL_SECONDS)
        worker.join()

        if error:
            raise ModelDownloadError(
                "Não foi possível baixar o modelo de reconhecimento de fala."
            ) from error[0]
        if on_progress is not None:
            on_progress(_directory_size(staging), approximate_total)

    @staticmethod
    def _validate(staging: Path) -> None:
        """Valida ANTES de instalar (item 10): um diretório sem `model.bin`
        nunca deve tomar o lugar de um modelo que funcionava."""
        missing = [name for name in _REQUIRED_FILES if not (staging / name).is_file()]
        if missing:
            raise ModelDownloadError(
                f"Download incompleto: faltam {', '.join(missing)}. Nada foi instalado."
            )
        if _directory_size(staging) < _MINIMUM_VALID_MODEL_BYTES:
            raise ModelDownloadError("Download incompleto (arquivo menor que o esperado).")


def _directory_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total
