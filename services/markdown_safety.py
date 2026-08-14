"""Sanitização do Markdown antes de renderizar no HUD.

O Qt renderiza Markdown nativamente (`TextEdit.MarkdownText`), o que é
ótimo — mas o renderizador de Rich Text do Qt **também interpreta HTML
embutido**. Como o texto vem de uma IA (e, por tabela, de qualquer coisa
que o usuário tenha colado no prompt), HTML cru não pode chegar lá:
`<img src=x onerror=...>`, `<iframe>`, `<a href="javascript:...">` e afins
transformariam o chat num mini-navegador com superfície de ataque.

Estratégia — **escapar, não remover**: qualquer `<tag>` vira `&lt;tag&gt;`
e aparece como texto literal. Assim o usuário continua vendo exatamente o
que a IA escreveu (importante quando se pede "me mostre um exemplo de
HTML"), sem que nada disso seja interpretado. Remover silenciosamente
mudaria o conteúdo e esconderia informação.

O texto RAW nunca é alterado no banco: isto é só a camada de exibição
(ver `frontend/message_model.py`, papel `markdown`).
"""

import re

# Blocos de código são preservados intactos: dentro deles, `<script>` é
# conteúdo legítimo que o usuário quer LER, e o Qt já não interpreta HTML
# dentro de bloco de código.
_FENCED_BLOCK = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")

# Esquemas de URL que nunca devem virar link clicável nem ser interpretados.
_DANGEROUS_SCHEME = re.compile(r"(?i)\b(javascript|vbscript|data|file)\s*:")

_HTML_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9-]*)((?:[^<>\"']|\"[^\"]*\"|'[^']*')*)>")
# Entidade HTML já escrita pelo usuário (`&lt;`) — preservada como está.
_ENTITY = re.compile(r"&(#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")


def _escape_html_tags(text: str) -> str:
    """Neutraliza qualquer tag HTML, deixando-a visível como texto."""
    return _HTML_TAG.sub(lambda m: m.group(0).replace("<", "&lt;").replace(">", "&gt;"), text)


def _neutralize_dangerous_urls(text: str) -> str:
    """`javascript:alert(1)` -> `javascript&#58;alert(1)`.

    Quebra o esquema sem apagar o texto: o usuário ainda lê o que estava
    escrito, mas o Qt não consegue montar um link executável."""
    return _DANGEROUS_SCHEME.sub(lambda m: m.group(1) + "&#58;", text)


def sanitize_markdown(text: str) -> str:
    """Deixa o Markdown seguro para `TextEdit.MarkdownText`.

    Preserva blocos de código e código inline exatamente como estão — lá
    dentro nada é interpretado como HTML, e mexer no conteúdo estragaria
    justamente o caso de "me mostre este HTML".
    """
    if not text:
        return ""

    # Fatia o texto em (código | resto) e só trata o "resto".
    parts: list[str] = []
    last_end = 0
    #
    # Blocos cercados têm precedência sobre código inline, e não é detalhe:
    # o padrão de inline (`` `...` ``) casa os DOIS primeiros backticks de
    # uma cerca ```` ``` ````, e se essa correspondência vencer, o bloco
    # inteiro deixa de ser protegido e seu conteúdo acaba escapado.
    fenced = [m.span() for m in _FENCED_BLOCK.finditer(text)]
    inline = [
        m.span()
        for m in _INLINE_CODE.finditer(text)
        if not any(start <= m.start() < end for start, end in fenced)
    ]
    protected = sorted(fenced + inline)

    merged: list[tuple[int, int]] = []
    for start, end in protected:
        if merged and start < merged[-1][1]:
            continue
        merged.append((start, end))

    for start, end in merged:
        segment = text[last_end:start]
        parts.append(_neutralize_dangerous_urls(_escape_html_tags(segment)))
        parts.append(text[start:end])  # código: intocado
        last_end = end
    tail = text[last_end:]
    parts.append(_neutralize_dangerous_urls(_escape_html_tags(tail)))

    return "".join(parts)


def contains_raw_html(text: str) -> bool:
    """Só para diagnóstico/teste: o texto tinha HTML antes de sanitizar?"""
    return bool(_HTML_TAG.search(text or ""))
