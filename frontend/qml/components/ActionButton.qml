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

    implicitWidth: labelText.implicitWidth + Theme.spacingLg * 2
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
