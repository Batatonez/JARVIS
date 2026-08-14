import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Botão de ação genérico e reutilizável (permissões, nova conversa, etc.).
// Tooltip embutido — qualquer instância com `tooltip` definido já é acessível.
Rectangle {
    id: button

    property string label: ""
    property string tooltip: ""
    property bool emphasis: false
    property color tint: Theme.cyan
    signal clicked()

    // Nunca mais largo que o container. Sem este teto, um rótulo longo
    // (ex.: "BAIXAR MODELO DE VOZ (~45 MB)", 436px) transbordava do cartão
    // do modal, que só tinha 384px — e nem um `Flow` resolve isso, porque
    // um item sozinho maior que a linha não tem para onde quebrar. Medido e
    // corrigido na v1.2 (ver tests/test_overlay_layout.py).
    readonly property real _naturalWidth: labelText.implicitWidth + Theme.spacingLg * 2
    implicitWidth: parent ? Math.min(_naturalWidth, parent.width) : _naturalWidth
    implicitHeight: 36
    radius: Theme.radiusMedium
    color: emphasis ? (mouseArea.containsMouse ? Theme.primaryBright : tint) : (mouseArea.containsMouse ? Theme.surfaceHover : "transparent")
    border.width: emphasis ? 0 : 1
    border.color: mouseArea.containsMouse ? Theme.borderStrong : Qt.rgba(1, 1, 1, 0.14)
    opacity: !button.enabled ? 0.4 : (mouseArea.pressed ? 0.85 : 1)
    scale: mouseArea.pressed ? 0.97 : 1
    Behavior on color { ColorAnimation { duration: Theme.durationFast } }
    Behavior on border.color { ColorAnimation { duration: Theme.durationFast } }
    Behavior on opacity { NumberAnimation { duration: Theme.durationFast } }
    Behavior on scale { NumberAnimation { duration: Theme.durationFast } }

    Text {
        id: labelText
        anchors.centerIn: parent
        // Quando o botão foi limitado pela largura do container, o texto
        // encolhe com reticências em vez de vazar. `implicitWidth` (largura
        // natural do texto) não depende de `width`, então não há ciclo.
        width: Math.min(implicitWidth, Math.max(0, button.width - Theme.spacingLg * 2))
        elide: Text.ElideRight
        horizontalAlignment: Text.AlignHCenter
        text: button.label
        color: button.emphasis ? Theme.background : (mouseArea.containsMouse ? Theme.textPrimary : Theme.textMuted)
        font.family: Theme.fontFamily
        font.pixelSize: 12
        font.weight: Font.DemiBold
        font.letterSpacing: Theme.letterSpacingLabel
        Behavior on color { ColorAnimation { duration: Theme.durationFast } }
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: button.clicked()
    }

    ToolTip.visible: mouseArea.containsMouse && button.tooltip.length > 0
    ToolTip.text: button.tooltip
    ToolTip.delay: 500
}
