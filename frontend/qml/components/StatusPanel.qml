import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Faixa discreta de status real do sistema. Nada aqui é inventado — cada
// indicador reflete uma property do Bridge, vinda de JarvisApplication.get_status().
// v0.6: agrupada numa faixa própria (fundo/borda sutis) em vez de texto
// solto sobre o HUD — ajuda a "ancorar" a leitura sem virar um dashboard.
// v0.7: indicador VOICE (push-to-talk pronto/indisponível) + controle
// compacto "OUTPUT ON/OFF" para a fala automática (desligada por padrão).
Item {
    id: panel

    property bool running: false
    property bool memoryAvailable: false
    property bool aiConfigured: false
    property bool aiSessionActive: false
    property string aiBackend: "nenhum"
    property bool voiceAvailable: false
    property bool ttsReady: false
    property bool voiceOutputEnabled: false
    signal voiceOutputToggleRequested()

    implicitHeight: row.implicitHeight + Theme.spacingSm * 2
    implicitWidth: row.implicitWidth + Theme.spacingMd * 2

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusMedium
        color: Theme.surfacePanel
        border.width: 1
        border.color: Theme.borderSubtle
    }

    Row {
        id: row
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: Theme.spacingMd
        spacing: Theme.spacingXl

        StatusIndicator {
            label: "CORE"
            value: panel.running ? "ONLINE" : "OFFLINE"
            tone: panel.running ? "ready" : "muted"
        }
        StatusIndicator {
            label: "MEMORY"
            value: panel.memoryAvailable ? "READY" : "UNAVAILABLE"
            tone: panel.memoryAvailable ? "ready" : "muted"
        }
        StatusIndicator {
            label: "AI"
            value: panel.aiConfigured ? panel.aiBackend.toUpperCase() : "OFFLINE"
            tone: panel.aiConfigured ? "ready" : "muted"
        }
        StatusIndicator {
            label: "SESSION"
            value: panel.aiSessionActive ? "ACTIVE" : "INACTIVE"
            tone: panel.aiSessionActive ? "active" : "muted"
        }
        StatusIndicator {
            label: "VOICE"
            value: panel.voiceAvailable ? "READY" : "UNAVAILABLE"
            tone: panel.voiceAvailable ? "ready" : "muted"
        }

        ActionButton {
            id: voiceOutputToggle
            anchors.verticalCenter: parent.verticalCenter
            visible: panel.ttsReady
            label: "OUTPUT " + (panel.voiceOutputEnabled ? "ON" : "OFF")
            tooltip: panel.voiceOutputEnabled
                ? "Desligar fala automática da resposta"
                : "Ligar fala automática da resposta (lê a resposta do JARVIS em voz alta)"
            emphasis: panel.voiceOutputEnabled
            tint: Theme.blue
            onClicked: panel.voiceOutputToggleRequested()
        }
    }
}
