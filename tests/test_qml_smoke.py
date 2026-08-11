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

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
