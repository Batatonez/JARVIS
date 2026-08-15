import QtQuick
import "../theme"

// UNIVERSAL COMMAND BAR (v1.7, navegação de teclado na v1.8) — Ctrl+K.
//
// Por que Ctrl+K e não Ctrl+Space: `Ctrl+Space` já é o push-to-talk do
// microfone desde a v1.3, está documentado no tooltip do MicButton e tem
// memória muscular. Dois handlers disputando o mesmo atalho seria pior que
// qualquer ganho de consistência. `Ctrl+K` é a convenção estabelecida de
// paleta de comandos (VS Code, Slack, Linear, GitHub, Notion).
//
// Este arquivo não decide NADA sobre o que é um comando: manda o texto ao
// Bridge e mostra o que voltar. Não sabe o que é aplicativo ou arquivo, não
// classifica risco e não executa ação — ver app/intents.py, app/actions.py e
// services/system/.
//
// ---------------------------------------------------------------------
// Seleção: UM estado só (v1.8)
// ---------------------------------------------------------------------
// `selectedIndex` é compartilhado por teclado e mouse. Manter dois estados
// (um para Tab, outro para hover) é como se produz o defeito clássico de
// paleta de comandos: o mouse destaca um item, a seta desce a partir de
// outro, e o Enter executa um terceiro.
//
// `-1` = nada selecionado. Nesse estado o Enter executa o TEXTO DIGITADO, e
// o primeiro Tab/seta seleciona o item 0. Isso é o que permite digitar um
// comando completo e apertar Enter sem nunca tocar na lista.
Item {
    id: overlay

    property bool open: false
    property var results: []              // [{section, label, sublabel, id, kind}]
    property string resultText: ""
    property bool resultOk: true
    property string confirmationText: ""  // não-vazio => aguardando confirmação
    property int selectedIndex: -1

    signal submitted(string text)
    signal activated(int index)           // executou um item da lista
    signal queryChanged(string text)
    signal confirmed()
    signal cancelled()
    signal closeRequested()

    readonly property bool hasResults: results.length > 0
    readonly property bool hasSelection: selectedIndex >= 0 && selectedIndex < results.length

    function reset() {
        input.text = ""
        overlay.resultText = ""
        overlay.confirmationText = ""
        overlay.selectedIndex = -1
    }

    function focusInput() {
        input.forceActiveFocus()
    }

    // Wrap-around nas duas direções: numa lista curta de paleta de comandos,
    // parar na ponta faz a pessoa achar que a tecla não funcionou. Descer no
    // último volta ao primeiro, e subir no primeiro vai ao último.
    function selectNext() {
        if (!hasResults) return
        overlay.selectedIndex = (overlay.selectedIndex + 1) % overlay.results.length
    }

    function selectPrevious() {
        if (!hasResults) return
        if (overlay.selectedIndex <= 0)
            overlay.selectedIndex = overlay.results.length - 1
        else
            overlay.selectedIndex -= 1
    }

    function activateSelection() {
        if (overlay.hasSelection)
            overlay.activated(overlay.selectedIndex)
        else if (input.text.trim().length > 0)
            overlay.submitted(input.text)
    }

    // A lista mudou (o usuário digitou mais uma letra): a seleção anterior
    // apontava para um item que pode nem existir mais. Zerar é a política
    // previsível — o próximo Tab começa do topo da lista NOVA. Tentar
    // preservar o item por identidade daria a impressão de seleção
    // "pulando" enquanto se digita.
    onResultsChanged: overlay.selectedIndex = -1

    visible: opacity > 0
    opacity: open ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: Theme.durationFast } }

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0.02, 0.03, 0.05, 0.72)
        MouseArea { anchors.fill: parent; onClicked: overlay.closeRequested() }
    }

    Rectangle {
        id: card
        width: Math.min(620, overlay.width - Theme.spacingXl * 2)
        // Ancorada no topo, não centralizada: uma paleta de comandos aparece
        // onde o olhar já está e não empurra o conteúdo.
        anchors.horizontalCenter: parent.horizontalCenter
        y: Math.min(overlay.height * 0.18, 160)
        height: column.implicitHeight + Theme.spacingLg * 2
        radius: Theme.radiusLarge
        color: Theme.surfaceElevated
        border.width: 1.5
        border.color: Theme.cyan

        scale: overlay.open ? 1 : 0.97
        Behavior on scale { NumberAnimation { duration: Theme.durationFast; easing.type: Easing.OutCubic } }

        MouseArea { anchors.fill: parent }

        Column {
            id: column
            x: Theme.spacingLg
            y: Theme.spacingLg
            width: parent.width - Theme.spacingLg * 2
            spacing: Theme.spacingSm

            AuthField {
                id: input
                width: parent.width
                label: "PERGUNTE OU DIGITE UM COMANDO"
                onTextChanged: overlay.queryChanged(text)
                onAccepted: {
                    if (overlay.confirmationText.length > 0)
                        overlay.confirmed()
                    else
                        overlay.activateSelection()
                }

                // Tab/Shift+Tab são capturados AQUI, no campo de texto, e não
                // no overlay: com o foco no input (que é onde ele sempre
                // está), um handler no pai nunca receberia a tecla — o Qt
                // trata Tab como navegação de foco antes disso.
                //
                // `event.accepted = true` só quando há lista: sem resultados,
                // o Tab volta a ser navegação de foco normal e a tela não
                // vira uma armadilha de foco (item 9).
                Keys.onPressed: (event) => {
                    if (overlay.confirmationText.length > 0)
                        return
                    if (event.key === Qt.Key_Tab) {
                        if (overlay.hasResults) { overlay.selectNext(); event.accepted = true }
                    } else if (event.key === Qt.Key_Backtab) {
                        if (overlay.hasResults) { overlay.selectPrevious(); event.accepted = true }
                    } else if (event.key === Qt.Key_Down) {
                        if (overlay.hasResults) { overlay.selectNext(); event.accepted = true }
                    } else if (event.key === Qt.Key_Up) {
                        if (overlay.hasResults) { overlay.selectPrevious(); event.accepted = true }
                    }
                }
            }

            Text {
                width: parent.width
                visible: overlay.confirmationText.length === 0 && overlay.resultText.length === 0
                    && !overlay.hasResults
                text: "Ex.: abrir Spotify · diminuir volume · quanto é 15% de 250 · tira um print"
                color: Theme.textFaint
                font.family: Theme.fontFamily
                font.pixelSize: 11
                wrapMode: Text.Wrap
            }

            // --- Resultados (aplicativos, arquivos, ações) ---
            Column {
                id: resultList
                width: parent.width
                spacing: 1
                visible: overlay.hasResults && overlay.confirmationText.length === 0

                Repeater {
                    model: overlay.results
                    Rectangle {
                        required property var modelData
                        required property int index

                        width: resultList.width
                        height: row.implicitHeight + 10
                        radius: Theme.radiusSmall
                        // Destaque discreto: fundo levemente ciano e uma
                        // borda fina. Sem glow — o item precisa ser óbvio,
                        // não chamativo.
                        color: index === overlay.selectedIndex
                            ? Qt.rgba(0.20, 0.80, 0.95, 0.14) : "transparent"
                        border.width: index === overlay.selectedIndex ? 1 : 0
                        border.color: Theme.cyan

                        Column {
                            id: row
                            x: Theme.spacingSm
                            anchors.verticalCenter: parent.verticalCenter
                            width: parent.width - Theme.spacingSm * 2
                            spacing: 1

                            Text {
                                width: parent.width
                                text: (modelData.section ? modelData.section + "  ·  " : "") + modelData.label
                                color: index === overlay.selectedIndex ? Theme.textPrimary : Theme.textSecondary
                                font.family: Theme.fontFamily
                                font.pixelSize: 12
                                elide: Text.ElideRight
                            }
                            Text {
                                width: parent.width
                                visible: !!modelData.sublabel
                                text: modelData.sublabel || ""
                                color: Theme.textFaint
                                font.family: Theme.fontFamily
                                font.pixelSize: 10
                                elide: Text.ElideMiddle
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            // Hover escreve no MESMO estado que o teclado lê:
                            // depois de passar o mouse, a próxima seta
                            // continua a partir dali (item 6).
                            onEntered: overlay.selectedIndex = index
                            onClicked: overlay.activated(index)
                        }
                    }
                }
            }

            // --- Confirmação de ação de risco alto ---
            Column {
                width: parent.width
                spacing: Theme.spacingSm
                visible: overlay.confirmationText.length > 0

                Rectangle {
                    width: parent.width
                    height: confirmLabel.implicitHeight + Theme.spacingSm * 2
                    radius: Theme.radiusSmall
                    color: Qt.rgba(1, 0.42, 0.5, 0.10)
                    border.width: 1
                    border.color: Theme.danger
                    Text {
                        id: confirmLabel
                        x: Theme.spacingSm
                        y: Theme.spacingSm
                        width: parent.width - Theme.spacingSm * 2
                        text: overlay.confirmationText + "?\nEsta ação pode fechar um programa com trabalho não salvo."
                        color: Theme.danger
                        font.family: Theme.fontFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                }
                ModalButtonRow {
                    ActionButton {
                        label: "CONFIRMAR"
                        emphasis: true
                        tint: Theme.danger
                        onClicked: overlay.confirmed()
                    }
                    ActionButton {
                        label: "CANCELAR"
                        emphasis: false
                        onClicked: overlay.cancelled()
                    }
                }
            }

            // --- Resultado ---
            Text {
                width: parent.width
                visible: overlay.resultText.length > 0 && overlay.confirmationText.length === 0
                text: overlay.resultText
                color: overlay.resultOk ? Theme.textPrimary : Theme.warning
                font.family: Theme.fontFamily
                font.pixelSize: 12
                wrapMode: Text.Wrap
            }

            // --- Quick actions do último resultado (v1.8) ---
            QuickActionRow {
                width: parent.width
                actions: overlay.quickActions
                onTriggered: (actionId) => overlay.quickActionTriggered(actionId)
            }
        }
    }

    property var quickActions: []
    signal quickActionTriggered(string actionId)

    Keys.onEscapePressed: overlay.closeRequested()
}
