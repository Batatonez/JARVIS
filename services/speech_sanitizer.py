"""`sanitize_text_for_tts` — transforma o texto de uma resposta no texto que
deve ser FALADO (v1.6.0).

--------------------------------------------------------------------------
Bug real que motivou o módulo
--------------------------------------------------------------------------
A resposta contém `**Importante**` e o sintetizador lê literalmente
"asterisco asterisco importante asterisco asterisco". Emojis também eram
verbalizados pelo nome em alguns casos.

A raiz é uma confusão de camadas: Markdown é notação de FORMATAÇÃO VISUAL.
Ele existe para o chat renderizar negrito; não tem tradução falada. Emoji é
notação visual pela mesma razão. O que o TTS precisa receber é a prosa, sem
a marcação.

--------------------------------------------------------------------------
Onde entra no pipeline
--------------------------------------------------------------------------
    provider
      ↓
    normalização (visible_content — reasoning já eliminado aqui)
      ↓
    ├── chat / banco / Copy   <- texto ORIGINAL, com Markdown
    └── sanitize_text_for_tts <- só o caminho da fala
          ↓
        speech_text

A ordem importa nos dois sentidos: sanitizar antes de persistir destruiria a
formatação que o chat deve mostrar, e sanitizar antes da normalização faria
o sanitizador trabalhar sobre raciocínio interno que nem deveria existir
naquele ponto.

--------------------------------------------------------------------------
O que é preservado, de propósito
--------------------------------------------------------------------------
Acentuação e cedilha (`á à â ã é ê í ó ô õ ú ç`) e símbolos que carregam
significado falado (`R$`, `€`, `%`, `+`, `-`, `=`). **Nunca** há conversão
para ASCII: transliterar destruiria o português, que é justamente o idioma
que o JARVIS fala por padrão. A remoção é dirigida — marcação e pictogramas
— nunca uma faixa Unicode inteira por precaução.
"""

import re
import unicodedata

