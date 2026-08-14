import QtQuick
import "../theme"

// 2FA SETUP (v1.3, item 47) — QR Code + chave textual + confirmação.
//
// O QR é desenhado aqui a partir da MATRIZ booleana que o backend calcula
// (`services/totp.py::qr_matrix`). Desenhar em vez de exibir uma imagem
// evita depender de PIL/QtSvg e, principalmente, evita gravar o segredo num
// arquivo de imagem temporário em disco.
//
// A chave textual existe como fallback obrigatório: nem todo mundo consegue
// escanear (autenticador no mesmo aparelho, câmera ruim, leitor offline).
Item {
    id: overlay

    property bool open: false
    property var enrollment: null   // {secret, qr: [[bool]], secretProtected}
    property string errorText: ""

    signal confirmRequested(string code)
    signal cancelRequested()

    readonly property int _qrSize: (enrollment && enrollment.qr) ? enrollment.qr.length : 0

    visible: opacity > 0
    opacity: open ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Theme.durationNormal } }
    focus: open
    Keys.onEscapePressed: overlay.cancelRequested()

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0.02, 0.03, 0.05, 0.82)
        MouseArea { anchors.fill: parent; onClicked: overlay.cancelRequested() }
    }

    Rectangle {
        id: card
        width: Math.min(420, overlay.width - Theme.spacingXl * 2)
        height: Math.min(column.implicitHeight + Theme.spacingLg * 2, overlay.height - Theme.spacingXl * 2)
        anchors.centerIn: parent
        radius: Theme.radiusLarge
        color: Theme.surfaceElevated
        border.width: 1.5
        border.color: Theme.violet
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
                        color: Theme.violet
                    }
                    Text {
                        text: "TWO-FACTOR AUTHENTICATION"
                        color: Theme.textSecondary
                        font.family: Theme.fontFamily
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                        font.letterSpacing: Theme.letterSpacingLabel * 1.3
                    }
                }

                Text {
                    width: parent.width
                    text: "Escaneie o código no seu aplicativo autenticador (Google Authenticator, "
                        + "Authy, 1Password...) e digite o código de 6 dígitos que ele mostrar."
                    color: Theme.textMuted
                    font.family: Theme.fontFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }

                // --- QR Code desenhado a partir da matriz ---
                Rectangle {
                    id: qrFrame
                    anchors.horizontalCenter: parent.horizontalCenter
                    visible: overlay._qrSize > 0
                    width: Math.min(220, column.width)
                    height: width
                    color: "white"   // QR precisa de fundo claro para ser lido
                    radius: Theme.radiusSmall

                    readonly property real cell: overlay._qrSize > 0 ? width / overlay._qrSize : 0

                    Repeater {
                        model: overlay._qrSize
                        Repeater {
                            required property int index
                            readonly property int rowIndex: index
                            model: overlay._qrSize
                            Rectangle {
                                required property int index
                                x: index * qrFrame.cell
                                y: rowIndex * qrFrame.cell
                                width: qrFrame.cell
                                height: qrFrame.cell
                                color: "black"
                                visible: overlay.enrollment
                                    && overlay.enrollment.qr[rowIndex][index]
                            }
                        }
                    }
                }

                Text {
                    width: parent.width
                    visible: overlay._qrSize === 0
                    text: "Não foi possível gerar o QR Code neste ambiente — use a chave abaixo."
                    color: Theme.warning
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                }

                // --- Chave textual (fallback obrigatório) ---
                Text {
                    text: "OU DIGITE ESTA CHAVE"
                    color: Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: 10
                    font.letterSpacing: Theme.letterSpacingLabel
                }
                Rectangle {
                    width: parent.width
                    height: secretText.implicitHeight + Theme.spacingSm * 2
                    radius: Theme.radiusSmall
                    color: Theme.surface
                    border.width: 1
                    border.color: Theme.borderSubtle
                    TextEdit {
                        id: secretText
                        x: Theme.spacingSm
                        y: Theme.spacingSm
                        width: parent.width - Theme.spacingSm * 2
                        text: overlay.enrollment ? overlay.enrollment.secret : ""
                        color: Theme.textPrimary
                        font.family: Theme.fontFamily
                        font.pixelSize: 13
                        font.letterSpacing: 1.5
                        readOnly: true
                        selectByMouse: true
                        wrapMode: Text.WrapAnywhere
                    }
                }

                AuthField {
                    id: codeField
                    width: parent.width
                    label: "CÓDIGO DE 6 DÍGITOS"
                    onAccepted: overlay.confirmRequested(text)
                }

                Text {
                    width: parent.width
                    visible: overlay.errorText.length > 0
                    text: overlay.errorText
                    color: Theme.danger
                    font.family: Theme.fontFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }

                Text {
                    width: parent.width
                    text: "A verificação em duas etapas só é ativada depois que este código for aceito."
                    color: Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                }

                ModalButtonRow {
                    ActionButton {
                        label: "ATIVAR 2FA"
                        emphasis: true
                        tint: Theme.violet
                        onClicked: overlay.confirmRequested(codeField.text)
                    }
                    ActionButton {
                        label: "CANCELAR"
                        emphasis: false
                        onClicked: overlay.cancelRequested()
                    }
                }
            }
        }
    }
}
