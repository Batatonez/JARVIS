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
    property bool available: true
    signal clicked()

    implicitWidth: 40
    implicitHeight: 40

    readonly property bool listening: voiceState === "listening"
    readonly property bool processing: voiceState === "processing_speech"
    property color tint: !button.available ? Theme.textFaint
        : button.listening ? Theme.violet
        : button.processing ? Theme.blue
        : (mouseArea.containsMouse ? Theme.textPrimary : Theme.textMuted)
    Behavior on tint { ColorAnimation { duration: Theme.durationFast } }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusMedium
        color: button.listening ? Qt.rgba(0.608, 0.420, 1.0, 0.16) : "transparent"
        border.width: button.listening ? 1 : 0
        border.color: Theme.violet
        Behavior on color { ColorAnimation { duration: Theme.durationFast } }
    }

    Item {
        id: glyph
        anchors.centerIn: parent
        width: 12; height: 16
        opacity: button.available ? 1 : 0.4

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
        enabled: button.available && !button.processing
        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: button.clicked()
    }

    ToolTip.visible: mouseArea.containsMouse
    ToolTip.delay: 500
    ToolTip.text: !button.available ? "Microfone indisponível"
        : button.listening ? "Parar gravação (Ctrl+Space)"
        : button.processing ? "Transcrevendo..."
        : "Gravar (Ctrl+Space)"
}
