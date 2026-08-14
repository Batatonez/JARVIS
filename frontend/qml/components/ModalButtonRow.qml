import QtQuick
import "../theme"

// Linha de botões de um modal — usada por TODOS os overlays (e-mail, voz,
// conta, permissão) para que a correção viva num lugar só.
//
// Por que existe (bug real da v1.2): os overlays usavam
// `Row { width: parent.width; layoutDirection: Qt.RightToLeft }`. Um `Row`
// não quebra linha nem encolhe filhos — quando a soma dos botões passa da
// largura disponível, eles simplesmente transbordam. No overlay de e-mail
// isso foi medido: largura 364px contra 616px de conteúdo, com dois botões
// em x negativo (-70 e -252), desenhados FORA do cartão. Era isso que fazia
// o modal parecer torto/cortado, com os botões espremidos.
//
// `Flow` resolve na raiz: mesmo alinhamento à direita, mas quebra para a
// linha de baixo quando não cabe. Nada de offset mágico, e continua correto
// em qualquer largura de janela ou tradução de rótulo mais longa.
Flow {
    id: buttonRow

    // Largura sempre vinda do pai (a coluna interna do cartão) — nunca um
    // valor fixo, senão o transbordo volta em telas estreitas.
    width: parent ? parent.width : 0
    spacing: Theme.spacingSm
    // Direita para a esquerda: a ação principal fica no canto direito, que
    // é onde o olho procura o botão de confirmar.
    layoutDirection: Qt.RightToLeft
}
