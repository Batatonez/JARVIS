import QtQuick
import "../theme"

// UNIVERSAL COMMAND BAR (v1.7) — Ctrl+K.
//
// Por que Ctrl+K e não Ctrl+Space: `Ctrl+Space` já é o push-to-talk do
// microfone desde a v1.3, está documentado no tooltip do MicButton e tem
// memória muscular. Dois handlers disputando o mesmo atalho seria pior que
// qualquer ganho de consistência. `Ctrl+K` é a convenção estabelecida de
// paleta de comandos (VS Code, Slack, Linear, GitHub, Notion), então não há
// nada novo a aprender.
//
// Este arquivo não decide NADA: manda o texto ao Bridge e mostra o que
// voltar. Não sabe o que é um aplicativo, não classifica risco e não executa
// ação — ver app/intents.py, app/actions.py e services/system/.
Item {
    id: overlay

    property bool open: false
    property var suggestions: []          // [{name, source}]
    property string resultText: ""
    property bool resultOk: true
    property string confirmationText: ""  // não-vazio => aguardando confirmação

    signal submitted(string text)
    signal textChanged(string text)
    signal confirmed()
    signal cancelled()
    signal closeRequested()

    function reset() {
        input.text = ""
        overlay.resultText = ""
        overlay.confirmationText = ""
    }

    function focusInput() {
        input.forceActiveFocus()
    }

    visible: opacity > 0
    opacity: open ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Theme.durationFast } }

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0.02, 0.03, 0.05, 0.72)
        MouseArea { anchors.fill: parent; onClicked: overlay.closeRequested() }
    }

    Rectangle {
        id: card
        width: Math.min(620, overlay.width - Theme.spacingXl * 2)
        // Ancorada no topo, não centralizada: uma paleta de comandos aparece
        // onde o olhar já está e não empurra o conteúdo.
        anchors.horizontalCenter: parent.horizontalCenter
        y: Math.min(overlay.height * 0.18, 160)
        height: column.implicitHeight + Theme.spacingLg * 2
        radius: Theme.radiusLarge
        color: Theme.surfaceElevated
        border.width: 1.5
        border.color: Theme.cyan

        scale: overlay.open ? 1 : 0.97
        Behavior on scale { NumberAnimation { duration: Theme.durationFast; easing.type: Easing.OutCubic } }

        MouseArea { anchors.fill: parent }

        Column {
            id: column
            x: Theme.spacingLg
            y: Theme.spacingLg
            width: parent.width - Theme.spacingLg * 2
            spacing: Theme.spacingSm

            AuthField {
                id: input
                width: parent.width
                label: "PERGUNTE OU DIGITE UM COMANDO"
                onTextChanged: overlay.textChanged(text)
                onAccepted: {
                    if (overlay.confirmationText.length > 0)
                        overlay.confirmed()
                    else if (text.trim().length > 0)
                        overlay.submitted(text)
                }
            }

            Text {
                width: parent.width
                visible: overlay.confirmationText.length === 0 && overlay.resultText.length === 0
                    && overlay.suggestions.length === 0
                text: "Ex.: abrir Spotify · diminuir volume · quanto é 15% de 250 · tira um print"
                color: Theme.textFaint
                font.family: Theme.fontFamily
                font.pixelSize: 11
                wrapMode: Text.Wrap
            }

            // --- Sugestões de aplicativo ---
            Column {
                width: parent.width
                spacing: 2
                visible: overlay.suggestions.length > 0 && overlay.confirmationText.length === 0

                Repeater {
                    model: overlay.suggestions
                    Rectangle {
                        required property var modelData
                        required property int index
                        width: column.width
                        height: 30
                        radius: Theme.radiusSmall
                        color: index === 0 ? Theme.surfacePanel : "transparent"
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            x: Theme.spacingSm
                            text: modelData.name
                            color: index === 0 ? Theme.textPrimary : Theme.textMuted
                            font.family: Theme.fontFamily
                            font.pixelSize: 12
                            elide: Text.ElideRight
                        }
                    }
                }
            }

            // --- Confirmação de ação de risco alto ---
            Column {
                width: parent.width
                spacing: Theme.spacingSm
                visible: overlay.confirmationText.length > 0

                Rectangle {
                    width: parent.width
                    height: confirmLabel.implicitHeight + Theme.spacingSm * 2
                    radius: Theme.radiusSmall
                    color: Qt.rgba(1, 0.42, 0.5, 0.10)
                    border.width: 1
                    border.color: Theme.danger
                    Text {
                        id: confirmLabel
                        x: Theme.spacingSm
                        y: Theme.spacingSm
                        width: parent.width - Theme.spacingSm * 2
                        text: overlay.confirmationText + "?\nEsta ação pode fechar um programa com trabalho não salvo."
                        color: Theme.danger
                        font.family: Theme.fontFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                }
                ModalButtonRow {
                    ActionButton {
                        label: "CONFIRMAR"
                        emphasis: true
                        tint: Theme.danger
                        onClicked: overlay.confirmed()
                    }
                    ActionButton {
                        label: "CANCELAR"
                        emphasis: false
                        onClicked: overlay.cancelled()
                    }
                }
            }

            // --- Resultado ---
            Text {
                width: parent.width
                visible: overlay.resultText.length > 0 && overlay.confirmationText.length === 0
                text: overlay.resultText
                color: overlay.resultOk ? Theme.textPrimary : Theme.warning
                font.family: Theme.fontFamily
                font.pixelSize: 12
                wrapMode: Text.Wrap
            }
        }
    }

    Keys.onEscapePressed: overlay.closeRequested()
}
