import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Botão de ícone pequeno para a sidebar (toggle de recolher/expandir). Só
// usado ali — se aparecer uma segunda necessidade genérica no futuro, dá
// pra promover pra algo como WindowButton.
Item {
    id: button

    property string glyph: "collapse"
    property bool flipped: false
    property string tooltip: ""
    signal clicked()

    implicitWidth: 26
    implicitHeight: 26

    readonly property color glyphColor: mouseArea.containsMouse ? Theme.textPrimary : Theme.textFaint

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSmall
        color: mouseArea.containsMouse ? Theme.surfaceHover : "transparent"
        Behavior on color { ColorAnimation { duration: Theme.durationFast } }
    }

    Text {
        anchors.centerIn: parent
        text: button.flipped ? "›" : "‹"
        color: button.glyphColor
        font.pixelSize: 15
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
