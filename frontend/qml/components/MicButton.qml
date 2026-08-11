import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Botão de microfone (push-to-talk). Ícone próprio desenhado só com QML
// (cápsula + haste + base — sem emoji, sem asset externo). Clique liga,
// clique de novo desliga (ver InputBar.qml para o porquê do clique em vez
// de pressionar-e-segurar).
Item {
    id: button

    property string voiceState: "idle" // idle | listening | processing_speech
    // Status real do reconhecimento de fala (services/stt_service.py::STTStatus,
    // via bridge.sttStatus): "ready" | "setup_required" | "no_microphone" | "unavailable".
    // Nunca presume "pronto" — reflete exatamente o que o backend confirmou.
    property string sttStatus: "unavailable"
    // Mensagem específica de por que a voz não está disponível (mic
    // ausente vs. erro ao abrir o dispositivo são coisas diferentes — ver
    // frontend/README.md, seção Voz) — nunca "desaparece misteriosamente".
    property string unavailableReason: "Microfone indisponível"
    signal clicked()

    implicitWidth: 40
    implicitHeight: 40

    readonly property bool listening: voiceState === "listening"
    readonly property bool processing: voiceState === "processing_speech"
    // Cinco estados visuais (v0.9), nessa ordem de precedência: uma captura já
    // em andamento manda mais que o status estático do STT.
    readonly property string micState: button.listening ? "listening"
        : button.processing ? "processing"
        : button.sttStatus === "setup_required" ? "setup_required"
        : button.sttStatus === "ready" ? "ready"
        : "error"
    readonly property bool clickable: button.micState === "setup_required"
        || button.micState === "ready"
        || button.micState === "listening"
    property color tint: button.micState === "error" ? Theme.danger
        : button.micState === "setup_required" ? Theme.warning
        : button.listening ? Theme.violet
        : button.processing ? Theme.blue
        : (mouseArea.containsMouse ? Theme.textPrimary : Theme.textMuted)
    Behavior on tint { ColorAnimation { duration: Theme.durationFast } }

    // Halo externo discreto, só durante LISTENING — dá presença sem virar
    // um flash; some instantaneamente fora desse estado.
    Rectangle {
        anchors.fill: parent
        anchors.margins: -4
        radius: Theme.radiusMedium + 4
        color: "transparent"
        border.width: 4
        border.color: Qt.rgba(0.608, 0.420, 1.0, 0.10)
        visible: button.listening
    }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusMedium
        color: button.listening ? Qt.rgba(0.608, 0.420, 1.0, 0.16) : (mouseArea.containsMouse && button.clickable ? Theme.surfaceHover : "transparent")
        border.width: button.listening ? 1 : 0
        border.color: Theme.violet
        Behavior on color { ColorAnimation { duration: Theme.durationFast } }
    }

    // Selo discreto em SETUP_REQUIRED — sinaliza "precisa de uma ação" sem
    // gritar (não é um erro, é um convite a instalar o modelo de voz).
    Rectangle {
        visible: button.micState === "setup_required"
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.topMargin: 2
        anchors.rightMargin: 2
        width: 7; height: 7; radius: 3.5
        color: Theme.warning
        border.width: 1
        border.color: Theme.background
    }

    Item {
        id: glyph
        anchors.centerIn: parent
        width: 12; height: 16
        opacity: button.micState === "error" ? 0.45 : 1

        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            y: 0
            width: 9; height: 12
            radius: 4.5
            color: "transparent"
            border.width: 1.4
            border.color: button.tint
        }
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            y: 12.5; width: 1.4; height: 3
            color: button.tint
        }
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            y: 15; width: 9; height: 1.4; radius: 0.7
            color: button.tint
        }

        // Pulso sutil enquanto grava — discreto, não um flash.
        SequentialAnimation on scale {
            loops: Animation.Infinite
            running: button.listening
            NumberAnimation { to: 1.12; duration: 480; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: 480; easing.type: Easing.InOutSine }
        }
    }

    // Três pontos discretos enquanto transcreve (PROCESSING_SPEECH).
    Row {
        visible: button.processing
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.bottom
        anchors.topMargin: 2
        spacing: 2
        Repeater {
            model: 3
            delegate: Rectangle {
                required property int index
                width: 3; height: 3; radius: 1.5
                color: Theme.blue
                SequentialAnimation on opacity {
                    loops: Animation.Infinite
                    PauseAnimation { duration: index * 140 }
                    NumberAnimation { to: 1.0; duration: 380; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 0.25; duration: 380; easing.type: Easing.InOutSine }
                }
            }
        }
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        enabled: button.clickable
        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: button.clicked()
    }

    ToolTip.visible: mouseArea.containsMouse
    ToolTip.delay: 500
    ToolTip.text: button.micState === "setup_required" ? "Configurar reconhecimento de voz"
        : button.micState === "error" ? button.unavailableReason
        : button.micState === "listening" ? "Parar gravação (Ctrl+Space)"
        : button.micState === "processing" ? "Transcrevendo..."
        : "Gravar (Ctrl+Space)"
}
