import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Botão de ação de uma mensagem (copiar, regenerar). Discreto de propósito:
// é um glifo pequeno que só ganha contraste no hover, para não competir com
// o texto da conversa.
Item {
    id: button

    property string glyph: ""
    property string tooltip: ""
    property bool highlighted: false
    signal clicked()

    implicitWidth: 18
    implicitHeight: 18

    Text {
        anchors.centerIn: parent
        text: button.glyph
        color: button.highlighted
            ? Theme.success
            : (mouseArea.containsMouse ? Theme.textPrimary : Theme.textFaint)
        font.family: Theme.fontFamily
        font.pixelSize: 13
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
    ToolTip.delay: 400
}
