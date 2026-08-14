"""Preparação do STT no `python setup.py` (v1.2).

**Nenhum download real acontece aqui**: o transporte HTTP do
`VoiceModelManager` é substituído por um fake (o mesmo padrão de
`tests/test_entitlements_and_voice_model_manager.py`), e todo caminho de
arquivo aponta para diretório temporário. Nenhum teste toca
`data/models/vosk` real.
"""

import io
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path

from services import vosk_model_manager
from services.first_run_setup import (
    SetupReport,
    StepResult,
    StepStatus,
    check_voice_dependencies,
    detect_microphone,
    ensure_stt_model,
)
from services.vosk_model_manager import ModelDownloadError, VoiceModelManager
from tests.test_entitlements_and_voice_model_manager import _FakeHTTPResponse, _make_malicious_zip

# Acima do piso de `_MINIMUM_VALID_MODEL_BYTES` (5 MB) — zeros comprimem
# quase a nada, então o .zip de teste continua pequeno em memória.
_PAYLOAD_BYTES = 6 * 1024 * 1024


def _make_complete_model_zip(model_name: str) -> bytes:
    """Zip parecido com o real: pasta do modelo com `conf/` e volume."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{model_name}/README", "modelo de teste")
        archive.writestr(f"{model_name}/conf/model.conf", "--min-active=200")
        archive.writestr(f"{model_name}/conf/mfcc.conf", "--sample-frequency=16000")
        archive.writestr(f"{model_name}/am/final.mdl", b"\0" * _PAYLOAD_BYTES)
    return buffer.getvalue()


def _make_partial_model_zip(model_name: str) -> bytes:
    """Só o README — o que sobra de uma extração interrompida."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{model_name}/README", "extração incompleta")
    return buffer.getvalue()


