pragma Singleton
import QtQuick

// Tema central do JARVIS HUD. Nenhuma cor/espaçamento/duração deve ser
// hardcoded em outro QML — sempre referenciar Theme.*.
QtObject {
    // --- Paleta ---
    // v0.6: fundo continua quase preto, mas superfícies e texto secundário
    // ganharam contraste real — o v0.5 estava escuro demais em status,
    // labels e placeholders.
    readonly property color background: "#070A10"
    // v0.8: topo do gradiente do fundo (Main.qml) — azul-grafite bem suave,
    // nunca preto puro; dá profundidade sem virar "wallpaper".
    readonly property color backgroundSecondary: "#0C1524"
    readonly property color surface: "#0C1420"
    readonly property color surfaceElevated: "#121D2C"
    readonly property color surfacePanel: "#0F1826" // faixas discretas (status), entre surface e surfaceElevated
    // Superfície translúcida (StatusPanel, cartões) — sensação de "vidro"
    // sobre o HUD contínuo, sem virar card opaco flutuante.
    readonly property color surfaceGlass: Qt.rgba(0.058, 0.086, 0.129, 0.62)
    // Hover genérico para controles que hoje só tinham pressed/tint fixo
    // (ActionButton, WindowButton, MicButton) — um único valor em vez de
    // rgba(1,1,1,x) espalhado com opacidades levemente diferentes.
    readonly property color surfaceHover: Qt.rgba(1, 1, 1, 0.07)

    readonly property color cyan: "#5CE1E6"
    // Ciano mais saturado/claro — hover e ênfase de elementos primários
    // (não é um estado novo, é o "cyan" sob luz mais forte).
    readonly property color primaryBright: "#8FF3F7"
    readonly property color blue: "#4C8DFF"
    readonly property color violet: "#9B6BFF"

    readonly property color textPrimary: "#F3F9FF"
    readonly property color textSecondary: "#B7C8DB" // labels/valores que precisam ser lidos de relance
    readonly property color textMuted: "#93A7BC"      // era #8297A8 — pouco legível sobre o fundo v0.5
    readonly property color textFaint: "#6A7F93"      // era #4E6072 — timestamps/labels técnicos, ainda discreto mas legível

    readonly property color success: "#4ADE80"
    readonly property color warning: "#FBBF24"
    readonly property color danger: "#FF6B7F"

    readonly property color borderSubtle: Qt.rgba(1, 1, 1, 0.10)
    readonly property color borderStrong: Qt.rgba(1, 1, 1, 0.16)
    readonly property color borderFocus: Qt.rgba(0.361, 0.882, 0.902, 0.55) // cyan translúcido

    // --- Geometria ---
    readonly property int radiusSmall: 6
    readonly property int radiusMedium: 12
    readonly property int radiusLarge: 20

    readonly property int spacingXs: 4
    readonly property int spacingSm: 8
    readonly property int spacingMd: 16
    readonly property int spacingLg: 28
    readonly property int spacingXl: 44

    // --- Tipografia ---
    // "Segoe UI Variable" só existe no Windows 11+; o mecanismo de
    // substituição de fontes do próprio Windows/Qt cai para "Segoe UI"
    // automaticamente quando a família exata não está instalada.
    readonly property string fontFamily: "Segoe UI Variable"
    readonly property real letterSpacingLabel: 1.1

    // --- Animação ---
    readonly property int durationFast: 120
    readonly property int durationNormal: 220
    readonly property int durationSlow: 420
    readonly property int durationBoot: 1050

    readonly property int easingStandard: Easing.OutCubic

    // --- Helpers de estado ---
    // v0.7: listening/speaking/processing_speech (voz) precisam ser
    // reconhecíveis à primeira vista — em especial LISTENING não pode se
    // confundir com THINKING (mesma cor ciano usada antes para os dois).
    function stateColor(state) {
        if (state === "error") return danger
        if (state === "waiting_confirmation") return warning
        if (state === "listening") return violet
        if (state === "speaking") return blue
        return cyan
    }

    function riskColor(riskLevel) {
        if (riskLevel === "dangerous") return danger
        if (riskLevel === "action") return warning
        return cyan
    }
}
