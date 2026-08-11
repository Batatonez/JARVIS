import QtQuick
import "../theme"

// Faixa discreta de status real do sistema. Nada aqui é inventado — cada
// indicador reflete uma property do Bridge, vinda de JarvisApplication.get_status().
Row {
    id: panel

    property bool running: false
    property bool memoryAvailable: false
    property bool aiConfigured: false
    property bool aiSessionActive: false
    property string aiBackend: "nenhum"

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
}
