import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Entrada de mensagem: Enter envia, Shift+Enter quebra linha, multiline com
// limite razoável. O botão de enviar vira STOP quando `busy` — cancela em
// vez de mandar outra mensagem (nunca deixa o usuário "spammar" enquanto
// o JARVIS está processando).
Item {
    id: bar

    property bool busy: false
    property int maxLength: 4000
    signal sendRequested(string text)
    signal cancelRequested()

    readonly property bool hasText: textArea.text.trim().length > 0

    implicitHeight: Math.min(168, Math.max(56, contentRow.implicitHeight + Theme.spacingMd * 2))

    function trySend() {
        const text = textArea.text.trim()
        if (text.length === 0 || bar.busy) return
        bar.sendRequested(text)
        textArea.clear()
    }

    Rectangle {
        id: frame
        anchors.fill: parent
        radius: Theme.radiusMedium
        color: Theme.surfaceElevated
        border.width: 1
        border.color: textArea.activeFocus ? Theme.borderFocus : (frameHover.hovered ? Theme.borderStrong : Theme.borderSubtle)
        Behavior on border.color { ColorAnimation { duration: Theme.durationFast } }

        HoverHandler { id: frameHover }

        Row {
            id: contentRow
            anchors.fill: parent
            anchors.margins: Theme.spacingSm
            spacing: Theme.spacingSm

            ScrollView {
                width: parent.width - sendButton.width - Theme.spacingSm
                height: parent.height
                clip: true

                TextArea {
                    id: textArea
                    placeholderText: "Converse com o JARVIS..."
                    placeholderTextColor: Theme.textFaint
                    color: Theme.textPrimary
                    font.family: Theme.fontFamily
                    font.pixelSize: 14
                    wrapMode: TextArea.Wrap
                    selectByMouse: true
                    background: null
                    padding: Theme.spacingXs

                    onTextChanged: {
                        if (length > bar.maxLength) {
                            remove(bar.maxLength, length)
                        }
                    }

                    Keys.onPressed: (event) => {
                        if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                                && !(event.modifiers & Qt.ShiftModifier)) {
                            event.accepted = true
                            bar.trySend()
                        }
                    }
                }
            }

            Rectangle {
                id: sendButton
                width: 40
                height: 40
                anchors.verticalCenter: parent.verticalCenter
                radius: Theme.radiusMedium
                color: bar.busy ? Theme.danger : (bar.hasText ? Theme.cyan : Theme.surface)
                Behavior on color { ColorAnimation { duration: Theme.durationFast } }

                Text {
                    anchors.centerIn: parent
                    text: bar.busy ? "■" : "↑"
                    color: (bar.busy || bar.hasText) ? Theme.background : Theme.textFaint
                    font.pixelSize: 15
                }

                MouseArea {
                    id: sendMouseArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: bar.busy ? bar.cancelRequested() : bar.trySend()
                }
                ToolTip.visible: sendMouseArea.containsMouse
                ToolTip.text: bar.busy ? "Cancelar resposta" : "Enviar"
                ToolTip.delay: 500
            }
        }
    }
}
