"""Carregamento automático do `.env` (v1.1).

Todos os testes usam um `.env` em diretório temporário e restauram
`os.environ` no teardown — **nunca** leem o `.env` real do projeto nem as
chaves de verdade do usuário.
"""

import os
import tempfile
import unittest
from pathlib import Path

from config.env_loader import _load_minimal, load_project_env


class EnvLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._original_environ = dict(os.environ)
        self.addCleanup(self._restore_environ)
        # Sob teste, `load_project_env` se recusa a ler `.env` (e limpa as
        # variáveis sensíveis) — é o que impede a suíte de tocar credencial
        # real. Estes testes SÃO do loader e usam um `.env` temporário, então
        # passam `force=True` para exercitar o caminho de verdade.

    def _restore_environ(self) -> None:
        os.environ.clear()
        os.environ.update(self._original_environ)

    def _write_env(self, content: str) -> None:
        (self.root / ".env").write_text(content, encoding="utf-8")

    # 1. .env é carregado automaticamente --------------------------------
    def test_env_file_is_loaded(self) -> None:
        os.environ.pop("JARVIS_TEST_LOADED", None)
        self._write_env("JARVIS_TEST_LOADED=valor-do-env\n")

        self.assertTrue(load_project_env(self.root, force=True))
        self.assertEqual(os.environ["JARVIS_TEST_LOADED"], "valor-do-env")

    # 2. ambiente existente vence o .env ---------------------------------
    def test_existing_environment_variable_wins_over_env_file(self) -> None:
        os.environ["JARVIS_TEST_PRECEDENCE"] = "valor-do-ambiente"
        self._write_env("JARVIS_TEST_PRECEDENCE=valor-do-env\n")

        load_project_env(self.root, force=True)

        self.assertEqual(os.environ["JARVIS_TEST_PRECEDENCE"], "valor-do-ambiente")

    # 3. .env ausente não quebra -----------------------------------------
    def test_missing_env_file_does_not_crash(self) -> None:
        self.assertFalse(load_project_env(self.root, force=True))

    def test_unreadable_env_file_does_not_crash(self) -> None:
        # Um diretório no lugar do arquivo: `is_file()` é falso, sem exceção.
        (self.root / ".env").mkdir()
        self.assertFalse(load_project_env(self.root, force=True))

    # 4. valores em branco tratados --------------------------------------
    def test_blank_values_are_handled(self) -> None:
        os.environ.pop("JARVIS_TEST_BLANK", None)
        self._write_env("JARVIS_TEST_BLANK=\n")

        load_project_env(self.root, force=True)

        # A variável existe e é vazia — `Settings` converte "" em None via
        # `or None`, que é o comportamento esperado para "não configurado".
        self.assertEqual(os.environ.get("JARVIS_TEST_BLANK"), "")

    def test_comments_and_empty_lines_are_ignored(self) -> None:
        os.environ.pop("JARVIS_TEST_REAL", None)
        self._write_env(
            "# um comentário\n"
            "\n"
            "   \n"
            "JARVIS_TEST_REAL=ok\n"
            "# JARVIS_TEST_COMENTADA=nao-deve-existir\n"
        )

        load_project_env(self.root, force=True)

        self.assertEqual(os.environ.get("JARVIS_TEST_REAL"), "ok")
        self.assertIsNone(os.environ.get("JARVIS_TEST_COMENTADA"))

    # 5. segredo nunca é impresso/logado ---------------------------------
    def test_secret_is_never_printed_or_logged(self) -> None:
        import io
        import logging
        import contextlib

        secret = "sk-or-v1-valor-super-secreto-do-teste"
        os.environ.pop("JARVIS_TEST_SECRET", None)
        self._write_env(f"JARVIS_TEST_SECRET={secret}\n")

        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        logger = logging.getLogger("config.env_loader")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                load_project_env(self.root, force=True)
        finally:
            logger.removeHandler(handler)

        self.assertEqual(os.environ["JARVIS_TEST_SECRET"], secret)  # carregou
        self.assertNotIn(secret, log_stream.getvalue())  # mas não vazou
        self.assertNotIn(secret, stdout.getvalue())
        self.assertNotIn("JARVIS_TEST_SECRET", log_stream.getvalue())


