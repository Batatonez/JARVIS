"""Smoke test do QML: confirma que `Main.qml` carrega sem erros/warnings —
não testa pixels (ver `frontend/README.md`). Roda com `QT_QPA_PLATFORM`
forçado para `offscreen` se ainda não estiver definido, então nenhuma janela
real é necessária para rodar a suíte. Inspeção visual de verdade continua
sendo manual (`python -m frontend`).

v0.9: o HUD agora é auth-first (`AuthScreen` antes do login, `hudRow`
com a `Sidebar` depois — ver frontend/qml/Main.qml). Os testes cobrem os
dois estados e a transição reativa entre eles, sempre com zero warnings.
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from app.models import RiskLevel  # noqa: E402
from tests.helpers import build_isolated_bridge, build_isolated_voice_service  # noqa: E402

QML_DIR = Path(__file__).resolve().parent.parent / "frontend" / "qml"


class QmlSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._qt_app = QGuiApplication.instance() or QGuiApplication([])
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self._tmp.name)

        self._bridge = build_isolated_bridge(
            self.tmp_path, dev_mode=True, voice_service_factory=build_isolated_voice_service
        )
        await self._bridge.initialize()  # sem sessão local -> permanece deslogado

        self.engine = QQmlApplicationEngine()
        self.engine.addImportPath(str(QML_DIR))
        self.warnings: list[str] = []
        self.engine.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        self.engine.rootContext().setContextProperty("bridge", self._bridge)

    async def asyncTearDown(self) -> None:
        self.engine.deleteLater()
        await self._bridge._shutdown()
        self._tmp.cleanup()

    async def _login(self) -> None:
        await self._bridge._register("alice", "Alice", "alice@example.com", "senha-forte-123")

    def _load(self):
        self.engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
        return self.engine.rootObjects()[0]

    # ------------------------------------------------------------------
    # Auth-first: tela de login antes, HUD depois
    # ------------------------------------------------------------------

    def test_main_qml_loads_without_errors(self) -> None:
        self._load()
        self.assertEqual(self.warnings, [])
        self.assertEqual(len(self.engine.rootObjects()), 1)

    def test_auth_screen_visible_and_hud_hidden_before_login(self) -> None:
        root = self._load()

        auth_screen = root.findChild(QObject, "authScreen")
        hud_row = root.findChild(QObject, "hudRow")

        self.assertIsNotNone(auth_screen)
        self.assertIsNotNone(hud_row)
        self.assertTrue(auth_screen.property("visible"))
        self.assertFalse(hud_row.property("visible"))
        self.assertEqual(self.warnings, [])

    async def test_hud_and_sidebar_become_visible_after_login(self) -> None:
        root = self._load()

        await self._login()
        QTest.qWait(50)  # deixa os bindings reativos (visible: bridge.authenticated) assentarem

        auth_screen = root.findChild(QObject, "authScreen")
        hud_row = root.findChild(QObject, "hudRow")
        sidebar = root.findChild(QObject, "sidebar")

        self.assertFalse(auth_screen.property("visible"))
        self.assertTrue(hud_row.property("visible"))
        self.assertIsNotNone(sidebar)
        self.assertTrue(sidebar.property("visible"))
        self.assertEqual(self.warnings, [])

    async def test_sidebar_reflects_authenticated_user(self) -> None:
        root = self._load()
        await self._login()
        QTest.qWait(50)

        sidebar = root.findChild(QObject, "sidebar")
        current_user = sidebar.property("currentUser")

        self.assertEqual(current_user["username"], "alice")
        self.assertEqual(current_user["plan"], "free")
        self.assertEqual(self.warnings, [])

    async def test_sidebar_collapse_toggle_changes_width_without_warnings(self) -> None:
        root = self._load()
        await self._login()
        QTest.qWait(50)
        sidebar = root.findChild(QObject, "sidebar")
        expanded_width = sidebar.property("width")

        root.setProperty("sidebarExpanded", False)
        QTest.qWait(400)  # Behavior on width anima (Theme.durationSlow)

        self.assertLess(sidebar.property("width"), expanded_width)
        self.assertEqual(self.warnings, [])

        root.setProperty("sidebarExpanded", True)
        QTest.qWait(400)
        self.assertEqual(self.warnings, [])

    async def test_logout_returns_to_auth_screen(self) -> None:
        root = self._load()
        await self._login()
        QTest.qWait(50)

        self._bridge.logout()
        for _ in range(30):
            if not self._bridge.authenticated:
                break
            await asyncio.sleep(0.005)  # `logout()` roda em asyncio, não no loop do Qt — QTest.qWait não avança isso
        QTest.qWait(50)

        auth_screen = root.findChild(QObject, "authScreen")
        hud_row = root.findChild(QObject, "hudRow")
        self.assertTrue(auth_screen.property("visible"))
        self.assertFalse(hud_row.property("visible"))
        self.assertEqual(self.warnings, [])

    # ------------------------------------------------------------------
    # HUD autenticado — mesma cobertura de v0.8, agora atrás do login
    # ------------------------------------------------------------------

    async def test_permission_overlay_hidden_when_no_pending_request(self) -> None:
        root = self._load()
        await self._login()

        overlay = root.findChild(QObject, "permissionOverlay")
        self.assertIsNotNone(overlay)
        self.assertFalse(overlay.property("visible"))
        self.assertIsNone(overlay.property("request"))

    async def test_permission_overlay_visible_with_pending_request(self) -> None:
        root = self._load()
        await self._login()
        overlay = root.findChild(QObject, "permissionOverlay")

        request = self._bridge._app.permissions.request("read_file", "Ler um arquivo", RiskLevel.READ)
        self._bridge._set_pending_permission()
        # `visible` depende da animação de opacity (Behavior, Theme.durationNormal
        # = 220ms) terminar, não só de processar um evento — dá tempo real ao
        # driver de animação do Qt.
        QTest.qWait(350)

        self.assertTrue(overlay.property("hasRequest"))
        self.assertTrue(overlay.property("visible"))
        pending = overlay.property("request")
        self.assertEqual(pending["id"], request.id)
        self.assertEqual(pending["riskLevel"], "read")

        self.assertEqual(self.warnings, [])

    async def test_core_reflects_devmode_simulated_states(self) -> None:
        root = self._load()
        await self._login()
        core = root.findChild(QObject, "jarvisCore")
        self.assertIsNotNone(core)

        for name in ("listening", "thinking", "speaking", "waiting_confirmation", "processing_speech", "error", "idle"):
            self._bridge.simulateState(name)
            self.assertEqual(core.property("state"), name)

        self.assertEqual(self.warnings, [])

    async def test_core_listening_is_visually_distinct_from_thinking(self) -> None:
        # Requisito explícito do v0.7/v0.8: LISTENING não pode se confundir
        # com THINKING (mesmo núcleo, cor de accent diferente).
        root = self._load()
        await self._login()
        core = root.findChild(QObject, "jarvisCore")

        self._bridge.simulateState("thinking")
        QTest.qWait(500)  # Behavior on accent anima por Theme.durationSlow (420ms)
        thinking_accent = core.property("accent")
        self._bridge.simulateState("listening")
        QTest.qWait(500)
        listening_accent = core.property("accent")

        self.assertNotEqual(thinking_accent, listening_accent)
        self.assertTrue(core.property("listening"))

    async def test_devmode_simulated_states_never_apply_when_devmode_off(self) -> None:
        non_dev_bridge = build_isolated_bridge(
            self.tmp_path, dev_mode=False, voice_service_factory=build_isolated_voice_service
        )
        await non_dev_bridge.initialize()
        await non_dev_bridge._register("bob", "Bob", "bob@example.com", "outra-senha-456")
        self.addAsyncCleanup(non_dev_bridge._shutdown)

        self.engine.rootContext().setContextProperty("bridge", non_dev_bridge)
        root = self._load()
        core = root.findChild(QObject, "jarvisCore")

        non_dev_bridge.simulateState("error")
        non_dev_bridge.simulateState("listening")

        self.assertEqual(core.property("state"), "idle")
        overlay = root.findChild(QObject, "permissionOverlay")
        self.assertIsNone(overlay.property("request"))
        self.assertEqual(self.warnings, [])

    async def test_window_resizes_across_target_resolutions_without_warnings(self) -> None:
        root = self._load()
        await self._login()

        for width, height in ((1100, 700), (1280, 720), (1440, 900), (1920, 1080), (2560, 1440)):
            root.setProperty("width", width)
            root.setProperty("height", height)
            QTest.qWait(10)

        self.assertEqual(self.warnings, [])

    async def test_window_resizes_with_sidebar_collapsed_without_warnings(self) -> None:
        root = self._load()
        await self._login()
        root.setProperty("sidebarExpanded", False)
        QTest.qWait(400)

        for width, height in ((1100, 700), (1280, 720), (1440, 900), (1920, 1080), (2560, 1440)):
            root.setProperty("width", width)
            root.setProperty("height", height)
            QTest.qWait(10)

        self.assertEqual(self.warnings, [])


if __name__ == "__main__":
    unittest.main()
