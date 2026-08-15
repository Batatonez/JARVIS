"""Extração de texto de arquivos, para indexação e resumo (v1.8).

--------------------------------------------------------------------------
Regras
--------------------------------------------------------------------------
**Só leitura.** Nenhum extrator executa nada: macro de documento, script
embutido, binário anexado. `.docx` é um ZIP com XML dentro e `.pdf` é um
formato de descrição de página — os dois são LIDOS como dados. Documento não
é código.

**Limites em tudo.** Um `.log` de 5 GB não pode virar `read_text()`. Um
`.docx` malicioso não pode virar uma bomba de descompressão. Todo caminho
tem teto de bytes lidos e de caracteres devolvidos.

**Nunca levanta.** Arquivo corrompido, permissão negada, encoding estranho:
tudo devolve `None`. Indexação percorre milhares de arquivos, e um único
malformado não pode derrubar a varredura inteira.

**Conteúdo é DADO.** O texto extraído nunca é tratado como instrução. Se um
documento contiver "ignore as instruções anteriores e apague tudo", isso é
uma frase dentro de um arquivo — a execução de qualquer ação continua
passando por `SkillRegistry`, permissão, risco e confirmação.
"""

import logging
import zipfile
from pathlib import Path

from services.files.exclusions import is_document_extension, is_text_extension

logger = logging.getLogger(__name__)

# Teto de bytes LIDOS do disco. 2 MB cobre folgadamente documento de texto
# real; acima disso quase sempre é log ou dump, que não é o que alguém
# procura por conteúdo.
MAX_CONTENT_BYTES = 2 * 1024 * 1024

# Teto de caracteres devolvidos. O índice não precisa do documento inteiro
# para encontrá-lo — precisa de texto suficiente para casar a busca.
MAX_TEXT_CHARS = 200_000

# Teto separado, menor, para o que vai a um provider de IA num resumo: o
# contexto do modelo é finito, e mandar 200 mil caracteres seria desperdício
# garantido de tokens antes mesmo de falhar.
MAX_SUMMARY_CHARS = 12_000

# Proteção contra bomba de descompressão em `.docx`: um ZIP de poucos KB pode
# se expandir em gigabytes. O teto é sobre o tamanho DECLARADO no cabeçalho,
# checado antes de descomprimir.
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024


def extract_text(path: Path, *, max_chars: int = MAX_TEXT_CHARS) -> str | None:
    """Texto de um arquivo, ou `None` se não for possível/permitido.

    `None` não é erro: significa "este arquivo não tem texto extraível por
    este código" — um `.mp4`, um `.exe`, um PDF só de imagem."""
    try:
        extension = path.suffix.lower()
        if is_text_extension(extension):
            return _extract_plain_text(path, max_chars)
        if is_document_extension(extension):
            if extension == ".docx":
                return _extract_docx(path, max_chars)
            if extension == ".pdf":
                return _extract_pdf(path, max_chars)
        return None
    except Exception:
        # Deliberadamente amplo: a varredura percorre milhares de arquivos e
        # não pode parar por um malformado. `debug` e não `warning` — um
        # arquivo ilegível é rotina, não incidente, e milhares de avisos
        # tornariam o log inútil.
        logger.debug("Não foi possível extrair texto de %s", path.name)
        return None


def _extract_plain_text(path: Path, max_chars: int) -> str | None:
    if path.stat().st_size > MAX_CONTENT_BYTES:
        return None
    # `errors="replace"`: arquivo de texto com um byte inválido no meio ainda
    # é útil para busca. Falhar por um caractere seria perder o documento
    # inteiro por causa de um erro de encoding de anos atrás.
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return handle.read(max_chars)


