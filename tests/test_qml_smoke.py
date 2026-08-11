"""Smoke test do QML: confirma que `Main.qml` carrega sem erros/warnings —
não testa pixels (ver `frontend/README.md`). Roda com `QT_QPA_PLATFORM`
forçado para `offscreen` se ainda não estiver definido, então nenhuma janela
real é necessária para rodar a suíte. Inspeção visual de verdade continua
sendo manual (`python -m frontend`).
"""

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
from frontend.bridge import JarvisBridge  # noqa: E402
from tests.helpers import build_isolated_application  # noqa: E402

QML_DIR = Path(__file__).resolve().parent.parent / "frontend" / "qml"


class QmlSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

        self._qt_app = QGuiApplication.instance() or QGuiApplication([])
        application = build_isolated_application(Path(self._tmp.name))
        self._bridge = JarvisBridge(application, dev_mode=True)

        self.engine = QQmlApplicationEngine()
        self.engine.addImportPath(str(QML_DIR))
        self.warnings: list[str] = []
        self.engine.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        self.engine.rootContext().setContextProperty("bridge", self._bridge)
        self.addCleanup(self.engine.deleteLater)

    def test_main_qml_loads_without_errors(self) -> None:
        self.engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))

        self.assertEqual(self.warnings, [])
        self.assertEqual(len(self.engine.rootObjects()), 1)

    def test_permission_overlay_hidden_when_no_pending_request(self) -> None:
        self.engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
        root = self.engine.rootObjects()[0]

        overlay = root.findChild(QObject, "permissionOverlay")
        self.assertIsNotNone(overlay)
        self.assertFalse(overlay.property("visible"))
        self.assertIsNone(overlay.property("request"))

    def test_permission_overlay_visible_with_pending_request(self) -> None:
        self.engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
        root = self.engine.rootObjects()[0]
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

    def test_core_reflects_devmode_simulated_states(self) -> None:
        self.engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
        root = self.engine.rootObjects()[0]
        core = root.findChild(QObject, "jarvisCore")
        self.assertIsNotNone(core)

        for name in ("listening", "thinking", "speaking", "waiting_confirmation", "processing_speech", "error", "idle"):
            self._bridge.simulateState(name)
            self.assertEqual(core.property("state"), name)

        self.assertEqual(self.warnings, [])

    def test_core_listening_is_visually_distinct_from_thinking(self) -> None:
        # Requisito explícito do v0.7/v0.8: LISTENING não pode se confundir
        # com THINKING (mesmo núcleo, cor de accent diferente).
        self.engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
        root = self.engine.rootObjects()[0]
        core = root.findChild(QObject, "jarvisCore")

        self._bridge.simulateState("thinking")
        QTest.qWait(500)  # Behavior on accent anima por Theme.durationSlow (420ms)
        thinking_accent = core.property("accent")
        self._bridge.simulateState("listening")
        QTest.qWait(500)
        listening_accent = core.property("accent")

        self.assertNotEqual(thinking_accent, listening_accent)
        self.assertTrue(core.property("listening"))

    def test_devmode_simulated_states_never_apply_when_devmode_off(self) -> None:
        application = build_isolated_application(Path(self._tmp.name))
        bridge = JarvisBridge(application, dev_mode=False)
        self.engine.rootContext().setContextProperty("bridge", bridge)
        self.engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
        root = self.engine.rootObjects()[0]
        core = root.findChild(QObject, "jarvisCore")

        bridge.simulateState("error")
        bridge.simulateState("listening")

        self.assertEqual(core.property("state"), "idle")
        overlay = root.findChild(QObject, "permissionOverlay")
        self.assertIsNone(overlay.property("request"))
        self.assertEqual(self.warnings, [])

    def test_window_resizes_across_target_resolutions_without_warnings(self) -> None:
        self.engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
        root = self.engine.rootObjects()[0]

        for width, height in ((1100, 700), (1280, 720), (1440, 900), (1920, 1080), (2560, 1440)):
            root.setProperty("width", width)
            root.setProperty("height", height)
            QTest.qWait(10)

        self.assertEqual(self.warnings, [])


if __name__ == "__main__":
    unittest.main()
