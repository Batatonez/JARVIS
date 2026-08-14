import QtQuick
import "../theme"

// RECOVERY CODES (v1.3, itens 49-50) — mostrados UMA vez.
//
// O backend só guarda o hash de cada código, então esta tela é literalmente a
// única oportunidade de anotá-los. Por isso o botão de fechar exige a
// confirmação de que foram salvos, e fechar limpa a lista da RAM do Bridge
// (`clearRecoveryCodes`).
Item {
    id: overlay

    property bool open: false
    property var codes: []

    signal closeRequested()

    property bool _acknowledged: false
    property bool _copied: false

    onOpenChanged: if (open) { _acknowledged = false; _copied = false }

    visible: opacity > 0
    opacity: open ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Theme.durationNormal } }
    focus: open
    // Sem Escape de propósito: fechar sem querer aqui perde os códigos.

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0.02, 0.03, 0.05, 0.86)
        MouseArea { anchors.fill: parent }  // bloqueia clique fora
    }

    Rectangle {
        id: card
        width: Math.min(420, overlay.width - Theme.spacingXl * 2)
        height: Math.min(column.implicitHeight + Theme.spacingLg * 2, overlay.height - Theme.spacingXl * 2)
        anchors.centerIn: parent
        radius: Theme.radiusLarge
        color: Theme.surfaceElevated
        border.width: 1.5
        border.color: Theme.success
        scale: overlay.open ? 1 : 0.94
        Behavior on scale { NumberAnimation { duration: Theme.durationNormal; easing.type: Easing.OutBack; easing.overshoot: 1.1 } }

        MouseArea { anchors.fill: parent }

        Flickable {
            anchors.fill: parent
            anchors.margins: Theme.spacingLg
            contentHeight: column.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds

            Column {
                id: column
                width: parent.width
                spacing: Theme.spacingMd

                Row {
                    spacing: Theme.spacingSm
                    Rectangle {
                        width: 8; height: 8; radius: 4
                        anchors.verticalCenter: parent.verticalCenter
                        color: Theme.success
                    }
                    Text {
                        text: "RECOVERY CODES"
                        color: Theme.textSecondary
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                        font.letterSpacing: Theme.letterSpacingLabel * 1.3
                    }
                }

                Text {
                    width: parent.width
                    text: "Guarde estes códigos num lugar seguro. Cada um funciona UMA vez e serve "
                        + "para entrar se você perder o autenticador. Esta é a única vez que eles aparecem."
                    color: Theme.textMuted
                    font.family: Theme.fontFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }

                Rectangle {
                    width: parent.width
                    height: codesText.implicitHeight + Theme.spacingMd * 2
                    radius: Theme.radiusSmall
                    color: Theme.surface
                    border.width: 1
                    border.color: Theme.borderSubtle

                    TextEdit {
                        id: codesText
                        x: Theme.spacingMd
                        y: Theme.spacingMd
                        width: parent.width - Theme.spacingMd * 2
                        text: overlay.codes.join("\n")
                        color: Theme.textPrimary
                        font.family: Theme.fontFamily
                        font.pixelSize: 14
                        font.letterSpacing: 1.5
                        readOnly: true
                        selectByMouse: true
                        horizontalAlignment: Text.AlignHCenter
                    }
                }

                Row {
                    width: parent.width
                    spacing: Theme.spacingSm
                    Rectangle {
                        id: checkbox
                        width: 18; height: 18; radius: 4
                        anchors.verticalCenter: parent.verticalCenter
                        color: overlay._acknowledged ? Theme.success : "transparent"
                        border.width: 1
                        border.color: overlay._acknowledged ? Theme.success : Theme.borderStrong
                        Text {
                            anchors.centerIn: parent
                            visible: overlay._acknowledged
                            text: "✓"
                            color: Theme.background
                            font.pixelSize: 12
                            font.weight: Font.Bold
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: overlay._acknowledged = !overlay._acknowledged
                        }
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        width: column.width - checkbox.width - Theme.spacingSm
                        text: "Salvei meus códigos de recuperação"
                        color: Theme.textPrimary
                        font.family: Theme.fontFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: overlay._acknowledged = !overlay._acknowledged
                        }
                    }
                }

                ModalButtonRow {
                    ActionButton {
                        label: overlay._copied ? "COPIADO ✓" : "COPIAR CÓDIGOS"
                        emphasis: false
                        onClicked: {
                            codesText.selectAll()
                            codesText.copy()
                            codesText.deselect()
                            overlay._copied = true
                        }
                    }
                    ActionButton {
                        label: "FECHAR"
                        emphasis: true
                        tint: Theme.success
                        enabled: overlay._acknowledged
                        onClicked: overlay.closeRequested()
                    }
                }
            }
        }
    }
}
