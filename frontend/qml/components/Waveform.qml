import QtQuick
import "../theme"

// Indicador discreto de nível de voz. Só deve ser usado com dado real
// (`level` vindo do microfone durante LISTENING) — nunca fica visível nem
// anima sozinho sem um nível real por trás, para não simular atividade que
// não existe (por isso não é usado durante SPEAKING: o TTS não expõe
// amplitude real, e uma "onda" ali seria decorativa/enganosa).
Item {
    id: waveform

    property real level: 0.0
    readonly property var barWeights: [0.45, 0.7, 1.0, 0.6, 0.85, 0.5, 0.75]

    implicitWidth: bars.implicitWidth
    implicitHeight: 26

    // Linha de base — dá a leitura de "waveform horizontal" mesmo com o
    // nível em repouso, em vez de barras soltas flutuando no ar.
    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        height: 1
        color: Theme.violet
        opacity: 0.18
    }

    Row {
        id: bars
        anchors.centerIn: parent
        spacing: 3

        Repeater {
            model: waveform.barWeights
            delegate: Rectangle {
                required property real modelData
                anchors.verticalCenter: parent.verticalCenter
                width: 3
                radius: 1.5
                height: Math.max(3, waveform.level * modelData * 26)
                color: Theme.violet
                opacity: 0.5 + waveform.level * 0.5
                Behavior on height { NumberAnimation { duration: 80; easing.type: Easing.OutQuad } }
            }
        }
    }
}
