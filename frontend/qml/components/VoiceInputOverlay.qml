import QtQuick
import QtQuick.Controls
import "../theme"

// VOICE INPUT (v1.3, itens 12-17) — escolher microfone, ver o nível de
// entrada e testar o reconhecimento.
//
// Diferente de `VoiceSetupOverlay.qml`, que existe só para instalar o modelo:
// este é o painel de configuração de voz do dia a dia.
//
// Nada aqui decide nada: o QML mostra `audioDevices`, `sttEngine` e
// `voiceLevel` que o Bridge publica, e manda de volta a escolha do usuário.
// Quem resolve dispositivo, persiste preferência e transcreve é o backend.
Item {
    id: overlay

    property bool open: false
    property var devices: []            // [{key, label, hostApi, isSystemDefault, isCurrent}]
    property string selectedKey: ""
    property bool fellBack: false
    property string engineName: "—"
    property string sttStatus: "unavailable"
    property real level: 0.0
    property bool testActive: false
    property string heardText: ""

    signal deviceSelected(string key)
    signal refreshRequested()
    signal testRequested()
    signal closeRequested()

    readonly property string _statusLabel: {
        if (sttStatus === "ready") return engineName.toUpperCase() + " — READY"
        if (sttStatus === "setup_required") return "SETUP REQUIRED"
        if (sttStatus === "no_microphone") return "NO MICROPHONE"
        return "UNAVAILABLE"
    }
    readonly property color _statusColor: sttStatus === "ready" ? Theme.cyan : Theme.textFaint

    visible: opacity > 0
    opacity: open ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Theme.durationNormal } }
    focus: open
    Keys.onEscapePressed: if (!overlay.testActive) overlay.closeRequested()

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0.02, 0.03, 0.05, 0.78)
        MouseArea { anchors.fill: parent; onClicked: if (!overlay.testActive) overlay.closeRequested() }
    }

    Rectangle {
        id: card
        width: Math.min(460, overlay.width - Theme.spacingXl * 2)
        anchors.centerIn: parent
        height: column.implicitHeight + Theme.spacingLg * 2
        radius: Theme.radiusLarge
        color: Theme.surfaceElevated
        border.width: 1.5
        border.color: Theme.cyan
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
                    color: Theme.cyan
                }
                Text {
                    text: "VOICE INPUT"
                    color: Theme.textSecondary
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    font.letterSpacing: Theme.letterSpacingLabel * 1.3
                }
            }

            // --- Microfone ---
            Text {
                text: "Microphone"
                color: Theme.textFaint
                font.family: Theme.fontFamily
                font.pixelSize: 11
            }

            ComboBox {
                id: deviceBox
                width: parent.width
                model: overlay.devices
                textRole: "label"
                valueRole: "key"
                enabled: !overlay.testActive && overlay.devices.length > 0
                font.family: Theme.fontFamily
                font.pixelSize: 12

                // `currentIndex` derivado do backend, nunca guardado aqui: a
                // fonte da verdade é `selectedKey`, que o Bridge publica.
                currentIndex: {
                    for (var i = 0; i < overlay.devices.length; i++) {
                        if (overlay.devices[i].key === overlay.selectedKey) return i
                    }
                    return -1
                }
                onActivated: (index) => {
                    if (index >= 0 && index < overlay.devices.length) {
                        overlay.deviceSelected(overlay.devices[index].key)
                    }
                }

                contentItem: Text {
                    leftPadding: Theme.spacingSm
                    rightPadding: deviceBox.indicator.width + Theme.spacingSm
                    text: deviceBox.displayText || (overlay.devices.length ? "Selecione" : "Nenhum microfone")
                    color: Theme.textPrimary
                    font: deviceBox.font
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }
                background: Rectangle {
                    implicitHeight: 34
                    radius: Theme.radiusSmall
                    color: Theme.surface
                    border.width: 1
                    border.color: deviceBox.activeFocus ? Theme.cyan : Theme.borderSubtle
                }
            }

            Text {
                width: parent.width
                visible: overlay.fellBack
                text: "O microfone salvo não está disponível agora — usando o padrão do sistema."
                color: Theme.warning
                font.family: Theme.fontFamily
                font.pixelSize: 11
                wrapMode: Text.Wrap
            }

            // --- Nível de entrada (item 17) ---
            Text {
                text: "Input Level"
                color: Theme.textFaint
                font.family: Theme.fontFamily
                font.pixelSize: 11
            }

            // Barra segmentada: 20 blocos acesos proporcionalmente ao nível.
            // Sem timer e sem polling — cada atualização vem do sinal
            // `voiceLevelChanged`, que o provider já emite throttled.
            Row {
                width: parent.width
                spacing: 3
                Repeater {
                    model: 20
                    Rectangle {
                        width: (column.width - 19 * 3) / 20
                        height: 10
                        radius: 2
                        readonly property bool lit: overlay.level * 20 > index
                        color: lit
                            ? (index > 16 ? Theme.warning : Theme.cyan)
                            : Theme.surface
                        opacity: lit ? 1 : 0.5
                        Behavior on opacity { NumberAnimation { duration: 80 } }
                    }
                }
            }

            // --- Engine ---
            Item {
                width: parent.width
                implicitHeight: Math.max(engineLabel.implicitHeight, engineValue.implicitHeight)
                Text {
                    id: engineLabel
                    anchors.left: parent.left
                    text: "Speech Recognition"
                    color: Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                }
                Text {
                    id: engineValue
                    anchors.right: parent.right
                    anchors.left: engineLabel.right
                    anchors.leftMargin: Theme.spacingSm
                    text: overlay._statusLabel
                    color: overlay._statusColor
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignRight
                    elide: Text.ElideRight
                }
            }

            // --- Resultado do teste (item 16) ---
            Column {
                width: parent.width
                spacing: 4
                visible: overlay.testActive || overlay.heardText.length > 0

                Text {
                    text: overlay.testActive ? "OUVINDO..." : "HEARD:"
                    color: overlay.testActive ? Theme.cyan : Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    font.letterSpacing: Theme.letterSpacingLabel
                }
                Text {
                    width: parent.width
                    visible: !overlay.testActive
                    // Este texto NUNCA é enviado à IA nem salvo como mensagem
                    // — é só a devolutiva do teste de microfone.
                    text: overlay.heardText.length > 0 ? "“" + overlay.heardText + "”" : "(nada reconhecido)"
                    color: overlay.heardText.length > 0 ? Theme.textPrimary : Theme.textMuted
                    font.family: Theme.fontFamily
                    font.pixelSize: 13
                    wrapMode: Text.Wrap
                }
            }

            ModalButtonRow {
                ActionButton {
                    label: overlay.testActive ? "OUVINDO..." : "TEST MICROPHONE"
                    emphasis: true
                    tint: Theme.cyan
                    enabled: !overlay.testActive && overlay.sttStatus === "ready"
                    onClicked: overlay.testRequested()
                }
                ActionButton {
                    label: "REFRESH DEVICES"
                    emphasis: false
                    enabled: !overlay.testActive
                    onClicked: overlay.refreshRequested()
                }
                ActionButton {
                    label: "FECHAR"
                    emphasis: false
                    enabled: !overlay.testActive
                    onClicked: overlay.closeRequested()
                }
            }
        }
    }
}
