"""Testes de FREE/PRO entitlements (sem billing) e do Voice Model Manager —
downloader do modelo Vosk. Nada aqui toca a rede, um microfone real ou
hardware: `urllib.request.urlopen` é sempre substituído por um fake local
(ver `_FakeHTTPResponse`), e o Zip Slip é testado com um .zip malicioso
criado localmente, nunca baixado.
"""

import io
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path

from app.entitlements import entitlements_for
from app.models import Plan
from services import vosk_model_manager
from services.ai_service import UnavailableAIService
from services.vosk_model_manager import ModelDownloadCancelled, ModelDownloadError, VoiceModelManager
from tests.helpers import build_isolated_account_manager, build_isolated_voice_service


class EntitlementsTests(unittest.TestCase):
    def test_free_plan_entitlements(self) -> None:
        entitlements = entitlements_for(Plan.FREE)
        self.assertEqual(entitlements.plan, Plan.FREE)
        self.assertEqual(entitlements.max_conversation_messages, 200)
        self.assertFalse(entitlements.advanced_tools)
        self.assertFalse(entitlements.multi_agent)

    def test_pro_plan_has_higher_message_limit_but_no_real_extra_capability_yet(self) -> None:
        entitlements = entitlements_for(Plan.PRO)
        self.assertEqual(entitlements.plan, Plan.PRO)
        self.assertGreater(entitlements.max_conversation_messages, entitlements_for(Plan.FREE).max_conversation_messages)
        # v0.9: sem tools avançadas/multi-agent reais para NENHUM plano ainda
        # (ver docstring de app/entitlements.py) — nada de billing implica isso.
        self.assertFalse(entitlements.advanced_tools)
        self.assertFalse(entitlements.multi_agent)

    def test_entitlements_are_a_pure_function_of_plan(self) -> None:
        self.assertIs(entitlements_for(Plan.FREE), entitlements_for(Plan.FREE))
        self.assertIsNot(entitlements_for(Plan.FREE), entitlements_for(Plan.PRO))


class AccountManagerEntitlementsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    async def test_new_account_defaults_to_free_plan_entitlements(self) -> None:
        account = build_isolated_account_manager(
            self.tmp_path, ai_service_factory=UnavailableAIService, voice_service_factory=build_isolated_voice_service
        )
        try:
            await account.register(username="alice", display_name="Alice", password="senha-forte-123")

            entitlements = account.current_entitlements()

            self.assertIsNotNone(entitlements)
            self.assertEqual(entitlements.plan, Plan.FREE)
        finally:
            await account.shutdown()

    async def test_entitlements_is_none_when_not_authenticated(self) -> None:
        account = build_isolated_account_manager(
            self.tmp_path, ai_service_factory=UnavailableAIService, voice_service_factory=build_isolated_voice_service
        )
        self.assertIsNone(account.current_entitlements())


def _make_model_zip(model_name: str) -> bytes:
    """Constrói, em memória, um .zip benigno equivalente ao que o servidor
    real serviria: uma pasta `<model_name>/` com um arquivo dentro."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{model_name}/README", "modelo de teste, não é um modelo Vosk de verdade")
        archive.writestr(f"{model_name}/am/final.mdl", "conteudo fake")
    return buffer.getvalue()


def _make_malicious_zip() -> bytes:
    """.zip com uma entrada de path traversal (`../../evil.txt`) — usado só
    para provar que `_extract_safely` bloqueia Zip Slip, nunca baixado de
    verdade."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../../evil.txt", "eu não deveria existir fora da pasta do modelo")
    return buffer.getvalue()


class _FakeHTTPResponse:
    """Substitui o objeto devolvido por `urllib.request.urlopen` — só o
    suficiente para `VoiceModelManager._download` funcionar: `headers`,
    `read(n)` em pedaços, e uso como context manager."""

    def __init__(self, payload: bytes, *, chunk_size: int = 8192) -> None:
        self._payload = payload
        self._offset = 0
        self._chunk_size = chunk_size
        self.headers = {"Content-Length": str(len(payload))}

    def read(self, size: int) -> bytes:
        size = size or self._chunk_size
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *exc_info) -> None:
        return None


class VoiceModelManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.models_dir = Path(self._tmp.name) / "models" / "vosk"
        self.manager = VoiceModelManager(models_dir=self.models_dir)

    def test_is_installed_false_when_directory_missing(self) -> None:
        self.assertFalse(self.manager.is_installed)

    def test_is_installed_false_when_directory_empty(self) -> None:
        self.manager.model_path.mkdir(parents=True)
        self.assertFalse(self.manager.is_installed)

    def test_is_installed_true_when_directory_has_files(self) -> None:
        self.manager.model_path.mkdir(parents=True)
        (self.manager.model_path / "am").mkdir()
        (self.manager.model_path / "am" / "final.mdl").write_text("x")
        self.assertTrue(self.manager.is_installed)

    def test_info_returns_expected_metadata(self) -> None:
        info = VoiceModelManager.info()
        self.assertTrue(info.url.startswith("https://"))
        self.assertEqual(info.language, "Português (Brasil)")
        self.assertGreater(info.approximate_size_bytes, 0)

    def test_download_and_install_rejects_non_https_url(self) -> None:
        with unittest.mock.patch.object(vosk_model_manager, "MODEL_URL", "http://example.com/model.zip"):
            with self.assertRaises(ModelDownloadError):
                self.manager.download_and_install()
        self.assertFalse(self.manager.is_installed)

    def test_download_and_install_success_extracts_and_renames_model_dir(self) -> None:
        payload = _make_model_zip(vosk_model_manager.MODEL_NAME)
        progress_calls: list[tuple[int, int]] = []

        with unittest.mock.patch.object(
            vosk_model_manager.urllib.request, "urlopen", return_value=_FakeHTTPResponse(payload)
        ):
            self.manager.download_and_install(on_progress=lambda done, total: progress_calls.append((done, total)))

        self.assertTrue(self.manager.is_installed)
        self.assertTrue((self.manager.model_path / "am" / "final.mdl").is_file())
        self.assertTrue(progress_calls)
        self.assertEqual(progress_calls[-1][0], len(payload))  # baixou tudo
        self.assertEqual(progress_calls[-1][1], len(payload))  # Content-Length batido

    def test_download_cancelled_mid_transfer_raises_and_installs_nothing(self) -> None:
        payload = _make_model_zip(vosk_model_manager.MODEL_NAME) * 50  # payload maior, várias iterações de leitura

        def _cancel_after_first_chunk(done: int, total: int) -> None:
            self.manager.cancel_download()

        with unittest.mock.patch.object(
            vosk_model_manager.urllib.request, "urlopen", return_value=_FakeHTTPResponse(payload, chunk_size=1024)
        ):
            with self.assertRaises(ModelDownloadCancelled):
                self.manager.download_and_install(on_progress=_cancel_after_first_chunk)

        self.assertFalse(self.manager.is_installed)

    def test_download_network_error_raises_model_download_error(self) -> None:
        import urllib.error

        with unittest.mock.patch.object(
            vosk_model_manager.urllib.request, "urlopen", side_effect=urllib.error.URLError("sem conexão")
        ):
            with self.assertRaises(ModelDownloadError):
                self.manager.download_and_install()

        self.assertFalse(self.manager.is_installed)

    def test_extract_safely_blocks_path_traversal(self) -> None:
        malicious_zip = Path(self._tmp.name) / "malicious.zip"
        malicious_zip.write_bytes(_make_malicious_zip())
        self.models_dir.mkdir(parents=True)

        with self.assertRaises(ModelDownloadError):
            self.manager._extract_safely(malicious_zip, self.models_dir)

        escaped_file = self.models_dir.parent.parent / "evil.txt"
        self.assertFalse(escaped_file.exists())
        self.assertFalse(self.manager.is_installed)


if __name__ == "__main__":
    unittest.main()
