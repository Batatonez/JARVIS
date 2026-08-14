import QtQuick
import "../theme"

// Uma mensagem do chat: bloco discreto com linha lateral, não um balão
// grande estilo WhatsApp. JARVIS usa accent ciano; usuário usa um tom mais
// neutro. Texto é selecionável/copiável.
Item {
    id: item

    required property bool isUser
    // RAW: exatamente o que está no banco. É o que o Copy entrega.
    required property string content
    // Mesmo texto, já sanitizado no Python para renderização segura
    // (ver services/markdown_safety.py). NUNCA usar `content` aqui: o
    // renderizador de Rich Text do Qt interpreta HTML embutido.
    required property string markdown
    required property string timestamp
    required property string messageId

    signal copyRequested(string rawText)
    signal regenerateRequested(string messageId)

    // Feedback do Copy — some sozinho (ver copiedTimer).
    property bool _copied: false

    implicitWidth: parent ? parent.width : 400
    implicitHeight: column.implicitHeight + Theme.spacingMd

    readonly property color accent: isUser ? Theme.blue : Theme.cyan

    Rectangle {
        id: rail
        x: 0
        y: 2
        width: 2
        height: column.implicitHeight - 4
        radius: 1
        color: item.accent
        opacity: 0.55
    }

    Column {
        id: column
        x: Theme.spacingMd
        width: parent.width - Theme.spacingMd - Theme.spacingSm
        spacing: 5

        Item {
            width: parent.width
            height: roleLabel.implicitHeight

            Text {
                id: roleLabel
                anchors.left: parent.left
                text: item.isUser ? "YOU" : "JARVIS"
                color: item.accent
                font.family: Theme.fontFamily
                font.pixelSize: 11
                font.weight: Font.DemiBold
                font.letterSpacing: Theme.letterSpacingLabel
            }
            Text {
                anchors.right: parent.right
                anchors.verticalCenter: roleLabel.verticalCenter
                text: item.timestamp
                color: Theme.textFaint
                font.family: Theme.fontFamily
                font.pixelSize: 11
            }
        }

        TextEdit {
            width: column.width
            // Markdown renderizado (headings, negrito, listas, código,
            // blockquote, tabelas, regras horizontais) — o Qt faz isso
            // nativamente. O texto já vem sanitizado do Python.
            textFormat: TextEdit.MarkdownText
            text: item.markdown
            color: Theme.textPrimary
            font.family: Theme.fontFamily
            font.pixelSize: 14
            wrapMode: Text.Wrap
            readOnly: true
            selectByMouse: true
            selectionColor: Qt.rgba(0.361, 0.882, 0.902, 0.35)
            persistentSelection: true
            cursorVisible: false
            // Links são renderizados com estilo, mas NÃO são clicáveis: sem
            // `onLinkActivated`, clicar não faz nada. Abrir URL vinda da IA
            // com um clique transformaria o chat num vetor de phishing — e
            // o escopo da v1.2 diz explicitamente para não criar um
            // mini-navegador aqui.
        }

        // --- Ações (v1.2): discretas, ganham presença no hover ---
        Row {
            id: actions
            spacing: Theme.spacingSm
            height: 18
            opacity: hoverHandler.hovered || item._copied ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.durationFast } }

            MessageActionButton {
                glyph: item._copied ? "✓" : "⧉"
                tooltip: item._copied ? "Copiado" : "Copiar mensagem"
                highlighted: item._copied
                // Passa o RAW (`content`), nunca o markdown renderizado —
                // e nunca "YOU"/"JARVIS"/horário.
                onClicked: {
                    item.copyRequested(item.content)
                    item._copied = true
                    copiedTimer.restart()
                }
            }
            MessageActionButton {
                // Só faz sentido numa resposta do JARVIS.
                visible: !item.isUser
                glyph: "↻"
                tooltip: "Gerar outra resposta"
                onClicked: item.regenerateRequested(item.messageId)
            }
        }

        Timer {
            id: copiedTimer
            interval: 1600
            onTriggered: item._copied = false
        }
    }

    HoverHandler { id: hoverHandler }
}
