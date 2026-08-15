import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Faixa discreta de status real do sistema. Nada aqui é inventado — cada
// indicador reflete uma property do Bridge, vinda de JarvisApplication.get_status().
// v0.6: agrupada numa faixa própria (fundo/borda sutis) em vez de texto
// solto sobre o HUD — ajuda a "ancorar" a leitura sem virar um dashboard.
// v0.7: indicador VOICE + fala automática. v0.8: prioriza CORE/AI/VOICE em
// janelas estreitas (MEMORY/SESSION viram secundários), fundo "glass", e o
// controle de saída de voz agora é um StatusIndicator clicável — não um
// botão de configurações.
Item {
    id: panel

    property bool running: false
    property bool memoryAvailable: false
    property bool aiConfigured: false
    property bool aiSessionActive: false
    // Estado do JARVIS (`bridge.jarvisState`) — usado só para diferenciar
    // THINKING/ERROR no indicador de IA.
    property string jarvisState: "idle"
    property bool voiceAvailable: false
    property bool ttsReady: false
    property bool voiceOutputEnabled: false
    // v1.5: rota realmente usada na última resposta. Vazia até existir uma
    // resposta — os indicadores abaixo simplesmente não aparecem antes disso,
    // em vez de mostrarem um provider "provável".
    property string aiProvider: ""
    property string aiModel: ""
    property bool aiFallbackUsed: false
    property int aiFallbackCount: 0
    // Breakpoint único (Main.qml: window.width < 1250) — só esconde os
    // indicadores secundários, nunca os três prioritários.
    property bool compact: false
    signal voiceOutputToggleRequested()

    implicitHeight: row.implicitHeight + Theme.spacingSm * 2
    implicitWidth: row.implicitWidth + Theme.spacingMd * 2

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusMedium
        color: Theme.surfaceGlass
        border.width: 1
        border.color: Theme.borderSubtle
    }

    Row {
        id: row
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: Theme.spacingMd
        spacing: panel.compact ? Theme.spacingLg : Theme.spacingXl

        StatusIndicator {
            label: "CORE"
            value: panel.running ? "ONLINE" : "OFFLINE"
            tone: panel.running ? "ready" : "muted"
        }
        // v1.1: o indicador mostra ESTADO, não o nome técnico do provider.
        // Qual provider/modelo está servindo (e se é rota gratuita) continua
        // disponível internamente — em log, diagnóstico e testes, via
        // `bridge.aiBackend` — só não é ruído na tela do usuário final.
        StatusIndicator {
            label: "AI"
            value: !panel.aiConfigured ? "NOT CONFIGURED"
                : panel.jarvisState === "error" ? "ERROR"
                : panel.jarvisState === "thinking" ? "THINKING"
                : "CONFIGURED"
            tone: !panel.aiConfigured ? "muted"
                : panel.jarvisState === "error" ? "danger"
                : panel.jarvisState === "thinking" ? "active"
                : "ready"
        }
        // v1.5: qual provider/modelo serviu a última resposta. Some em janela
        // estreita (é informação de diagnóstico, não prioritária) e só existe
        // depois da primeira resposta real.
        StatusIndicator {
            visible: !panel.compact && panel.aiProvider.length > 0
            label: "ROUTE"
            value: panel.aiModel.length > 0 ? panel.aiModel : panel.aiProvider
            tone: "ready"
            tooltip: panel.aiProvider + (panel.aiModel.length > 0 ? "  ·  " + panel.aiModel : "")
        }
        // Indicador de fallback: aparece SÓ quando um fallback realmente
        // aconteceu. Um indicador permanente marcando "0 fallbacks" seria
        // ruído constante para informar a ausência de um evento raro.
        StatusIndicator {
            visible: panel.aiFallbackUsed
            label: "FALLBACK"
            value: String(panel.aiFallbackCount)
            tone: "active"
            tooltip: "A resposta veio de um provider alternativo: "
                + panel.aiFallbackCount + " tentativa(s) anterior(es) falharam."
        }
        StatusIndicator {
            label: "VOICE"
            value: panel.voiceAvailable ? "READY" : "UNAVAILABLE"
            tone: panel.voiceAvailable ? "ready" : "muted"
        }
        StatusIndicator {
            visible: !panel.compact
            label: "MEMORY"
            value: panel.memoryAvailable ? "READY" : "UNAVAILABLE"
            tone: panel.memoryAvailable ? "ready" : "muted"
        }
        StatusIndicator {
            visible: !panel.compact
            label: "SESSION"
            value: panel.aiSessionActive ? "ACTIVE" : "INACTIVE"
            tone: panel.aiSessionActive ? "active" : "muted"
        }
        StatusIndicator {
            visible: panel.ttsReady
            label: "OUTPUT"
            value: panel.voiceOutputEnabled ? "ON" : "OFF"
            tone: panel.voiceOutputEnabled ? "active" : "muted"
            clickable: true
            tooltip: panel.voiceOutputEnabled
                ? "Desligar fala automática da resposta"
                : "Ligar fala automática da resposta (lê a resposta do JARVIS em voz alta)"
            onClicked: panel.voiceOutputToggleRequested()
        }
    }
}
