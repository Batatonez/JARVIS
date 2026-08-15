import QtQuick
import "../theme"

// Quick actions genéricas (v1.8).
//
// Renderer ÚNICO para qualquer conjunto de ações contextuais: resultado de
// busca de arquivo, screenshot, aplicativo aberto. A alternativa — um bloco
// de botões escrito à mão por tipo de resposta — significaria que adicionar
// uma ação nova exige mexer em QML em vários lugares, e que dois lugares
// acabam divergindo.
//
// O modelo vem pronto do Bridge (`{id, label, riskLevel, enabled}`) e este
// arquivo só desenha e emite o id de volta. Ele NÃO sabe o que a ação faz,
// não valida risco e não executa nada: quem autoriza continua sendo
// `app/actions.py`, pelo mesmo caminho de sempre. Um botão aqui não é um
// atalho para pular o modelo de risco — clicar em algo perigoso continua
// abrindo a confirmação.
Row {
    id: root

    property var actions: []   // [{id, label, riskLevel, enabled}]
    signal triggered(string actionId)

    spacing: Theme.spacingSm
    visible: actions.length > 0

    Repeater {
        model: root.actions
        ActionButton {
            required property var modelData
            label: modelData.label
            emphasis: false
            // Ação de risco alto ganha o tom de perigo — a cor não é o
            // controle (a confirmação é), mas avisa antes do clique.
            tint: modelData.riskLevel === "dangerous" ? Theme.danger : Theme.blue
            enabled: modelData.enabled === undefined ? true : modelData.enabled
            onClicked: root.triggered(modelData.id)
        }
    }
}
