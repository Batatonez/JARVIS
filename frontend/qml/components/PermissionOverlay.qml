import QtQuick
import "../theme"

// Overlay de pedido de permissão — fundação preparada desde o v0.4
// (`app/permissions.py`), sem nenhuma ferramenta real conectada ainda.
// A cor depende do nível de risco (READ/ACTION/DANGEROUS).
Item {
    id: overlay

    // { id, action, description, riskLevel } ou null
    property var request: null
    signal approved(string requestId)
    signal denied(string requestId)

    readonly property color riskColor: request ? Theme.riskColor(request.riskLevel) : Theme.cyan
    readonly property bool dangerous: request ? request.riskLevel === "dangerous" : false
    // Bug do v0.5: `request !== null` é uma comparação estrita — um
    // `Property("QVariant")` do Qt que devolve `None` do Python chega aqui
    // como `undefined`, não `null`, e em JS `undefined !== null` é sempre
    // `true`. Resultado: o overlay ficava com opacity 1 (visível, bloqueando
    // clique) mesmo sem nenhum pedido pendente. Checagem "truthy" (`request
    // ? ... : ...`) resolve para os dois casos (null e undefined).
    readonly property bool hasRequest: !!request

    visible: opacity > 0
    opacity: hasRequest ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Theme.durationNormal } }

    focus: hasRequest
    Keys.onEscapePressed: if (overlay.request) overlay.denied(overlay.request.id)

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0.02, 0.03, 0.05, 0.78)

        MouseArea {
            // bloqueia cliques na interface por trás do overlay
            anchors.fill: parent
        }
    }

    // Glow externo — só para DANGEROUS, reforça que essa decisão pesa mais
    // que uma READ/ACTION comum, sem precisar de texto extra.
    Rectangle {
        anchors.centerIn: card
        width: card.width + 16
        height: card.height + 16
        radius: Theme.radiusLarge + 8
        color: "transparent"
        border.width: 8
        border.color: Qt.rgba(1, 0.357, 0.443, 0.08)
        visible: overlay.dangerous
    }

    Rectangle {
        id: card
        width: Math.min(440, overlay.width - Theme.spacingXl * 2)
        anchors.centerIn: parent
        height: column.implicitHeight + Theme.spacingLg * 2
        radius: Theme.radiusLarge
        color: Theme.surfaceElevated
        border.width: overlay.dangerous ? 2 : 1.5
        border.color: overlay.riskColor
        scale: overlay.hasRequest ? 1 : 0.92
        Behavior on border.width { NumberAnimation { duration: Theme.durationNormal } }
        Behavior on scale { NumberAnimation { duration: Theme.durationNormal; easing.type: Easing.OutBack; easing.overshoot: 1.1 } }

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
                    color: overlay.riskColor
                }
                Text {
                    text: "ACTION REQUEST"
                    color: Theme.textSecondary
                    font.family: Theme.fontFamily
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    font.letterSpacing: Theme.letterSpacingLabel * 1.5
                }
            }

            Text {
                width: parent.width
                text: "JARVIS deseja realizar:"
                color: Theme.textMuted
                font.family: Theme.fontFamily
                font.pixelSize: 13
            }

            Text {
                width: parent.width
                text: overlay.request ? overlay.request.description : ""
                color: Theme.textPrimary
                font.family: Theme.fontFamily
                font.pixelSize: 16
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
            }

            Row {
                spacing: Theme.spacingXs
                Text {
                    text: "Risco:"
                    color: Theme.textFaint
                    font.family: Theme.fontFamily
                    font.pixelSize: 12
                }
                Text {
                    text: overlay.request ? overlay.request.riskLevel.toUpperCase() : ""
                    color: overlay.riskColor
                    font.family: Theme.fontFamily
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                    font.letterSpacing: Theme.letterSpacingLabel
                }
            }

            // Flow em vez de Row: botoes quebram linha em vez de vazar do
            // cartao quando nao cabem (ver ModalButtonRow.qml).
            ModalButtonRow {

                ActionButton {
                    label: "PERMITIR"
                    emphasis: true
                    tint: overlay.riskColor
                    onClicked: overlay.approved(overlay.request.id)
                }
                ActionButton {
                    label: "NEGAR"
                    emphasis: false
                    onClicked: overlay.denied(overlay.request.id)
                }
            }
        }
    }
}