# --- Blocos de código -----------------------------------------------------
# Política determinística escolhida: o BLOCO INTEIRO sai da fala.
#
# A alternativa (falar o conteúdo sem as crases) foi descartada por medida:
# código lido em voz alta é ininteligível — pontuação, indentação e símbolos
# viram um fluxo de ruído mais longo que a explicação em volta. Quem quer o
# código lê na tela; a fala fica com a prosa.
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_CODE_FENCE_UNCLOSED = re.compile(r"```.*\Z", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]+)`")

# --- Links e imagens ------------------------------------------------------
# `[OpenAI](https://…)` -> `OpenAI`. A URL sai: ler um endereço longo em voz
# alta é o pior caso possível de prosódia, e o texto do link é exatamente o
# rótulo que o autor escolheu para descrevê-lo.
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BARE_URL = re.compile(r"<https?://[^>]+>")

# --- Ênfase ---------------------------------------------------------------
# Ordem importa: `**` antes de `*`, `__` antes de `_`, senão o marcador duplo
# é consumido meio a meio e sobra um asterisco solto.
_BOLD_STAR = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_BOLD_UNDER = re.compile(r"__(.+?)__", re.DOTALL)
_ITALIC_STAR = re.compile(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", re.DOTALL)
# `_` só é ênfase fora de palavra — `nome_da_variavel` não pode ser mutilado.
_ITALIC_UNDER = re.compile(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", re.DOTALL)
_STRIKE = re.compile(r"~~(.+?)~~", re.DOTALL)

# --- Estrutura de linha ---------------------------------------------------
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_ORDERED = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
# Linha só de `---`/`***`/`___`: separador visual, sem leitura possível.
_HORIZONTAL_RULE = re.compile(r"^\s{0,3}([-*_])\s*(\1\s*){2,}$", re.MULTILINE)
# Linha de separação de tabela (`|---|---|`) — idem.
_TABLE_SEPARATOR = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$", re.MULTILINE)
_TABLE_PIPE = re.compile(r"\s*\|\s*")

# --- Whitespace -----------------------------------------------------------
_MULTI_BLANK_LINE = re.compile(r"\n{3,}")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")

# --- Emoji e pictogramas --------------------------------------------------
# Removidos por PROPRIEDADE do caractere, não por lista de emojis: uma lista
# fica desatualizada a cada versão do Unicode, e a propriedade não.
_ZWJ = "‍"  # zero-width joiner: cola emojis compostos (👨‍👩‍👧)
_VARIATION_SELECTORS = {"︎", "️"}  # apresentação texto/emoji
_SKIN_TONE_RANGE = (0x1F3FB, 0x1F3FF)  # Fitzpatrick
_REGIONAL_INDICATOR_RANGE = (0x1F1E6, 0x1F1FF)  # bandeiras (par de indicadores)
_KEYCAP = "⃣"  # combina com dígito: 1️⃣
_TAG_RANGE = (0xE0020, 0xE007F)  # tags de bandeira (🏴󠁧󠁢󠁳󠁣󠁴󠁿)

# Blocos que são pictográficos por natureza. Deliberadamente NÃO inclui os
# blocos de pontuação/símbolo geral, onde vivem `€`, `%`, `×`, `÷` e as aspas
# tipográficas — remover esses mudaria o SENTIDO do que é falado.
_PICTOGRAPHIC_RANGES = (
    (0x1F300, 0x1F5FF),  # símbolos e pictogramas diversos
    (0x1F600, 0x1F64F),  # emoticons
    (0x1F680, 0x1F6FF),  # transporte e mapas
    (0x1F900, 0x1F9FF),  # suplementares (gestos, pessoas)
    (0x1FA70, 0x1FAFF),  # suplementares estendidos
    (0x2600, 0x26FF),    # símbolos diversos (☀ ⚠ ♻)
    (0x2700, 0x27BF),    # dingbats (✅ ✂ ✈)
    (0x1F000, 0x1F0FF),  # cartas/mahjong/dominó
)


def _in_any_range(code_point: int, ranges) -> bool:
    return any(low <= code_point <= high for low, high in ranges)


def _is_pictographic(char: str) -> bool:
    code_point = ord(char)
    if char in _VARIATION_SELECTORS or char == _ZWJ or char == _KEYCAP:
        return True
    if _in_any_range(code_point, (_SKIN_TONE_RANGE, _REGIONAL_INDICATOR_RANGE, _TAG_RANGE)):
        return True
    return _in_any_range(code_point, _PICTOGRAPHIC_RANGES)


def strip_emoji(text: str) -> str:
    """Remove pictogramas e TODOS os seus modificadores.

    Filtrar caractere a caractere (e não por regex de sequência) é o que
    garante que nada de lixo residual sobre: um emoji composto por ZWJ, um
    par de indicadores regionais, um seletor de variação órfão ou um
    modificador de tom de pele isolado caem todos pela mesma regra, sem
    precisar enumerar as combinações possíveis."""
    return "".join(char for char in text if not _is_pictographic(char))


def _strip_markdown(text: str) -> str:
    text = _CODE_FENCE.sub(" ", text)
    # Um bloco aberto e nunca fechado (resposta truncada) também não deve ser
    # falado como se fosse prosa.
    text = _CODE_FENCE_UNCLOSED.sub(" ", text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _BARE_URL.sub("", text)
    text = _INLINE_CODE.sub(r"\1", text)

    text = _BOLD_STAR.sub(r"\1", text)
    text = _BOLD_UNDER.sub(r"\1", text)
    text = _STRIKE.sub(r"\1", text)
    text = _ITALIC_STAR.sub(r"\1", text)
    text = _ITALIC_UNDER.sub(r"\1", text)

    text = _HORIZONTAL_RULE.sub("", text)
    text = _TABLE_SEPARATOR.sub("", text)
    text = _HEADING.sub("", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _BULLET.sub("", text)
    text = _ORDERED.sub("", text)
    # Célula de tabela vira pausa curta: sem isto, "A|B" seria falado colado.
    text = _TABLE_PIPE.sub(", ", text)
    return text


def _normalize_whitespace(text: str) -> str:
    # Cada linha limpa isoladamente para que a remoção de marcadores não
    # deixe espaço à esquerda.
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = _MULTI_BLANK_LINE.sub("\n\n", text)
    text = _MULTI_SPACE.sub(" ", text)
    # Vírgulas órfãs deixadas pela conversão de tabela.
    text = re.sub(r"(,\s*){2,}", ", ", text)
    text = re.sub(r"^\s*,\s*", "", text, flags=re.MULTILINE)
    return text.strip()


def sanitize_text_for_tts(text: str) -> str:
    """Texto de resposta -> texto para falar.

    Usado por TODO caminho de fala (auto-speak e replay/listen), para não
    existir uma rota em que o sintetizador receba Markdown cru.

    Devolve string vazia quando não sobra nada pronunciável (ex.: uma
    resposta que era só um bloco de código) — quem chama trata isso como
    "não há o que falar", nunca como erro."""
    if not text:
        return ""

    cleaned = _strip_markdown(text)
    cleaned = strip_emoji(cleaned)
    # NFC (não NFKD): recompõe sequências equivalentes sem decompor acento,
    # preservando `ç`/`ã` intactos. NFKD seguido de descarte de combinantes é
    # exatamente a receita que transformaria "coração" em "coracao".
    cleaned = unicodedata.normalize("NFC", cleaned)
    return _normalize_whitespace(cleaned)