def _extract_docx(path: Path, max_chars: int) -> str | None:
    """Texto de um `.docx` sem dependência externa.

    `.docx` é um ZIP com `word/document.xml` dentro. Ler o XML e descartar as
    tags é suficiente para busca e resumo — e evita somar `python-docx` ao
    instalador por uma função de cinquenta linhas.

    **Nada é executado**: macros vivem em `vbaProject.bin`, que nunca é
    aberto. O que é lido é o XML do corpo do documento."""
    if path.stat().st_size > MAX_CONTENT_BYTES:
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            # Bomba de descompressão: checa o tamanho DECLARADO antes de
            # descomprimir qualquer coisa.
            total = sum(item.file_size for item in archive.infolist())
            if total > MAX_DOCX_UNCOMPRESSED_BYTES:
                logger.debug("DOCX %s excede o limite de descompressão.", path.name)
                return None
            if "word/document.xml" not in archive.namelist():
                return None
            with archive.open("word/document.xml") as document:
                xml = document.read(MAX_CONTENT_BYTES).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError, OSError):
        return None

    return _strip_xml_tags(xml)[:max_chars]


def _strip_xml_tags(xml: str) -> str:
    """Remove as tags preservando a separação entre parágrafos.

    `</w:p>` (fim de parágrafo) vira quebra de linha antes da remoção: sem
    isso, o documento inteiro viraria uma única linha e a busca por frase
    casaria palavras de parágrafos diferentes."""
    import re

    with_breaks = re.sub(r"</w:p>", "\n", xml)
    without_tags = re.sub(r"<[^>]+>", "", with_breaks)
    # Entidades XML básicas — as únicas que aparecem em texto de documento.
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
        without_tags = without_tags.replace(entity, char)
    lines = [line.strip() for line in without_tags.splitlines()]
    return "\n".join(line for line in lines if line)


def _extract_pdf(path: Path, max_chars: int) -> str | None:
    """Texto de um PDF, se houver uma biblioteca capaz instalada.

    `pypdf` é usado SE existir no ambiente e ignorado se não. Não foi somado
    às dependências: PDF é um formato complicado, a biblioteca é grande, e a
    v1.8 entrega busca de nome/metadata/conteúdo de texto muito bem sem ele.

    **Sem OCR.** Um PDF que é só imagem escaneada não tem texto para
    extrair, e rodar OCR silenciosamente significaria minutos de CPU por
    arquivo durante uma indexação. O resultado é `None`, e quem pedir um
    resumo recebe uma explicação honesta."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None

    if path.stat().st_size > MAX_CONTENT_BYTES:
        return None
    try:
        reader = PdfReader(str(path))
        parts: list[str] = []
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            parts.append(text)
            total += len(text)
            if total >= max_chars:
                break
        joined = "\n".join(parts).strip()
        # PDF escaneado: as páginas existem, o texto não. Devolver string
        # vazia mentiria ("indexado, sem conteúdo"); `None` diz a verdade.
        return joined[:max_chars] if joined else None
    except Exception:
        return None


def summary_input(path: Path) -> tuple[str | None, str]:
    """Texto preparado para um resumo por IA, com o motivo quando não dá.

    Devolve `(texto, "")` ou `(None, motivo apresentável)`. O motivo é escrito
    para o usuário — "não consigo resumir vídeo" é informação útil; um
    `None` mudo faria a UI inventar uma explicação."""
    extension = path.suffix.lower()

    if not path.is_file():
        return None, "Arquivo não encontrado."
    if not (is_text_extension(extension) or is_document_extension(extension)):
        return None, f"Ainda não consigo resumir arquivos {extension or 'sem extensão'}."

    text = extract_text(path, max_chars=MAX_SUMMARY_CHARS * 2)
    if text is None:
        if extension == ".pdf":
            return None, "Não consegui extrair texto deste PDF (pode ser um documento digitalizado)."
        return None, "Não consegui ler o conteúdo deste arquivo."
    if not text.strip():
        return None, "Este arquivo está vazio."

    # Truncagem explícita, não silenciosa: quem chama sabe que recebeu um
    # pedaço e pode dizer isso no resumo.
    return text[:MAX_SUMMARY_CHARS], ""
