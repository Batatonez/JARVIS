import QtQuick
import "../theme"

// Overlay de instalação do modelo de voz (v0.9) — aberto quando o usuário
// clica no microfone em SETUP_REQUIRED. Nunca baixa nada sozinho: só existe
// para pedir consentimento explícito, mostrar a origem/tamanho/licença
// antes, e acompanhar o progresso real do download.
Item {
    id: overlay

    property bool open: false
    property var modelInfo: null // {name, language, approximateSizeBytes, license, source}
    property bool downloadActive: false
    property var downloadProgress: null // {downloaded, total}
    signal downloadRequested()
    signal cancelRequested()
    signal closeRequested()

    readonly property real _approxMb: modelInfo ? (modelInfo.approximateSizeBytes / 1000000) : 0
    readonly property real _progressFraction: {
        if (!downloadProgress || !downloadProgress.total) return 0
        return Math.min(1, downloadProgress.downloaded / downloadProgress.total)
    }

    visible: opacity > 0
    opacity: open ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Theme.durationNormal } }
    focus: open
    Keys.onEscapePressed: if (!overlay.downloadActive) overlay.closeRequested()

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0.02, 0.03, 0.05, 0.78)
        MouseArea { anchors.fill: parent; onClicked: if (!overlay.downloadActive) overlay.closeRequested() }
    }

    Rectangle {
        id: card
        width: Math.min(440, overlay.width - Theme.spacingXl * 2)
        anchors.centerIn: parent
        height: column.implicitHeight + Theme.spacingLg * 2
        radius: Theme.radiusLarge
        color: Theme.surfaceElevated
        border.width: 1.5
        border.color: Theme.violet
        scale: overlay.open ? 1 : 0.94
        Behavior on scale { NumberAnimation { duration: Theme.durationNormal; easing.type: Easing.OutBack; easing.overshoot: 1.1 } }

        MouseArea { anchors.fill: parent }

        Column {
            id: column
            x: Theme.spacingLg
            y: Theme.spacingLg
            width: card.width - Theme.spacingLg * 2
            spacing: Theme.spacingMd

            Row {
                spacing: Theme.spacingSm
                Rectangle {
                    width: 8; height: 8; radius: 4
                    anchors.verticalCenter: parent.verticalCenter
                    color: Theme.violet
                }
                Text {
                    text: "VOICE INPUT — SETUP REQUIRED"
                    color: Theme.textSecondary
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    font.letterSpacing: Theme.letterSpacingLabel * 1.3
                }
            }

            Text {
                width: parent.width
                text: "O reconhecimento de fala roda 100% offline (Vosk) — precisa de um modelo pequeno instalado uma vez. Nada é enviado à nuvem."
                color: Theme.textMuted
                font.family: Theme.fontFamily
                font.pixelSize: 13
                wrapMode: Text.Wrap
            }

            Column {
                width: parent.width
                spacing: 4
                visible: overlay.modelInfo !== null

                Row {
                    width: parent.width
                    Text { text: "Idioma"; color: Theme.textFaint; font.family: Theme.fontFamily; font.pixelSize: 11 }
                    Item { width: parent.width - 160; height: 1 }
                    Text {
                        text: overlay.modelInfo ? overlay.modelInfo.language : ""
                        color: Theme.textPrimary; font.family: Theme.fontFamily; font.pixelSize: 11
                    }
                }
                Row {
                    width: parent.width
                    Text { text: "Tamanho aprox."; color: Theme.textFaint; font.family: Theme.fontFamily; font.pixelSize: 11 }
                    Item { width: parent.width - 160; height: 1 }
                    Text {
                        text: overlay._approxMb.toFixed(0) + " MB"
                        color: Theme.textPrimary; font.family: Theme.fontFamily; font.pixelSize: 11
                    }
                }
                Row {
                    width: parent.width
                    Text { text: "Licença"; color: Theme.textFaint; font.family: Theme.fontFamily; font.pixelSize: 11 }
                    Item { width: parent.width - 160; height: 1 }
                    Text {
                        text: overlay.modelInfo ? overlay.modelInfo.license : ""
                        color: Theme.textPrimary; font.family: Theme.fontFamily; font.pixelSize: 11
                    }
                }
                Row {
                    width: parent.width
                    Text { text: "Origem"; color: Theme.textFaint; font.family: Theme.fontFamily; font.pixelSize: 11 }
                    Item { width: parent.width - 160; height: 1 }
                    Text {
                        text: overlay.modelInfo ? overlay.modelInfo.source : ""
                        color: Theme.textPrimary; font.family: Theme.fontFamily; font.pixelSize: 11
                        wrapMode: Text.Wrap
                        width: 200
                        horizontalAlignment: Text.AlignRight
                    }
                }
            }

            // --- Progresso (só durante o download) ---
            Column {
                width: parent.width
                spacing: 6
                visible: overlay.downloadActive

                Rectangle {
                    width: parent.width; height: 6; radius: 3
                    color: Theme.surface
                    Rectangle {
                        width: parent.width * overlay._progressFraction
                        height: parent.height
                        radius: 3
                        color: Theme.violet
                        Behavior on width { NumberAnimation { duration: 120 } }
                    }
                }
                Text {
                    text: overlay.downloadProgress && overlay.downloadProgress.total
                        ? (Math.round(overlay.downloadProgress.downloaded / 1000000) + " MB / " + Math.round(overlay.downloadProgress.total / 1000000) + " MB")
                        : "Conectando..."
                    color: Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                }
            }

            Row {
                width: parent.width
                spacing: Theme.spacingSm
                layoutDirection: Qt.RightToLeft

                ActionButton {
                    visible: !overlay.downloadActive
                    label: "BAIXAR MODELO DE VOZ (~" + overlay._approxMb.toFixed(0) + " MB)"
                    emphasis: true
                    tint: Theme.violet
                    onClicked: overlay.downloadRequested()
                }
                ActionButton {
                    visible: overlay.downloadActive
                    label: "CANCELAR"
                    emphasis: false
                    onClicked: overlay.cancelRequested()
                }
                ActionButton {
                    visible: !overlay.downloadActive
                    label: "AGORA NÃO"
                    emphasis: false
                    onClicked: overlay.closeRequested()
                }
            }
        }
    }
}
