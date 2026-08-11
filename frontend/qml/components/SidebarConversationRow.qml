import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Uma linha de conversa na sidebar — título + botão de excluir (só aparece
// no hover, para não poluir a lista). Realce discreto quando é a conversa
// aberta no momento.
Item {
    id: row

    property string title: ""
    property bool active: false
    signal clicked()
    signal deleteClicked()

    implicitHeight: 30

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSmall
        color: row.active ? Qt.rgba(0.361, 0.882, 0.902, 0.10) : (hoverHandler.hovered ? Theme.surfaceHover : "transparent")
        border.width: row.active ? 1 : 0
        border.color: Theme.borderFocus
        Behavior on color { ColorAnimation { duration: Theme.durationFast } }
    }

    HoverHandler { id: hoverHandler }
    MouseArea {
        anchors.fill: parent
        anchors.rightMargin: deleteButton.visible ? deleteButton.width : 0
        cursorShape: Qt.PointingHandCursor
        onClicked: row.clicked()
    }

    Text {
        anchors.left: parent.left
        anchors.right: deleteButton.left
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: Theme.spacingSm
        anchors.rightMargin: Theme.spacingXs
        text: row.title
        color: row.active ? Theme.textPrimary : Theme.textMuted
        font.family: Theme.fontFamily
        font.pixelSize: 12
        elide: Text.ElideRight
    }

    Item {
        id: deleteButton
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.rightMargin: Theme.spacingXs
        width: 20; height: 20
        visible: hoverHandler.hovered

        Text {
            anchors.centerIn: parent
            text: "✕"
            color: deleteHover.hovered ? Theme.danger : Theme.textFaint
            font.pixelSize: 10
            Behavior on color { ColorAnimation { duration: Theme.durationFast } }
        }
        HoverHandler { id: deleteHover }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: row.deleteClicked()
        }
        ToolTip.visible: deleteHover.hovered
        ToolTip.text: "Excluir conversa"
        ToolTip.delay: 500
    }
}
