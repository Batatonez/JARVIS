import QtQuick
import "../theme"

// Painel de conta — modal simples (não uma tela gigante de configurações).
// Mostra nome, username, plano e o botão de logout. Segue o mesmo padrão
// visual de PermissionOverlay (dim + card), reaproveitado por consistência.
Item {
    id: overlay

    property bool open: false
    property var user: null // {id, username, displayName, plan}
    signal closeRequested()
    signal logoutRequested()

    visible: opacity > 0
    opacity: open ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Theme.durationNormal } }
    focus: open
    Keys.onEscapePressed: overlay.closeRequested()

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0.02, 0.03, 0.05, 0.72)
        MouseArea { anchors.fill: parent; onClicked: overlay.closeRequested() }
    }

    Rectangle {
        id: card
        width: Math.min(360, overlay.width - Theme.spacingXl * 2)
        anchors.centerIn: parent
        height: column.implicitHeight + Theme.spacingLg * 2
        radius: Theme.radiusLarge
        color: Theme.surfaceElevated
        border.width: 1
        border.color: Theme.borderStrong
        scale: overlay.open ? 1 : 0.94
        Behavior on scale { NumberAnimation { duration: Theme.durationNormal; easing.type: Easing.OutBack; easing.overshoot: 1.1 } }

        // Impede que o clique no card feche o painel (o fundo é que fecha).
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
                    width: 36; height: 36; radius: 18
                    anchors.verticalCenter: parent.verticalCenter
                    color: Theme.surface
                    border.width: 1
                    border.color: Theme.cyan
                    Text {
                        anchors.centerIn: parent
                        text: overlay.user ? overlay.user.displayName.charAt(0).toUpperCase() : "?"
                        color: Theme.cyan
                        font.family: Theme.fontFamily
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                    }
                }
                Column {
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 1
                    Text {
                        text: overlay.user ? overlay.user.displayName : ""
                        color: Theme.textPrimary
                        font.family: Theme.fontFamily
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                    }
                    Text {
                        text: overlay.user ? ("@" + overlay.user.username) : ""
                        color: Theme.textFaint
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                    }
                }
            }

            Rectangle { width: parent.width; height: 1; color: Theme.borderSubtle }

            Row {
                width: parent.width
                Text {
                    text: "PLANO"
                    color: Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    font.letterSpacing: Theme.letterSpacingLabel
                }
                Item { width: parent.width - 140; height: 1 }
                Text {
                    text: overlay.user ? overlay.user.plan.toUpperCase() : ""
                    color: Theme.cyan
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    font.letterSpacing: Theme.letterSpacingLabel
                }
            }

            Text {
                width: parent.width
                text: "Cobrança e recursos PRO ainda não existem — planejado para uma versão futura."
                color: Theme.textFaint
                font.family: Theme.fontFamily
                font.pixelSize: 10
                wrapMode: Text.Wrap
            }

            Row {
                width: parent.width
                spacing: Theme.spacingSm
                layoutDirection: Qt.RightToLeft

                ActionButton {
                    label: "SAIR"
                    emphasis: false
                    tint: Theme.danger
                    onClicked: overlay.logoutRequested()
                }
                ActionButton {
                    label: "FECHAR"
                    emphasis: false
                    onClicked: overlay.closeRequested()
                }
            }
        }
    }
}
