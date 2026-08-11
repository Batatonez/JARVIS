import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Botão de controle de janela (minimizar/maximizar/fechar), desenhado só
// com QML — sem ícones externos.
Item {
    id: button

    property string glyph: "close" // minimize | maximize | restore | close
    property bool danger: false
    property string accessibleText: ""
    signal clicked()

    implicitWidth: 46
    implicitHeight: 40

    readonly property color hoverColor: danger ? Theme.danger : Theme.surfaceHover
    readonly property color glyphColor: mouseArea.containsMouse ? (danger ? "#FFFFFF" : Theme.textPrimary) : Theme.textMuted

    Rectangle {
        anchors.fill: parent
        color: mouseArea.containsMouse ? button.hoverColor : "transparent"
        Behavior on color { ColorAnimation { duration: Theme.durationFast } }
    }

    Item {
        anchors.centerIn: parent
        width: 10
        height: 10

        Rectangle {
            visible: button.glyph === "minimize"
            width: 10; height: 1
            anchors.bottom: parent.bottom
            color: button.glyphColor
        }

        Rectangle {
            visible: button.glyph === "maximize"
            width: 9; height: 9
            border.color: button.glyphColor
            border.width: 1
            color: "transparent"
        }

        Item {
            visible: button.glyph === "restore"
            anchors.fill: parent
            Rectangle {
                x: 2; y: 0; width: 7; height: 7
                border.color: button.glyphColor
                border.width: 1
                color: Theme.surface
            }
            Rectangle {
                x: 0; y: 2; width: 7; height: 7
                border.color: button.glyphColor
                border.width: 1
                color: Theme.surface
            }
        }

        Text {
            visible: button.glyph === "close"
            anchors.centerIn: parent
            text: "✕"
            color: button.glyphColor
            font.pixelSize: 12
        }
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: button.clicked()
    }

    ToolTip.visible: mouseArea.containsMouse && button.accessibleText.length > 0
    ToolTip.text: button.accessibleText
    ToolTip.delay: 500
}
