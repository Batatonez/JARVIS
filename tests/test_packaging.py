"""Distribuição Windows — separação de caminhos, sanitização de log e
integridade da configuração de build.

Nenhum teste aqui roda o PyInstaller nem o Inno Setup: gerar um artefato de
683 MB dentro da suíte a tornaria inutilizável. O que é verificado é o que
pode dar errado em silêncio — a lógica de caminhos, o que a configuração de
build promete incluir, e o que ela promete nunca incluir. O build de verdade
é validado por `python scripts/build_windows.py`, que audita o artefato
final.
"""

import importlib
import os
import re
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from config import paths

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGING_DIR = PROJECT_ROOT / "packaging" / "windows"


class PathSeparationTests(unittest.TestCase):
    """A regra que sustenta o app instalado: recurso e dado do usuário são
    coisas diferentes e moram em lugares diferentes."""

    def test_development_keeps_both_roots_at_the_repository(self) -> None:
        """Nada muda para quem roda do código-fonte — é isso que dispensa
        migração de banco existente."""
        self.assertFalse(paths.is_frozen())
        self.assertEqual(paths.resource_root(), PROJECT_ROOT)
        self.assertEqual(paths.user_data_root(), PROJECT_ROOT)

    def test_frozen_sends_user_data_to_localappdata(self) -> None:
        with unittest.mock.patch.object(paths, "is_frozen", return_value=True), \
             unittest.mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\Test\AppData\Local"}, clear=False):
            os.environ.pop("JARVIS_USER_DATA", None)
            root = paths.user_data_root()
        self.assertEqual(root, Path(r"C:\Users\Test\AppData\Local") / "JARVIS")

    def test_frozen_user_data_is_never_inside_the_install_directory(self) -> None:
        """O defeito que este teste existe para impedir: gravar o banco na
        pasta de instalação — somente leitura para usuário padrão, e apagada
        a cada atualização."""
        install_dir = Path(r"C:\Users\Test\AppData\Local\Programs\JARVIS")
        with unittest.mock.patch.object(paths, "is_frozen", return_value=True), \
             unittest.mock.patch.object(paths, "resource_root", return_value=install_dir), \
             unittest.mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\Test\AppData\Local"}, clear=False):
            os.environ.pop("JARVIS_USER_DATA", None)
            data_root = paths.user_data_root()
        self.assertNotEqual(data_root, install_dir)
        self.assertNotIn(install_dir, data_root.parents)

    def test_explicit_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.dict(os.environ, {"JARVIS_USER_DATA": tmp}, clear=False):
                self.assertEqual(paths.user_data_root(), Path(tmp).resolve())

    def test_override_handles_paths_with_spaces_and_unicode(self) -> None:
        """Diretório de usuário do Windows tem espaço com frequência e pode
        ter acento — presumir ASCII sem espaço quebraria na máquina real."""
        with tempfile.TemporaryDirectory() as tmp:
            awkward = Path(tmp) / "Test User" / "Ação Ünicode"
            awkward.mkdir(parents=True)
            with unittest.mock.patch.dict(os.environ, {"JARVIS_USER_DATA": str(awkward)}, clear=False):
                self.assertEqual(paths.user_data_root(), awkward.resolve())

    def test_frozen_without_localappdata_never_falls_back_to_the_install_dir(self) -> None:
        install_dir = Path(r"C:\Program Files\JARVIS")
        with unittest.mock.patch.object(paths, "is_frozen", return_value=True), \
             unittest.mock.patch.object(paths, "resource_root", return_value=install_dir), \
             unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JARVIS_USER_DATA", None)
            os.environ.pop("LOCALAPPDATA", None)
            root = paths.user_data_root()
        self.assertNotEqual(root, install_dir)

    def test_settings_derive_mutable_paths_from_user_data_root(self) -> None:
        from config import settings as settings_module

        source = Path(settings_module.__file__).read_text(encoding="utf-8")
        for mutable in ("db_path", "log_path", "users_dir", "session_token_path", "whisper_models_dir"):
            match = re.search(rf"^\s*{mutable}: Path = (\w+)", source, re.MULTILINE)
            self.assertIsNotNone(match, mutable)
            self.assertEqual(match.group(1), "USER_DATA_ROOT", mutable)

    def test_ensure_user_data_dirs_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.dict(os.environ, {"JARVIS_USER_DATA": tmp}, clear=False):
                first = paths.ensure_user_data_dirs()
                second = paths.ensure_user_data_dirs()
        self.assertEqual(first, second)


