"""Layout dos modais (v1.2) — regressão do overlay "torto".

Estes testes MEDEM geometria real (x/width/childrenRect) em várias
resoluções, em vez de olhar aparência. O bug original era objetivo e
mensurável: a linha de botões do overlay de e-mail tinha `width=364` com
`childrenRect.width=616`, e dois botões em x negativo (-70 e -252),
desenhados fora do cartão.

Causa: `Row` não quebra linha nem encolhe filhos — três botões
("VERIFICAR", "REENVIAR CÓDIGO", "AGORA NÃO") somam ~600px e nunca caberiam
nos 364px disponíveis. Corrigido com `ModalButtonRow` (um `Flow`),
compartilhado por todos os overlays.
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

from tests.helpers import build_isolated_bridge, build_isolated_voice_service  # noqa: E402

QML_DIR = Path(__file__).resolve().parent.parent / "frontend" / "qml"

# Resoluções alvo do projeto + duas propositalmente apertadas, que é onde
# um layout não responsivo quebra primeiro.
_RESOLUTIONS = ((1100, 700), (1280, 720), (1440, 900), (1920, 1080), (800, 600), (700, 520))

_OVERLAYS = (
    ("emailVerificationOpen", "emailVerificationOverlay"),
    ("voiceSetupOpen", "voiceSetupOverlay"),
    ("accountPanelOpen", "accountPanel"),
)


class OverlayLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._qt_app = QGuiApplication.instance() or QGuiApplication([])
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._bridge = build_isolated_bridge(
            Path(self._tmp.name), dev_mode=True, voice_service_factory=build_isolated_voice_service
        )
        await self._bridge.initialize()
        await self._bridge._register("alice", "Alice", "alice@example.com", "senha-forte-123")

        self.engine = QQmlApplicationEngine()
        self.engine.addImportPath(str(QML_DIR))
        self.warnings: list[str] = []
        self.engine.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        self.engine.rootContext().setContextProperty("bridge", self._bridge)
        self.engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
        self.root = self.engine.rootObjects()[0]
        QTest.qWait(80)

    async def asyncTearDown(self) -> None:
        self.engine.deleteLater()
        await self._bridge._shutdown()
        self._tmp.cleanup()

    @staticmethod
    def _card_of(overlay: QObject) -> QObject | None:
        """O cartão é o maior filho que NÃO ocupa a largura toda (o fundo
        escurecido ocupa)."""
        overlay_width = float(overlay.property("width"))
        best = None
        for child in overlay.findChildren(QObject):
            width, height = child.property("width"), child.property("height")
            if width is None or height is None:
                continue
            width, height = float(width), float(height)
            if width >= overlay_width - 1:
                continue
            area = width * height
            if best is None or area > best[0]:
                best = (area, child)
        return best[1] if best else None

    def _button_rows(self, overlay: QObject) -> list[QObject]:
        return [
            child
            for child in overlay.findChildren(QObject)
            if "ModalButtonRow" in child.metaObject().className()
        ]

    async def test_cards_never_overflow_the_window(self) -> None:
        for prop, name in _OVERLAYS:
            self.root.setProperty(prop, True)
            QTest.qWait(300)
            overlay = self.root.findChild(QObject, name)
            self.assertIsNotNone(overlay, name)

            for width, height in _RESOLUTIONS:
                self.root.setProperty("width", width)
                self.root.setProperty("height", height)
                QTest.qWait(60)

                card = self._card_of(overlay)
                self.assertIsNotNone(card, f"{name} @ {width}x{height}")
                x, y = float(card.property("x")), float(card.property("y"))
                card_width, card_height = float(card.property("width")), float(card.property("height"))

                self.assertGreaterEqual(x, 0, f"{name} @ {width}x{height}: vaza pela esquerda")
                self.assertLessEqual(
                    x + card_width, width, f"{name} @ {width}x{height}: vaza pela direita"
                )
                self.assertGreaterEqual(y, 0, f"{name} @ {width}x{height}: vaza pelo topo")
                self.assertLessEqual(
                    y + card_height, height, f"{name} @ {width}x{height}: vaza por baixo"
                )

            self.root.setProperty(prop, False)
            QTest.qWait(120)

    async def test_cards_stay_horizontally_centered(self) -> None:
        for prop, name in _OVERLAYS:
            self.root.setProperty(prop, True)
            QTest.qWait(300)
            overlay = self.root.findChild(QObject, name)

            for width, height in _RESOLUTIONS:
                self.root.setProperty("width", width)
                self.root.setProperty("height", height)
                QTest.qWait(60)
                card = self._card_of(overlay)
                x, card_width = float(card.property("x")), float(card.property("width"))
                # Margem de 1px para arredondamento de layout.
                self.assertAlmostEqual(
                    x, (width - card_width) / 2, delta=1.0,
                    msg=f"{name} @ {width}x{height}: não centralizado",
                )

            self.root.setProperty(prop, False)
            QTest.qWait(120)

    async def test_button_rows_never_overflow_their_width(self) -> None:
        """O defeito exato do bug: conteúdo mais largo que a linha.

        Falha se alguém trocar `ModalButtonRow` de volta por um `Row`, ou
        adicionar um botão de rótulo longo demais."""
        for prop, name in _OVERLAYS:
            self.root.setProperty(prop, True)
            QTest.qWait(300)
            overlay = self.root.findChild(QObject, name)

            for width, height in _RESOLUTIONS:
                self.root.setProperty("width", width)
                self.root.setProperty("height", height)
                QTest.qWait(60)

                for row in self._button_rows(overlay):
                    row_width = float(row.property("width"))
                    children_rect = row.property("childrenRect")
                    if children_rect is None or row_width <= 0:
                        continue
                    self.assertLessEqual(
                        children_rect.width(), row_width + 0.5,
                        f"{name} @ {width}x{height}: botões estouram a linha "
                        f"({children_rect.width():.0f} > {row_width:.0f})",
                    )

            self.root.setProperty(prop, False)
            QTest.qWait(120)

    async def test_buttons_are_never_positioned_outside_their_row(self) -> None:
        """No bug, dois botões ficavam em x=-70 e x=-252."""
        for prop, name in _OVERLAYS:
            self.root.setProperty(prop, True)
            QTest.qWait(300)
            overlay = self.root.findChild(QObject, name)

            for width, height in ((1100, 700), (700, 520)):
                self.root.setProperty("width", width)
                self.root.setProperty("height", height)
                QTest.qWait(60)

                for row in self._button_rows(overlay):
                    row_width = float(row.property("width"))
                    for child in row.children():
                        if not child.property("label"):
                            continue
                        if not child.property("visible"):
                            continue
                        child_x = float(child.property("x"))
                        child_width = float(child.property("width"))
                        self.assertGreaterEqual(
                            child_x, 0,
                            f"{name} @ {width}x{height}: botão "
                            f"{child.property('label')!r} em x negativo ({child_x:.0f})",
                        )
                        self.assertLessEqual(
                            child_x + child_width, row_width + 0.5,
                            f"{name} @ {width}x{height}: botão "
                            f"{child.property('label')!r} passa da linha",
                        )

            self.root.setProperty(prop, False)
            QTest.qWait(120)

    async def test_no_qml_warnings_while_opening_every_overlay(self) -> None:
        for prop, _name in _OVERLAYS:
            self.root.setProperty(prop, True)
            QTest.qWait(300)
            self.root.setProperty(prop, False)
            QTest.qWait(150)

        self.assertEqual(self.warnings, [])


if __name__ == "__main__":
    unittest.main()
