"""`VoiceModelManager`: sabe tudo sobre o modelo Vosk local — se está
instalado, onde, e como baixar (só sob consentimento explícito do usuário,
nunca sozinho, nunca automaticamente ao iniciar). Não fala com Qt/QML —
quem expõe isso ao HUD é o Bridge (ver `frontend/bridge.py`).

Fonte do modelo: alphacephei.com — site oficial dos mantenedores do Vosk
(https://alphacephei.com/vosk/models). Modelo pequeno com suporte a
pt-BR, licença Apache 2.0, ~45 MB.
"""

import logging
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_NAME = "vosk-model-small-pt-0.3"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODEL_LANGUAGE = "Português (Brasil)"
MODEL_APPROXIMATE_SIZE_BYTES = 45_000_000  # aproximado — o download real usa Content-Length do servidor
MODEL_LICENSE = "Apache 2.0"
MODEL_SOURCE = "alphacephei.com (mantenedores oficiais do Vosk)"

_DOWNLOAD_TIMEOUT_S = 30
_CHUNK_SIZE = 65536
# Piso para considerar um modelo extraído por inteiro. O modelo pequeno de
# pt-BR tem dezenas de MB descompactado; 5 MB descarta com folga uma pasta
# só com README ou uma extração interrompida, sem depender do tamanho exato
# de uma versão específica do modelo.
_MINIMUM_VALID_MODEL_BYTES = 5 * 1024 * 1024


class ModelDownloadError(Exception):
    """Falha real no download/instalação — mensagem já segura para mostrar ao usuário."""


class ModelDownloadCancelled(Exception):
    """Levantada quando `cancel_download()` é chamado durante um download em andamento."""


@dataclass(frozen=True)
class ModelInfo:
    name: str
    url: str
    language: str
    approximate_size_bytes: int
    license: str
    source: str


class VoiceModelManager:
    def __init__(self, *, models_dir: Path, model_dir_name: str = "vosk-model-small-pt") -> None:
        self._models_dir = Path(models_dir)
        self._model_path = self._models_dir / model_dir_name
        self._cancel_requested = False

    @property
    def model_path(self) -> Path:
        return self._model_path

    @property
    def is_installed(self) -> bool:
        # Um modelo Vosk de verdade tem várias subpastas (am/, conf/,
        # graph/...) — só confere que a pasta existe e não está vazia;
        # `VoskSTTProvider`/`vosk.Model` falha alto e claro se o conteúdo
        # estiver corrompido/incompleto (não escondemos esse erro aqui).
        return self._model_path.is_dir() and any(self._model_path.iterdir())

    @property
    def is_complete(self) -> bool:
        """Checagem mais rigorosa que `is_installed`, usada pelo setup para
        decidir se precisa baixar (v1.2).

        `is_installed` responde "existe alguma coisa aí?", o que é o certo
        para o HUD decidir entre SETUP_REQUIRED e READY. Mas uma extração
        interrompida deixa a pasta existindo com uma fração dos arquivos —
        e aí o setup não pode considerar o modelo pronto.

        Critério deliberadamente frouxo (pasta `conf/` + tamanho mínimo) em
        vez de uma lista exata de arquivos: uma checagem rígida demais
        rejeitaria um layout de modelo ligeiramente diferente e mandaria
        baixar de novo para sempre. `conf/` existe em todo modelo Vosk, e o
        piso de tamanho descarta pasta só com README ou extração parcial."""
        if not self._model_path.is_dir():
            return False
        if not (self._model_path / "conf").is_dir():
            return False
        return self.installed_size_bytes() >= _MINIMUM_VALID_MODEL_BYTES

    def installed_size_bytes(self) -> int:
        """Soma dos arquivos do modelo instalado (0 se não existir)."""
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

    @staticmethod
    def info() -> ModelInfo:
        return ModelInfo(
            name=MODEL_NAME,
            url=MODEL_URL,
            language=MODEL_LANGUAGE,
            approximate_size_bytes=MODEL_APPROXIMATE_SIZE_BYTES,
            license=MODEL_LICENSE,
            source=MODEL_SOURCE,
        )

    def cancel_download(self) -> None:
        self._cancel_requested = True

    def download_and_install(self, *, on_progress: Callable[[int, int], None] | None = None) -> None:
        """Baixa e instala o modelo. Só deve ser chamado em resposta a uma
        ação explícita do usuário — nunca automaticamente. `on_progress`
        recebe `(bytes_baixados, bytes_totais)`; `bytes_totais` é `0` se o
        servidor não informar `Content-Length`."""
        if not MODEL_URL.startswith("https://"):
            raise ModelDownloadError("URL do modelo não é HTTPS — download recusado por segurança.")

        self._cancel_requested = False
        self._models_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(dir=self._models_dir) as tmp_dir:
            zip_path = Path(tmp_dir) / "model.zip"
            try:
                self._download(MODEL_URL, zip_path, on_progress)
                self._extract_safely(zip_path, self._models_dir)
            except ModelDownloadCancelled:
                logger.info("Download do modelo de voz cancelado pelo usuário.")
                raise
            except ModelDownloadError:
                raise
            except Exception as exc:
                logger.exception("Falha ao baixar/instalar o modelo de voz.")
                raise ModelDownloadError(
                    "Não foi possível baixar o modelo de voz. Verifique sua conexão e tente novamente."
                ) from exc
            # `tmp_dir` (e o .zip temporário dentro dele) é sempre limpo aqui,
            # sucesso ou falha — `TemporaryDirectory` cuida disso no `with`.

    def _download(self, url: str, destination: Path, on_progress: Callable[[int, int], None] | None) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": "JARVIS-voice-setup/1.0"})
        try:
            response = urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT_S)
        except urllib.error.URLError as exc:
            raise ModelDownloadError(f"Não foi possível conectar ao servidor do modelo: {exc.reason}") from exc

        with response:
            total = int(response.headers.get("Content-Length", 0) or 0)
            downloaded = 0
            with open(destination, "wb") as handle:
                while True:
                    if self._cancel_requested:
                        raise ModelDownloadCancelled()
                    chunk = response.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if on_progress is not None:
                        on_progress(downloaded, total)

    def _extract_safely(self, zip_path: Path, destination_root: Path) -> None:
        """Protege contra Zip Slip (path traversal via `../` ou caminho
        absoluto dentro do .zip): cada entrada só é extraída se o caminho
        resolvido continuar dentro de `destination_root`."""
        destination_root = destination_root.resolve()
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                member_path = (destination_root / member.filename).resolve()
                if not member_path.is_relative_to(destination_root):
                    raise ModelDownloadError(f"Arquivo do modelo contém um caminho inválido: {member.filename}")
            archive.extractall(destination_root)

        extracted_dir = destination_root / MODEL_NAME
        if extracted_dir.is_dir() and extracted_dir != self._model_path:
            if self._model_path.exists():
                shutil.rmtree(self._model_path)
            extracted_dir.rename(self._model_path)