class MinimalParserTests(unittest.TestCase):
    """O fallback usado quando `python-dotenv` não está instalado."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / ".env"
        self._original_environ = dict(os.environ)
        self.addCleanup(self._restore_environ)

    def _restore_environ(self) -> None:
        os.environ.clear()
        os.environ.update(self._original_environ)

    def test_parses_quotes_export_and_comments(self) -> None:
        for name in ("JARVIS_T_A", "JARVIS_T_B", "JARVIS_T_C", "JARVIS_T_D"):
            os.environ.pop(name, None)
        self.path.write_text(
            "# comentário\n"
            "JARVIS_T_A=simples\n"
            'JARVIS_T_B="entre aspas duplas"\n'
            "JARVIS_T_C='entre aspas simples'\n"
            "export JARVIS_T_D=com-export\n",
            encoding="utf-8",
        )

        _load_minimal(self.path)

        self.assertEqual(os.environ["JARVIS_T_A"], "simples")
        self.assertEqual(os.environ["JARVIS_T_B"], "entre aspas duplas")
        self.assertEqual(os.environ["JARVIS_T_C"], "entre aspas simples")
        self.assertEqual(os.environ["JARVIS_T_D"], "com-export")

    def test_does_not_override_existing_environment(self) -> None:
        os.environ["JARVIS_T_KEEP"] = "do-ambiente"
        self.path.write_text("JARVIS_T_KEEP=do-arquivo\n", encoding="utf-8")

        _load_minimal(self.path)

        self.assertEqual(os.environ["JARVIS_T_KEEP"], "do-ambiente")

    def test_malformed_lines_are_skipped(self) -> None:
        os.environ.pop("JARVIS_T_OK", None)
        self.path.write_text("linha sem igual\n=sem_chave\nJARVIS_T_OK=ok\n", encoding="utf-8")

        _load_minimal(self.path)

        self.assertEqual(os.environ["JARVIS_T_OK"], "ok")


class EmptyEnvValueTests(unittest.TestCase):
    """Regressão da v1.1: com o `.env` carregado automaticamente, copiar o
    `.env.example` (cheio de `CHAVE=` em branco, de propósito) colocava `""`
    no ambiente. `int("")` derrubava o JARVIS já no import de
    `config.settings` — seguir a própria documentação quebrava o app."""

    def setUp(self) -> None:
        self._original_environ = dict(os.environ)
        self.addCleanup(self._restore_environ)

    def _restore_environ(self) -> None:
        os.environ.clear()
        os.environ.update(self._original_environ)

    # Os defaults de `Settings` são congelados na criação do dataclass (no
    # import), então setar variável e instanciar `Settings()` aqui não
    # provaria nada. Testamos os helpers diretamente — e o cenário completo
    # via subprocesso, que é o único jeito honesto de exercitar import-time.

    def test_blank_numeric_values_fall_back_to_defaults(self) -> None:
        from config.settings import _env_float, _env_int

        os.environ["JARVIS_T_NUM"] = ""
        self.assertEqual(_env_int("JARVIS_T_NUM", 587), 587)
        self.assertEqual(_env_float("JARVIS_T_NUM", 60.0), 60.0)

    def test_missing_numeric_value_falls_back_to_default(self) -> None:
        from config.settings import _env_int

        os.environ.pop("JARVIS_T_AUSENTE", None)
        self.assertEqual(_env_int("JARVIS_T_AUSENTE", 1024), 1024)

    def test_garbage_numeric_value_falls_back_instead_of_crashing(self) -> None:
        from config.settings import _env_int

        os.environ["JARVIS_T_LIXO"] = "nao-e-numero"
        self.assertEqual(_env_int("JARVIS_T_LIXO", 1024), 1024)

    def test_valid_numeric_value_is_respected(self) -> None:
        from config.settings import _env_int

        os.environ["JARVIS_T_OK"] = "42"
        self.assertEqual(_env_int("JARVIS_T_OK", 1024), 42)

    def test_boolean_flags_accept_common_spellings(self) -> None:
        from config.settings import _env_flag

        for value in ("0", "false", "no", "off", "FALSE"):
            os.environ["JARVIS_T_FLAG"] = value
            self.assertFalse(_env_flag("JARVIS_T_FLAG", True), f"valor {value!r} deveria desligar")
        for value in ("1", "true", "yes", "on", "TRUE"):
            os.environ["JARVIS_T_FLAG"] = value
            self.assertTrue(_env_flag("JARVIS_T_FLAG", False), f"valor {value!r} deveria ligar")

    def test_blank_boolean_falls_back_to_default(self) -> None:
        from config.settings import _env_flag

        os.environ["JARVIS_T_FLAG"] = ""
        self.assertTrue(_env_flag("JARVIS_T_FLAG", True))
        self.assertFalse(_env_flag("JARVIS_T_FLAG", False))

    def test_env_example_can_be_copied_verbatim_without_breaking(self) -> None:
        """O cenário exato do bug, de ponta a ponta: copiar `.env.example`
        como `.env` e iniciar o JARVIS.

        Roda num subprocesso com `cwd` num diretório temporário (que contém
        o `.env` copiado) — assim o import de `config.settings` acontece de
        verdade com aquelas variáveis, sem tocar o `.env` real do projeto."""
        import subprocess
        import sys

        from config.settings import PROJECT_ROOT

        example = PROJECT_ROOT / ".env.example"
        if not example.is_file():
            self.skipTest(".env.example não encontrado")

        with tempfile.TemporaryDirectory() as tmp:
            # Reproduz `cp .env.example .env` — mas no diretório temporário.
            env_values = {}
            for raw_line in example.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                key, separator, value = line.partition("=")
                if separator:
                    env_values[key.strip()] = value.strip()

            child_env = dict(os.environ)
            child_env.update(env_values)
            child_env["PYTHONPATH"] = str(PROJECT_ROOT)

            result = subprocess.run(
                [sys.executable, "-c", "from config.settings import Settings; Settings(); print('OK')"],
                capture_output=True,
                text=True,
                env=child_env,
                cwd=tmp,
                timeout=60,
            )

        self.assertEqual(
            result.returncode, 0, f"import de Settings falhou com o .env.example:\n{result.stderr}"
        )
        self.assertIn("OK", result.stdout)


class SettingsIntegrationTests(unittest.TestCase):
    """`config/settings.py` chama o loader no import — este teste confirma
    que a fiação existe, sem depender do `.env` real do usuário."""

    def test_settings_module_loads_env_before_defaults(self) -> None:
        import inspect

        import config.settings as settings_module

        source = inspect.getsource(settings_module)
        # O argumento passou a ser `USER_DATA_ROOT` (packaging: num app
        # instalado o `.env` mora no diretório de dados do usuário, o único
        # dos dois onde se pode escrever). A PROPRIEDADE medida aqui é a
        # mesma de sempre — a ordem da chamada.
        loader_line = source.index("load_project_env(USER_DATA_ROOT)")
        dataclass_line = source.index("class Settings")
        # A chamada precisa vir ANTES da definição da classe: os defaults são
        # avaliados na criação do dataclass.
        self.assertLess(loader_line, dataclass_line)


if __name__ == "__main__":
    unittest.main()
