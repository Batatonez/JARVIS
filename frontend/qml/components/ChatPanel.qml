import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Painel de conversa. Scroll inteligente: só acompanha mensagens novas
// automaticamente se o usuário já estava perto do fim; não puxa para baixo
// se ele estiver lendo uma mensagem antiga.
Item {
    id: panel

    property alias model: listView.model
    readonly property bool hasMessages: listView.count > 0

    Column {
        anchors.centerIn: parent
        spacing: Theme.spacingXs
        visible: !panel.hasMessages
        opacity: 0.85

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "JARVIS"
            color: Theme.textFaint
            font.family: Theme.fontFamily
            font.pixelSize: 13
            font.letterSpacing: Theme.letterSpacingLabel * 2
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Como posso ajudar?"
            color: Theme.textMuted
            font.family: Theme.fontFamily
            font.pixelSize: 15
        }
    }

    ListView {
        id: listView
        anchors.fill: parent
        visible: panel.hasMessages
        spacing: Theme.spacingMd
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        property bool stickToBottom: true
        onMovementEnded: stickToBottom = atYEnd
        onContentHeightChanged: if (stickToBottom) Qt.callLater(listView.positionViewAtEnd)

        delegate: MessageItem {
            width: listView.width
            isUser: model.isUser
            content: model.content
            timestamp: model.timestamp
        }

        add: Transition {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.durationNormal; easing.type: Easing.OutCubic }
        }

        ScrollBar.vertical: ScrollBar {
            policy: ScrollBar.AsNeeded
        }
    }
}
