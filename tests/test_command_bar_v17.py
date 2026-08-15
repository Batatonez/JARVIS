"""Universal Command Bar e controle do computador (v1.7).

Nenhum teste abre aplicativo de verdade, mexe no volume da máquina, tira
print ou encerra processo: o `SystemControl` é substituído por um fake que só
registra o que foi pedido, e o `AppResolver` lê um Menu Iniciar temporário
com atalhos falsos.
"""

import tempfile
import unittest
from pathlib import Path

from app.actions import ActionRequest, ActionType, action_from_intent, permission_of, risk_of
from app.command_bar import CommandBarService, _safe_eval
from app.intents import Intent, IntentRouter
from app.models import RiskLevel
from services.system.app_resolver import AppResolver


class _FakeSystem:
    """Registra chamadas em vez de tocar o sistema. Cada método devolve o
    mesmo formato `(ok, detalhe)` do `SystemControl` real."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple] = []
        self._fail = fail

    def _record(self, name, *args):
        self.calls.append((name, *args))
        return (not self._fail, "falhou" if self._fail else f"{name} ok")

    def open_path(self, target):
        return self._record("open_path", str(target))

    def open_url(self, url):
        return self._record("open_url", url)

    def close_app(self, name):
        return self._record("close_app", name)

    def volume_up(self):
        return self._record("volume_up")

    def volume_down(self):
        return self._record("volume_down")

    def volume_set(self, level):
        return self._record("volume_set", level)

    def mute(self):
        return self._record("mute")

    def unmute(self):
        return self._record("unmute")

    def screenshot(self):
        self.calls.append(("screenshot",))
        return (not self._fail, "C:/tmp/print.png")

    def read_clipboard(self):
        self.calls.append(("read_clipboard",))
        return (not self._fail, "conteudo copiado")

    def list_processes(self, *, limit=12):
        self.calls.append(("list_processes",))
        from services.system.system_control import ProcessInfo

        return (not self._fail, [ProcessInfo("chrome.exe", 1, 500.0)])

    def system_info(self):
        self.calls.append(("system_info",))
        return (not self._fail, {"sistema": "Windows 11"})

    def called(self, name: str) -> bool:
        return any(call[0] == name for call in self.calls)


def _service(**kwargs) -> tuple[CommandBarService, _FakeSystem]:
    system = kwargs.pop("system", None) or _FakeSystem()
    return CommandBarService(system=system, **kwargs), system


# ======================================================================
# ROTEAMENTO DE INTENÇÃO
# ======================================================================


class IntentRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = IntentRouter()

    def test_open_app_commands(self) -> None:
        for text in ("abrir Spotify", "abre Discord", "abrir VS Code", "open Steam"):
            routed = self.router.route(text)
            self.assertIs(routed.intent, Intent.OPEN_APP, text)
            self.assertTrue(routed.parameters["target"])

    def test_target_keeps_the_original_casing(self) -> None:
        """O nome vai para a resolução de aplicativo como está — devolver
        "vs code" onde o Menu Iniciar tem "VS Code" atrapalharia a busca."""
        self.assertEqual(self.router.route("abrir VS Code").parameters["target"], "VS Code")

    def test_system_actions(self) -> None:
        expected = {
            "diminuir volume": "volume_down",
            "aumentar volume": "volume_up",
            "volume 50": "volume_set",
            "mudo": "mute",
            "tira um print": "screenshot",
            "o que eu copiei?": "clipboard_read",
            "quais apps estão usando mais memória?": "list_processes",
        }
        for text, action in expected.items():
            routed = self.router.route(text)
            self.assertIs(routed.intent, Intent.SYSTEM_ACTION, text)
            self.assertEqual(routed.parameters["action"], action, text)

    def test_math_and_percentage(self) -> None:
        for text in ("quanto é 2+2", "qual é 15% de 250?", "calcular 10*3"):
            self.assertIs(self.router.route(text).intent, Intent.TOOL, text)

    def test_conversation_is_never_mistaken_for_a_command(self) -> None:
        """O custo dos dois erros não é simétrico: um comando virar conversa é
        inofensivo; uma conversa virar comando abre programa sem pedido."""
        for text in (
            "qual é a capital do Japão?",
            "o spotify travou de novo",
            "me explica por que 2+2 é 4",
            "abrir uma discussão sobre o projeto amanhã",
            "acha um jeito de resolver isso",
            "2024",
            "você pode aumentar a fonte?",
        ):
            self.assertIs(self.router.route(text).intent, Intent.CHAT, text)

    def test_slash_commands_stay_with_the_command_registry(self) -> None:
        self.assertIs(self.router.route("/status").intent, Intent.CHAT)
        self.assertIs(self.router.route("/new").intent, Intent.CHAT)

    def test_broad_destructive_phrasing_is_not_a_command(self) -> None:
        """"mata todos os processos python" vai para a IA, que pode explicar,
        em vez de virar uma ação de escopo amplo."""
        self.assertIs(self.router.route("mata todos os processos python").intent, Intent.CHAT)

    def test_empty_input_is_unknown(self) -> None:
        self.assertIs(self.router.route("").intent, Intent.UNKNOWN)

    def test_url_keeps_its_case(self) -> None:
        routed = self.router.route("abrir https://example.com/Path/To")
        self.assertEqual(routed.parameters["url"], "https://example.com/Path/To")

    def test_accents_do_not_break_matching(self) -> None:
        self.assertIs(self.router.route("abrir configurações").intent, Intent.SETTINGS)
        self.assertIs(self.router.route("abrir configuracoes").intent, Intent.SETTINGS)

    def test_router_never_executes_anything(self) -> None:
        """`route()` é puro: entender e executar são separados por desenho."""
        import inspect

        source = inspect.getsource(IntentRouter)
        for forbidden in ("startfile", "subprocess", "Popen", "system("):
            self.assertNotIn(forbidden, source)


# ======================================================================
# RISCO, PERMISSÃO E CONFIRMAÇÃO
# ======================================================================


class RiskAndPermissionTests(unittest.TestCase):
    def test_read_actions_are_low_risk(self) -> None:
        for action in (ActionType.CLIPBOARD_READ, ActionType.LIST_PROCESSES, ActionType.SYSTEM_INFO):
            self.assertIs(risk_of(action), RiskLevel.READ, action)

    def test_reversible_actions_are_medium_risk(self) -> None:
        for action in (ActionType.OPEN_APP, ActionType.VOLUME_UP, ActionType.SCREENSHOT):
            self.assertIs(risk_of(action), RiskLevel.ACTION, action)

    def test_closing_an_app_is_high_risk(self) -> None:
        """É a única ação desta versão que pode descartar trabalho não salvo."""
        self.assertIs(risk_of(ActionType.CLOSE_APP), RiskLevel.DANGEROUS)

    def test_high_risk_always_requires_confirmation(self) -> None:
        request = ActionRequest(ActionType.CLOSE_APP, {"target": "Spotify"})
        self.assertTrue(request.requires_confirmation)

    def test_low_and_medium_risk_do_not_require_confirmation(self) -> None:
        for action in (ActionType.OPEN_APP, ActionType.CLIPBOARD_READ, ActionType.VOLUME_UP):
            self.assertFalse(ActionRequest(action).requires_confirmation, action)

    def test_every_action_declares_a_permission(self) -> None:
        for action in ActionType:
            self.assertIsInstance(permission_of(action), str)

    def test_unknown_action_defaults_to_dangerous(self) -> None:
        """Falhar para o lado seguro: uma ação nova sem classificação
        explícita não passa despercebida."""

        class _Fake(str):
            pass

        self.assertIs(risk_of(_Fake("acao_inventada")), RiskLevel.DANGEROUS)

    def test_action_carries_its_source(self) -> None:
        """Auditável: uma ação perigosa proposta pelo modelo é distinguível
        de uma digitada pelo usuário."""
        request = ActionRequest(ActionType.CLOSE_APP, {}, source="ai")
        self.assertEqual(request.source, "ai")


class ConfirmationFlowTests(unittest.TestCase):
    def test_high_risk_command_is_not_executed_on_submit(self) -> None:
        service, system = _service()
        result = service.submit("fecha Spotify")
        self.assertTrue(result.needs_confirmation)
        self.assertFalse(result.ok)
        self.assertFalse(system.called("close_app"), "executou sem confirmação")

    def test_high_risk_command_runs_only_after_confirm(self) -> None:
        service, system = _service()
        pending = service.submit("fecha Spotify")
        result = service.confirm(pending.request)
        self.assertTrue(result.ok)
        self.assertTrue(system.called("close_app"))

    def test_direct_execute_of_a_dangerous_action_still_asks(self) -> None:
        """`execute()` é público — uma chamada direta não pode escapar da
        regra só por ter pulado a porta da frente."""
        service, system = _service()
        result = service.execute(ActionRequest(ActionType.CLOSE_APP, {"target": "x"}))
        self.assertTrue(result.needs_confirmation)
        self.assertFalse(system.called("close_app"))

    def test_ai_proposed_dangerous_action_gets_no_shortcut(self) -> None:
        """Um provider não é um usuário autenticado: propor não é autorizar."""
        service, system = _service()
        result = service.execute(ActionRequest(ActionType.CLOSE_APP, {"target": "x"}, source="ai"))
        self.assertTrue(result.needs_confirmation)
        self.assertFalse(system.called("close_app"))


# ======================================================================
# EXECUÇÃO
# ======================================================================


class ExecutionTests(unittest.TestCase):
    def _apps(self, tmp: Path) -> AppResolver:
        programs = tmp / "Programs"
        programs.mkdir(parents=True)
        for name in ("Spotify.lnk", "Discord.lnk", "Spotify Web Helper.lnk", "Uninstall Spotify.lnk"):
            (programs / name).write_text("", encoding="utf-8")
        return AppResolver(start_menu_dirs=[programs])

    def test_open_app_resolves_and_opens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, system = _service(apps=self._apps(Path(tmp)))
            result = service.submit("abrir Spotify")
            self.assertTrue(result.ok)
            self.assertTrue(system.called("open_path"))
            self.assertIn("Spotify", result.detail)

    def test_shortest_name_wins_over_a_longer_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resolver = self._apps(Path(tmp))
            self.assertEqual(resolver.resolve("spotify").name, "Spotify")

    def test_uninstall_entries_are_filtered_out(self) -> None:
        """"abrir spotify" nunca pode resolver para "Uninstall Spotify"."""
        with tempfile.TemporaryDirectory() as tmp:
            names = [app.name for app in self._apps(Path(tmp)).all_apps()]
            self.assertNotIn("Uninstall Spotify", names)

    def test_unknown_app_reports_instead_of_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, system = _service(apps=self._apps(Path(tmp)))
            result = service.submit("abrir AplicativoQueNaoExiste")
            self.assertFalse(result.ok)
            self.assertFalse(system.called("open_path"))
            self.assertIn("não encontrei", result.detail.lower())

    def test_volume_commands_reach_the_system(self) -> None:
        for text, call in (("diminuir volume", "volume_down"), ("aumentar volume", "volume_up"),
                           ("mudo", "mute"), ("volume 40", "volume_set")):
            service, system = _service()
            service.submit(text)
            self.assertTrue(system.called(call), text)

    def test_screenshot_offers_quick_actions_instead_of_printing_the_path(self) -> None:
        service, system = _service()
        result = service.submit("tira um print")
        self.assertTrue(result.ok)
        self.assertIn("open_screenshot", result.quick_actions)
        self.assertNotIn("C:/", result.detail)

    def test_processes_are_listed_readably(self) -> None:
        service, _ = _service()
        result = service.submit("quais apps estão usando mais memória?")
        self.assertTrue(result.ok)
        self.assertIn("chrome.exe", result.detail)

    def test_chat_input_is_not_handled_locally(self) -> None:
        service, system = _service()
        result = service.submit("qual é a capital do Japão?")
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "")
        self.assertEqual(system.calls, [])

    def test_features_of_later_versions_say_so_instead_of_pretending(self) -> None:
        """A IA responderia "claro, vou lembrar" sem agendar nada."""
        service, _ = _service()
        self.assertIn("não est", service.submit("me lembra da prova amanhã").detail.lower())
        self.assertIn("não est", service.submit("acha meu arquivo de história").detail.lower())

    def test_system_failure_is_reported_not_raised(self) -> None:
        service, _ = _service(system=_FakeSystem(fail=True))
        result = service.submit("diminuir volume")
        self.assertFalse(result.ok)
        self.assertTrue(result.detail)


class CalculatorTests(unittest.TestCase):
    def test_basic_arithmetic(self) -> None:
        service, _ = _service()
        self.assertEqual(service.submit("quanto é 2+2").detail, "4")
        self.assertEqual(service.submit("quanto é 10*3").detail, "30")

    def test_percentage(self) -> None:
        service, _ = _service()
        self.assertEqual(service.submit("qual é 15% de 250?").detail, "37.5")

    def test_evaluator_rejects_anything_that_is_not_arithmetic(self) -> None:
        """Não é um `eval` restrito: é um interpretador de aritmética, e
        nenhum objeto Python é alcançável a partir dele."""
        for dangerous in (
            "__import__('os').system('calc')",
            "open('x')",
            "[].__class__",
            "().__class__.__bases__",
            "exec('x=1')",
            "1if True else 2",
        ):
            self.assertIsNone(_safe_eval(dangerous), dangerous)

    def test_evaluator_refuses_resource_exhaustion(self) -> None:
        self.assertIsNone(_safe_eval("9**9**9"))
        self.assertIsNone(_safe_eval("2" + "+2" * 500))

    def test_division_by_zero_is_handled(self) -> None:
        self.assertIsNone(_safe_eval("1/0"))

    def test_no_eval_or_exec_in_the_module(self) -> None:
        source = (Path(__file__).resolve().parent.parent / "app" / "command_bar.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("eval(", source.replace("_safe_eval(", "").replace("def _safe_eval", ""))
        self.assertNotIn("exec(", source)


# ======================================================================
# OFFLINE
# ======================================================================


class OfflineTests(unittest.TestCase):
    """Requisito da v1.7: abrir o Spotify não precisa de nuvem. Com todos os
    providers fora do ar, tudo que é local continua funcionando."""

    def test_no_command_module_imports_an_ai_provider(self) -> None:
        """Verifica os IMPORTS, não a prosa: os docstrings destes módulos
        mencionam `ProviderRouterAIService` para explicar de onde a IA entra
        no fluxo, e citar não é depender."""
        import ast
        import inspect

        from app import actions, command_bar, intents

        for module in (intents, actions, command_bar):
            tree = ast.parse(inspect.getsource(module))
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported += [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imported.append(node.module or "")
                    imported += [alias.name for alias in node.names]
            joined = " ".join(imported).lower()
            for forbidden in ("provider", "openrouter", "ai_service", "anthropic"):
                self.assertNotIn(forbidden, joined, f"{module.__name__} importa IA")

    def test_local_commands_work_with_no_ai_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            programs = Path(tmp) / "Programs"
            programs.mkdir()
            (programs / "Spotify.lnk").write_text("", encoding="utf-8")
            service, system = _service(apps=AppResolver(start_menu_dirs=[programs]))
            for text in ("abrir Spotify", "diminuir volume", "quanto é 2+2", "tira um print"):
                self.assertTrue(service.submit(text).ok, text)

    def test_system_control_never_imports_a_provider(self) -> None:
        source = (
            Path(__file__).resolve().parent.parent / "services" / "system" / "system_control.py"
        ).read_text(encoding="utf-8")
        for forbidden in ("openrouter", "ProviderRouter", "anthropic"):
            self.assertNotIn(forbidden, source.lower())


# ======================================================================
# ATALHO
# ======================================================================


class ShortcutTests(unittest.TestCase):
    QML = Path(__file__).resolve().parent.parent / "frontend" / "qml" / "Main.qml"

    def test_command_bar_uses_ctrl_k(self) -> None:
        self.assertIn('sequence: "Ctrl+K"', self.QML.read_text(encoding="utf-8"))

    def test_ctrl_space_still_belongs_to_the_microphone(self) -> None:
        """Conflito auditado: `Ctrl+Space` é o push-to-talk desde a v1.3 e
        está documentado no tooltip do MicButton. Reaproveitá-lo criaria dois
        handlers disputando o mesmo atalho."""
        qml = self.QML.read_text(encoding="utf-8")
        index = qml.index('sequence: "Ctrl+Space"')
        following = qml[index : index + 400]
        self.assertIn("handleMicToggle", following)

    def test_each_shortcut_is_bound_only_once(self) -> None:
        qml = self.QML.read_text(encoding="utf-8")
        for sequence in ('"Ctrl+K"', '"Ctrl+Space"'):
            self.assertEqual(qml.count(f"sequence: {sequence}"), 1, sequence)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
