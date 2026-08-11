import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Entrada de mensagem: Enter envia, Shift+Enter quebra linha, multiline com
// limite razoável. O botão de enviar vira STOP quando `busy` — cancela em
// vez de mandar outra mensagem (nunca deixa o usuário "spammar" enquanto
// o JARVIS está processando). v0.7: [texto][MIC][SEND] — a transcrição de
// voz cai neste campo de texto, nunca é enviada sozinha (ver MicButton.qml).
Item {
    id: bar

    property bool busy: false
    property int maxLength: 4000
    property string voiceState: "idle" // idle | listening | processing_speech
    // Status real do STT (bridge.sttStatus — ver services/stt_service.py::STTStatus).
    property string sttStatus: "unavailable"
    property string voiceUnavailableReason: "Microfone indisponível"
    property bool speaking: false
    signal sendRequested(string text)
    signal cancelRequested()
    signal micToggleRequested()
    signal stopSpeakingRequested()

    readonly property bool hasText: textArea.text.trim().length > 0
    readonly property bool voiceBusy: bar.voiceState === "listening" || bar.voiceState === "processing_speech"

    implicitHeight: Math.min(168, Math.max(56, contentRow.implicitHeight + Theme.spacingMd * 2))

    function trySend() {
        const text = textArea.text.trim()
        if (text.length === 0 || bar.busy || bar.voiceBusy) return
        bar.sendRequested(text)
        textArea.clear()
    }

    // Chamado pelo Main.qml quando `voice.transcription.completed` chega
    // (via Bridge). Nunca envia sozinho — só preenche o campo para revisão.
    function insertTranscription(text) {
        if (!text) return
        textArea.text = textArea.text.trim().length === 0 ? text : (textArea.text + " " + text)
        textArea.cursorPosition = textArea.text.length
        textArea.forceActiveFocus()
    }

    // Glow de foco extremamente discreto — uma sombra sutil atrás do frame,
    // não um brilho chapado. Só visível com o campo focado.
    Rectangle {
        anchors.fill: parent
        anchors.margins: -3
        radius: Theme.radiusMedium + 3
        color: "transparent"
        border.width: 3
        border.color: Qt.rgba(0.361, 0.882, 0.902, 0.10)
        opacity: textArea.activeFocus ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: Theme.durationNormal } }
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
                width: parent.width - micButton.width - sendButton.width - Theme.spacingSm * 2
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

            MicButton {
                id: micButton
                anchors.verticalCenter: parent.verticalCenter
                voiceState: bar.voiceState
                sttStatus: bar.sttStatus
                unavailableReason: bar.voiceUnavailableReason
                onClicked: bar.micToggleRequested()
            }

            Rectangle {
                id: sendButton
                width: 40
                height: 40
                anchors.verticalCenter: parent.verticalCenter
                radius: Theme.radiusMedium
                color: bar.busy ? Theme.danger
                    : bar.speaking ? Theme.blue
                    : bar.hasText ? (sendMouseArea.containsMouse ? Theme.primaryBright : Theme.cyan)
                    : (sendMouseArea.containsMouse ? Theme.surfaceHover : Theme.surface)
                Behavior on color { ColorAnimation { duration: Theme.durationFast } }

                Text {
                    anchors.centerIn: parent
                    text: (bar.busy || bar.speaking) ? "■" : "↑"
                    color: (bar.busy || bar.speaking || bar.hasText) ? Theme.background : Theme.textFaint
                    font.pixelSize: 15
                }

                MouseArea {
                    id: sendMouseArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: bar.busy ? bar.cancelRequested() : bar.speaking ? bar.stopSpeakingRequested() : bar.trySend()
                }
                ToolTip.visible: sendMouseArea.containsMouse
                ToolTip.text: bar.busy ? "Cancelar resposta" : bar.speaking ? "Parar de falar" : "Enviar"
                ToolTip.delay: 500
            }
        }
    }
}