class QmlResolutionTests(unittest.TestCase):
    def test_launcher_resolves_qml_relative_to_resource_root_when_frozen(self) -> None:
        """Empacotado, `__file__` aponta para dentro do bundle e não é um
        diretório — resolver o QML por ele abriria uma janela vazia."""
        from frontend import launcher

        fake_bundle = Path(r"C:\Bundle")
        with unittest.mock.patch("config.paths.is_frozen", return_value=True), \
             unittest.mock.patch("config.paths.resource_root", return_value=fake_bundle):
            self.assertEqual(launcher._qml_dir(), fake_bundle / "frontend" / "qml")

    def test_launcher_uses_the_source_tree_in_development(self) -> None:
        from frontend import launcher

        self.assertEqual(launcher._qml_dir(), PROJECT_ROOT / "frontend" / "qml")

    def test_files_the_build_promises_to_ship_actually_exist(self) -> None:
        """`scripts/build_windows.py` verifica estes caminhos no artefato. Se
        um deles for renomeado no código-fonte, o build só falharia ao rodar
        — este teste falha antes."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_build_windows", PROJECT_ROOT / "scripts" / "build_windows.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for required in module.REQUIRED_ARTIFACTS:
            if required.endswith(".exe"):
                continue  # produzido pelo build, não existe no código-fonte
            source_path = PROJECT_ROOT / required.replace("_internal/", "", 1)
            self.assertTrue(source_path.exists(), f"{source_path} não existe no código-fonte")


class BuildConfigurationTests(unittest.TestCase):
    def test_packaging_sources_are_versioned(self) -> None:
        for name in ("jarvis.spec", "JARVIS.iss"):
            self.assertTrue((PACKAGING_DIR / name).is_file(), name)
        self.assertTrue((PROJECT_ROOT / "scripts" / "build_windows.py").is_file())
        self.assertTrue((PROJECT_ROOT / "requirements-build.txt").is_file())

    def test_spec_bundles_the_qml_tree(self) -> None:
        spec = (PACKAGING_DIR / "jarvis.spec").read_text(encoding="utf-8")
        self.assertIn("frontend/qml", spec)

    def test_spec_excludes_tests_and_avoids_upx(self) -> None:
        spec = (PACKAGING_DIR / "jarvis.spec").read_text(encoding="utf-8")
        self.assertIn('"tests"', spec)
        self.assertIn("upx=False", spec)

    def test_release_build_hides_the_console(self) -> None:
        spec = (PACKAGING_DIR / "jarvis.spec").read_text(encoding="utf-8")
        self.assertIn("console=not IS_RELEASE", spec)

    def test_installer_is_per_user_and_needs_no_admin(self) -> None:
        iss = (PACKAGING_DIR / "JARVIS.iss").read_text(encoding="utf-8")
        self.assertIn("PrivilegesRequired=lowest", iss)
        self.assertIn(r"{localappdata}\Programs", iss)

    def test_installer_does_not_delete_user_data_by_default(self) -> None:
        """Desinstalar o programa e perder o histórico são coisas diferentes.
        A pergunta existe, e a resposta padrão é não (`MB_DEFBUTTON2`)."""
        iss = (PACKAGING_DIR / "JARVIS.iss").read_text(encoding="utf-8")
        self.assertIn("MB_DEFBUTTON2", iss)
        # A remoção só pode acontecer dentro do bloco condicional da pergunta.
        self.assertIn("DelTree", iss)
        self.assertLess(iss.index("MsgBox"), iss.index("DelTree"))

    def test_startup_and_desktop_shortcuts_are_opt_in(self) -> None:
        iss = (PACKAGING_DIR / "JARVIS.iss").read_text(encoding="utf-8")
        for task in re.findall(r"^Name: \"(desktopicon|startupicon)\".*$", iss, re.MULTILINE):
            pass
        self.assertEqual(iss.count("Flags: unchecked"), 2)

    def test_installer_version_comes_from_the_build_script(self) -> None:
        """Sem `JarvisVersion` injetada, a compilação falha — é o que impede
        uma segunda fonte de versão escrita à mão."""
        iss = (PACKAGING_DIR / "JARVIS.iss").read_text(encoding="utf-8")
        self.assertIn("#ifndef JarvisVersion", iss)
        self.assertIn("#error", iss)

    def test_version_is_consistent_across_official_sources(self) -> None:
        from config.settings import Settings

        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        declared = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
        self.assertIsNotNone(declared)
        self.assertEqual(declared.group(1), Settings.core_version)

    def test_build_script_refuses_to_clean_outside_the_project(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_build_windows", PROJECT_ROOT / "scripts" / "build_windows.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "build"
            outside.mkdir()
            with self.assertRaises(module.BuildError):
                module._safe_rmtree(outside)

    def test_build_script_refuses_to_clean_a_non_build_directory(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_build_windows", PROJECT_ROOT / "scripts" / "build_windows.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with self.assertRaises(module.BuildError):
            module._safe_rmtree(PROJECT_ROOT / "services")

    def test_generated_version_metadata_is_not_committed(self) -> None:
        """Ele é derivado da versão oficial a cada build; versioná-lo criaria
        a segunda fonte de versão que o build existe para evitar."""
        gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("packaging/windows/version_info.txt", gitignore)

    def test_build_artifacts_are_not_committed(self) -> None:
        gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("dist/", gitignore)
        self.assertIn("build/", gitignore)


class LogSanitizationTests(unittest.TestCase):
    """Segunda camada: se uma credencial escapar para uma mensagem de log,
    ela é mascarada antes de tocar o disco."""

    def _redact(self, message: str, *args) -> str:
        import logging

        from config.logging_config import SecretRedactingFilter

        record = logging.LogRecord("t", logging.INFO, __file__, 1, message, args, None)
        SecretRedactingFilter().filter(record)
        return record.getMessage()

    def test_known_provider_key_shapes_are_redacted(self) -> None:
        for secret in (
            "sk-abcdefghijklmnopqrstuvwxyz123456",
            "nvapi-abcdefghijklmnopqrstuvwxyz1234",
            "gsk_abcdefghijklmnopqrstuvwxyz1234",
            "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456",
        ):
            self.assertNotIn(secret, self._redact(f"chamando com {secret}"), secret)

    def test_authorization_header_is_redacted(self) -> None:
        self.assertNotIn("abcdefghijklmnop123", self._redact("Authorization: Bearer abcdefghijklmnop123"))

    def test_secret_passed_as_argument_is_redacted(self) -> None:
        """`logger.info("key=%s", chave)` só é pego olhando a mensagem já
        interpolada — checar apenas o template deixaria passar."""
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456",
                         self._redact("usando %s", "sk-abcdefghijklmnopqrstuvwxyz123456"))

    def test_ordinary_messages_are_untouched(self) -> None:
        """Um filtro amplo demais tornaria o log inútil justamente quando ele
        é necessário."""
        for ordinary in (
            "Migrando banco local: versão 4 -> 5.",
            "Conta apagada a pedido do usuário.",
            "openrouter/free respondeu sem conteúdo visível.",
        ):
            self.assertEqual(self._redact(ordinary), ordinary)

    def test_log_file_rotates(self) -> None:
        import inspect

        from config import logging_config

        source = inspect.getsource(logging_config)
        self.assertIn("RotatingFileHandler", source)
        self.assertIn("maxBytes", source)
        self.assertIn("backupCount", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
