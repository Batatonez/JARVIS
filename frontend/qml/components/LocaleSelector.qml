import QtQuick
import QtQuick.Controls.Basic
import "../theme"

// Seletor de uma preferência regional (idioma, região ou moeda) — v1.6.0.
//
// Componente próprio (e não um ComboBox solto em cada campo) porque os três
// seletores compartilham exatamente o mesmo comportamento, incluindo o
// detalhe que importa: quando a escolha é "Automático", o rótulo mostra
// TAMBÉM o que ela resolveu ("Automático — Brasil"). Sem isso, "Automático"
// vira caixa-preta e o usuário não tem como saber se a detecção acertou.
//
// Nada aqui decide nada: `options`, `currentValue` e `resolvedLabel` chegam
// prontos do Bridge, e a seleção sobe como sinal. Este arquivo não sabe o que
// é um locale.
Item {
    id: selector

    property string label: ""
    property var options: []          // [{value, label}]
    property string currentValue: ""
    property string resolvedLabel: "" // preenchido só quando a escolha é automática
    signal selected(string value)

    implicitHeight: column.implicitHeight

    function _indexOfCurrent() {
        for (var i = 0; i < selector.options.length; i++) {
            if (selector.options[i].value === selector.currentValue)
                return i
        }
        return 0
    }

    Column {
        id: column
        width: parent.width
        spacing: 4

        Text {
            text: selector.label
            color: Theme.textFaint
            font.family: Theme.fontFamily
            font.pixelSize: 11
            font.letterSpacing: Theme.letterSpacingLabel
        }

        ComboBox {
            id: combo
            width: parent.width
            model: selector.options
            textRole: "label"
            valueRole: "value"
            currentIndex: selector._indexOfCurrent()

            // Reposiciona quando a preferência muda por fora (ex.: logo após
            // salvar). Sem isto, o seletor continuaria mostrando a escolha
            // anterior mesmo com a nova já aplicada.
            onModelChanged: currentIndex = selector._indexOfCurrent()
            Connections {
                target: selector
                function onCurrentValueChanged() { combo.currentIndex = selector._indexOfCurrent() }
            }

            onActivated: (index) => {
                if (index >= 0 && index < selector.options.length)
                    selector.selected(selector.options[index].value)
            }

            background: Rectangle {
                radius: Theme.radiusSmall
                color: Theme.surfacePanel
                border.width: 1
                border.color: combo.activeFocus ? Theme.blue : Theme.borderSubtle
            }
            contentItem: Text {
                leftPadding: Theme.spacingSm
                rightPadding: combo.indicator.width + Theme.spacingSm
                text: combo.displayText
                color: Theme.textPrimary
                font.family: Theme.fontFamily
                font.pixelSize: 12
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
        }

        Text {
            width: parent.width
            visible: selector.resolvedLabel.length > 0
            text: "→ " + selector.resolvedLabel
            color: Theme.textMuted
            font.family: Theme.fontFamily
            font.pixelSize: 11
            elide: Text.ElideRight
        }
    }
}
