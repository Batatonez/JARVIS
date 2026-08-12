import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Painel de conversa. Scroll inteligente: só acompanha mensagens novas
// automaticamente se o usuário já estava perto do fim; não puxa para baixo
// se ele estiver lendo uma mensagem antiga.
Item {
    id: panel

    property alias model: listView.model
    // Resposta em andamento (response.started sem response.completed/failed
    // ainda) — liga um indicador discreto, sem inventar texto de resposta.
    property bool pending: false
    readonly property bool hasMessages: listView.count > 0

    Column {
        anchors.centerIn: parent
        spacing: Theme.spacingSm
        visible: !panel.hasMessages
        opacity: 0.9

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "JARVIS"
            color: Theme.textMuted
            font.family: Theme.fontFamily
            font.pixelSize: 13
            font.letterSpacing: Theme.letterSpacingLabel * 2
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Como posso ajudar?"
            color: Theme.textSecondary
            font.family: Theme.fontFamily
            font.pixelSize: 15
        }
    }

    ListView {
        id: listView
        objectName: "chatListView"  // usado pelo smoke test de QML para inspecionar os delegates
        anchors.fill: parent
        visible: panel.hasMessages
        spacing: Theme.spacingMd
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        property bool stickToBottom: true
        onMovementEnded: stickToBottom = atYEnd
        onContentHeightChanged: if (stickToBottom) Qt.callLater(listView.positionViewAtEnd)

        // As propriedades vêm dos *role names* do MessageListModel
        // (`isUser`, `content`, `timestamp` — ver frontend/message_model.py),
        // injetadas automaticamente pelo Qt porque o MessageItem as declara
        // como `required property`.
        //
        // NÃO reintroduzir `isUser: model.isUser` (e afins) aqui: a partir do
        // Qt 6, um delegate com required properties deixa de receber o objeto
        // de contexto `model`, então esses bindings resolvem para `undefined`
        // e falham em silêncio — `content` virava "" (mensagem sem texto) e
        // `isUser` virava false (toda mensagem rotulada "JARVIS", inclusive as
        // do usuário). Foi exatamente o bug corrigido na v1.1.
        delegate: MessageItem {
            width: listView.width
        }

        add: Transition {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.durationNormal; easing.type: Easing.OutCubic }
        }

        footer: Item {
            width: listView.width
            height: panel.pending ? 30 : 0
            visible: panel.pending
            Behavior on height { NumberAnimation { duration: Theme.durationFast } }

            Row {
                x: Theme.spacingMd
                anchors.verticalCenter: parent.verticalCenter
                spacing: Theme.spacingSm

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "JARVIS"
                    color: Theme.cyan
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    font.letterSpacing: Theme.letterSpacingLabel
                }
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 4; height: 4; radius: 2
                    color: Theme.cyan
                    SequentialAnimation on opacity {
                        loops: Animation.Infinite
                        NumberAnimation { to: 0.3; duration: 700; easing.type: Easing.InOutSine }
                        NumberAnimation { to: 1.0; duration: 700; easing.type: Easing.InOutSine }
                    }
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "PROCESSING"
                    color: Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    font.letterSpacing: Theme.letterSpacingLabel
                }
                Row {
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 4
                    Repeater {
                        model: 3
                        delegate: Rectangle {
                            required property int index
                            width: 3; height: 3; radius: 1.5
                            color: Theme.textFaint
                            SequentialAnimation on opacity {
                                loops: Animation.Infinite
                                PauseAnimation { duration: index * 160 }
                                NumberAnimation { to: 1.0; duration: 420; easing.type: Easing.InOutSine }
                                NumberAnimation { to: 0.25; duration: 420; easing.type: Easing.InOutSine }
                            }
                        }
                    }
                }
            }
        }

        ScrollBar.vertical: ScrollBar {
            policy: ScrollBar.AsNeeded
        }
    }
}
