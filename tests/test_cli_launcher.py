"""Comando `jarvis` (v1.1) — parser e despacho.

**Nenhum teste aqui abre uma GUI**: `frontend.launcher.run` é substituído
por um dublê, então o caminho testado é "o CLI chama o entrypoint certo",
não "o Qt sobe uma janela".
"""

import io
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout

from frontend.cli import HELP_TEXT, Command, main, resolve_command


class ResolveCommandTests(unittest.TestCase):
    def test_no_arguments_starts_jarvis(self) -> None:
        self.assertIs(resolve_command([]), Command.START)

    def test_wake_starts_jarvis(self) -> None:
        self.assertIs(resolve_command(["wake"]), Command.START)

    def test_wake_up_starts_jarvis(self) -> None:
        self.assertIs(resolve_command(["wake", "up"]), Command.START)

    def test_start_starts_jarvis(self) -> None:
        self.assertIs(resolve_command(["start"]), Command.START)

    def test_commands_are_case_and_whitespace_insensitive(self) -> None:
        for args in (["WAKE"], ["Wake", "Up"], ["  wake  ", " up "], ["START"]):
            self.assertIs(resolve_command(args), Command.START, f"{args!r} deveria iniciar")

    def test_help_flags_are_recognized(self) -> None:
        for args in (["-h"], ["--help"], ["help"]):
            self.assertIs(resolve_command(args), Command.HELP, f"{args!r} deveria pedir ajuda")

    def test_unknown_argument_is_rejected(self) -> None:
        for args in (["sleep"], ["wake", "me", "up"], ["--version"], ["start", "now"]):
            self.assertIs(resolve_command(args), Command.UNKNOWN, f"{args!r} deveria ser desconhecido")


class MainDispatchTests(unittest.TestCase):
    """`main()` só pode tocar o launcher quando o comando é de início."""

    def test_start_calls_the_gui_entrypoint_once(self) -> None:
        with unittest.mock.patch("frontend.launcher.run", return_value=0) as run:
            exit_code = main([])

        run.assert_called_once_with()
        self.assertEqual(exit_code, 0)

    def test_every_start_alias_reaches_the_same_entrypoint(self) -> None:
        for args in ([], ["wake"], ["wake", "up"], ["start"]):
            with unittest.mock.patch("frontend.launcher.run", return_value=0) as run:
                main(args)
            run.assert_called_once_with()

    def test_launcher_exit_code_is_propagated(self) -> None:
        """Se o HUD falhar ao carregar, `run()` devolve 1 — o shell precisa
        enxergar isso."""
        with unittest.mock.patch("frontend.launcher.run", return_value=1):
            self.assertEqual(main([]), 1)

    def test_help_prints_usage_and_never_starts_the_gui(self) -> None:
        stdout = io.StringIO()
        with unittest.mock.patch("frontend.launcher.run") as run:
            with redirect_stdout(stdout):
                exit_code = main(["--help"])

        run.assert_not_called()
        self.assertEqual(exit_code, 0)
        self.assertIn("Usage:", stdout.getvalue())
        self.assertIn("jarvis wake up", stdout.getvalue())

    def test_unknown_argument_prints_usage_to_stderr_and_never_starts(self) -> None:
        stderr = io.StringIO()
        with unittest.mock.patch("frontend.launcher.run") as run:
            with redirect_stderr(stderr):
                exit_code = main(["voar"])

        run.assert_not_called()
        self.assertEqual(exit_code, 2)  # convenção de erro de uso
        self.assertIn("Usage:", stderr.getvalue())

    def test_argv_is_used_when_no_arguments_are_passed(self) -> None:
        with unittest.mock.patch("sys.argv", ["jarvis", "wake", "up"]):
            with unittest.mock.patch("frontend.launcher.run", return_value=0) as run:
                self.assertEqual(main(), 0)
        run.assert_called_once_with()


class HelpTextTests(unittest.TestCase):
    def test_help_lists_every_accepted_command(self) -> None:
        for line in ("jarvis", "jarvis wake", "jarvis wake up", "jarvis start"):
            self.assertIn(line, HELP_TEXT)


class PackagingTests(unittest.TestCase):
    """O console script precisa apontar para o entrypoint real."""

    def test_pyproject_declares_the_jarvis_command(self) -> None:
        import tomllib
        from pathlib import Path

        from config.settings import PROJECT_ROOT

        pyproject = Path(PROJECT_ROOT) / "pyproject.toml"
        self.assertTrue(pyproject.is_file(), "pyproject.toml não encontrado")

        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["scripts"]["jarvis"], "frontend.cli:main")

    def test_declared_entrypoint_is_importable_and_callable(self) -> None:
        """Evita o erro clássico de o console script apontar para um nome
        que não existe — só descoberto ao rodar o comando."""
        import importlib

        module = importlib.import_module("frontend.cli")
        self.assertTrue(callable(getattr(module, "main", None)))

    def test_module_entrypoint_still_works(self) -> None:
        """`python -m frontend` não pode ter sido quebrado pelo atalho."""
        import importlib

        launcher = importlib.import_module("frontend.launcher")
        self.assertTrue(callable(getattr(launcher, "run", None)))


if __name__ == "__main__":
    unittest.main()