class ModelValidationTests(unittest.TestCase):
    """`is_complete` é o que o setup usa para decidir se precisa baixar."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.models_dir = Path(self._tmp.name) / "models"
        self.manager = VoiceModelManager(models_dir=self.models_dir)

    def _write_model(self, *, with_conf: bool, size: int) -> None:
        path = self.manager.model_path
        (path / "am").mkdir(parents=True, exist_ok=True)
        if with_conf:
            (path / "conf").mkdir(parents=True, exist_ok=True)
            (path / "conf" / "model.conf").write_text("x", encoding="utf-8")
        (path / "am" / "final.mdl").write_bytes(b"\0" * size)

    def test_absent_model_is_neither_installed_nor_complete(self) -> None:
        self.assertFalse(self.manager.is_installed)
        self.assertFalse(self.manager.is_complete)

    def test_complete_model_is_valid(self) -> None:
        self._write_model(with_conf=True, size=_PAYLOAD_BYTES)
        self.assertTrue(self.manager.is_installed)
        self.assertTrue(self.manager.is_complete)

    def test_partial_model_is_not_considered_valid(self) -> None:
        """Extração interrompida deixa a pasta existindo — `is_installed`
        diz sim, mas o setup não pode aceitar isso como pronto."""
        self.manager.model_path.mkdir(parents=True)
        (self.manager.model_path / "README").write_text("só isso", encoding="utf-8")

        self.assertTrue(self.manager.is_installed)
        self.assertFalse(self.manager.is_complete)

    def test_model_without_conf_directory_is_not_valid(self) -> None:
        self._write_model(with_conf=False, size=_PAYLOAD_BYTES)
        self.assertFalse(self.manager.is_complete)

    def test_model_below_size_floor_is_not_valid(self) -> None:
        self._write_model(with_conf=True, size=1024)
        self.assertFalse(self.manager.is_complete)

    def test_installed_size_is_zero_when_absent(self) -> None:
        self.assertEqual(self.manager.installed_size_bytes(), 0)


class EnsureModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.models_dir = Path(self._tmp.name) / "models"
        self.manager = VoiceModelManager(models_dir=self.models_dir)

    def _patch_download(self, payload: bytes):
        return unittest.mock.patch.object(
            vosk_model_manager.urllib.request, "urlopen", return_value=_FakeHTTPResponse(payload)
        )

    def test_already_installed_model_is_not_downloaded_again(self) -> None:
        """Idempotência: rodar o setup de novo não rebaixa nada."""
        path = self.manager.model_path
        (path / "conf").mkdir(parents=True)
        (path / "conf" / "model.conf").write_text("x", encoding="utf-8")
        (path / "final.mdl").write_bytes(b"\0" * _PAYLOAD_BYTES)

        with unittest.mock.patch.object(vosk_model_manager.urllib.request, "urlopen") as urlopen:
            result = ensure_stt_model(self.manager)

        urlopen.assert_not_called()
        self.assertEqual(result.status, StepStatus.ALREADY_PRESENT)
        self.assertTrue(result.ok)

    def test_missing_model_is_downloaded(self) -> None:
        payload = _make_complete_model_zip(vosk_model_manager.MODEL_NAME)

        with self._patch_download(payload):
            result = ensure_stt_model(self.manager)

        self.assertEqual(result.status, StepStatus.DOWNLOADED)
        self.assertTrue(self.manager.is_complete)

    def test_download_reports_progress(self) -> None:
        payload = _make_complete_model_zip(vosk_model_manager.MODEL_NAME)
        seen: list[tuple[int, int]] = []

        with self._patch_download(payload):
            ensure_stt_model(self.manager, on_progress=lambda done, total: seen.append((done, total)))

        self.assertTrue(seen)
        self.assertEqual(seen[-1][0], len(payload))  # chegou ao fim

    def test_missing_model_is_only_reported_when_download_is_not_allowed(self) -> None:
        with unittest.mock.patch.object(vosk_model_manager.urllib.request, "urlopen") as urlopen:
            result = ensure_stt_model(self.manager, allow_download=False)

        urlopen.assert_not_called()
        self.assertEqual(result.status, StepStatus.MISSING)

    def test_interrupted_download_fails_without_leaving_a_model(self) -> None:
        import urllib.error

        with unittest.mock.patch.object(
            vosk_model_manager.urllib.request, "urlopen", side_effect=urllib.error.URLError("conexão caiu")
        ):
            result = ensure_stt_model(self.manager)

        self.assertEqual(result.status, StepStatus.FAILED)
        self.assertFalse(self.manager.is_complete)
        self.assertFalse(self.manager.model_path.exists())

    def test_invalid_archive_fails_cleanly(self) -> None:
        with self._patch_download(b"isto nao e um zip"):
            result = ensure_stt_model(self.manager)

        self.assertEqual(result.status, StepStatus.FAILED)
        self.assertFalse(self.manager.is_complete)

    def test_partial_archive_is_rejected_after_download(self) -> None:
        """O .zip extrai, mas o resultado não passa na validação — melhor
        falhar claro do que declarar pronto algo que o Vosk não carrega."""
        payload = _make_partial_model_zip(vosk_model_manager.MODEL_NAME)

        with self._patch_download(payload):
            result = ensure_stt_model(self.manager)

        self.assertEqual(result.status, StepStatus.FAILED)
        self.assertIn("completo", result.detail)

    def test_path_traversal_in_archive_is_blocked(self) -> None:
        """Zip Slip: entrada `../../evil.txt` não pode escapar do diretório
        de modelos (proteção do VoiceModelManager, exercitada pelo setup)."""
        self.models_dir.mkdir(parents=True, exist_ok=True)

        with self._patch_download(_make_malicious_zip()):
            result = ensure_stt_model(self.manager)

        self.assertEqual(result.status, StepStatus.FAILED)
        escaped = self.models_dir.parent.parent / "evil.txt"
        self.assertFalse(escaped.exists())
        self.assertFalse(self.manager.is_complete)

    def test_download_uses_https_only(self) -> None:
        """Nenhuma proteção foi afrouxada para o setup funcionar."""
        self.assertTrue(vosk_model_manager.MODEL_URL.startswith("https://"))

        with unittest.mock.patch.object(vosk_model_manager, "MODEL_URL", "http://exemplo/modelo.zip"):
            with self.assertRaises(ModelDownloadError):
                self.manager.download_and_install()


class MicrophoneDetectionTests(unittest.TestCase):
    def test_missing_microphone_never_fails_setup(self) -> None:
        with unittest.mock.patch("sounddevice.query_devices", side_effect=RuntimeError("sem dispositivo")):
            result = detect_microphone()

        self.assertEqual(result.status, StepStatus.NOT_DETECTED)
        self.assertTrue(result.ok, "ausência de microfone não pode reprovar o setup")

    def test_detected_microphone_reports_name_and_rate(self) -> None:
        device = {"name": "Microfone (Teste)", "default_samplerate": 44100.0}
        with unittest.mock.patch("sounddevice.query_devices", return_value=device):
            result = detect_microphone()

        self.assertEqual(result.status, StepStatus.DETECTED)
        self.assertIn("Microfone (Teste)", result.detail)
        self.assertIn("44100", result.detail)

    def test_report_succeeds_even_without_microphone(self) -> None:
        report = SetupReport(
            launcher=StepResult(StepStatus.OK),
            dependencies=StepResult(StepStatus.OK),
            speech_to_text=StepResult(StepStatus.ALREADY_PRESENT),
            microphone=StepResult(StepStatus.NOT_DETECTED),
        )
        self.assertTrue(report.succeeded)

    def test_report_fails_when_a_required_step_fails(self) -> None:
        report = SetupReport(
            launcher=StepResult(StepStatus.OK),
            dependencies=StepResult(StepStatus.OK),
            speech_to_text=StepResult(StepStatus.FAILED, "erro"),
            microphone=StepResult(StepStatus.DETECTED),
        )
        self.assertFalse(report.succeeded)


class DependencyCheckTests(unittest.TestCase):
    def test_reports_ok_when_voice_dependencies_are_importable(self) -> None:
        result = check_voice_dependencies()
        self.assertEqual(result.status, StepStatus.OK)

    def test_reports_missing_dependency(self) -> None:
        import builtins

        real_import = builtins.__import__

        def _fail_vosk(name, *args, **kwargs):
            if name == "vosk":
                raise ImportError("simulado")
            return real_import(name, *args, **kwargs)

        with unittest.mock.patch.object(builtins, "__import__", side_effect=_fail_vosk):
            result = check_voice_dependencies()

        self.assertEqual(result.status, StepStatus.MISSING)
        self.assertIn("vosk", result.detail)


class SetupScriptContractTests(unittest.TestCase):
    """`setup.py` tem dois modos; quebrar isso quebra `pip install -e .`."""

    def test_setup_py_delegates_to_setuptools_when_given_a_command(self) -> None:
        source = (Path(__file__).resolve().parent.parent / "setup.py").read_text(encoding="utf-8")
        self.assertIn("len(sys.argv) > 1", source)
        self.assertIn("_delegate_to_setuptools", source)
        self.assertIn("from setuptools import setup", source)

    def test_setup_py_does_not_download_at_import_time(self) -> None:
        """Importar o módulo (o que o setuptools faz) não pode disparar
        pip nem download — só a execução direta faz isso."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "jarvis_setup_probe", Path(__file__).resolve().parent.parent / "setup.py"
        )
        module = importlib.util.module_from_spec(spec)
        with unittest.mock.patch("subprocess.run") as run:
            spec.loader.exec_module(module)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
