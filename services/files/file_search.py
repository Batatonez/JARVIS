"""Busca de arquivos: nome, metadata e conteúdo (v1.8).

--------------------------------------------------------------------------
Tudo local
--------------------------------------------------------------------------
Nenhuma consulta deste módulo sai da máquina. Nome, caminho, metadata e
conteúdo ficam no SQLite local; nenhum provider de IA é chamado, nem para
"melhorar" o ranking. Busca de arquivo funciona com a internet inteira fora
do ar — e o conteúdo dos documentos do usuário não é assunto de terceiro.

--------------------------------------------------------------------------
Ranking
--------------------------------------------------------------------------
Pesos explícitos e ordenados, do mais para o menos confiável:

    exact     100   o nome é exatamente a busca
    prefix     80   o nome começa com a busca
    contains   60   a busca aparece no nome
    token      45   todas as palavras aparecem, em qualquer ordem
    content    35   a busca aparece no CONTEÚDO
    fuzzy      25   parecido o bastante (erro de digitação, plural)

Fuzzy é o último de propósito. Ele é o que faz "parasita" achar
"PARASITAS E DOENÇAS.pdf", e também o que faria "casa" achar "caso", "cara"
e "asa" se não tivesse limiar. O limiar alto e a posição no fim do ranking
são o que mantêm o resultado útil.

Empate é desfeito pela data de modificação: entre dois arquivos igualmente
parecidos, o mais recente quase sempre é o procurado.
"""

import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 8
MAX_LIMIT = 50

SCORE_EXACT = 100.0
SCORE_PREFIX = 80.0
SCORE_CONTAINS = 60.0
SCORE_TOKEN = 45.0
SCORE_CONTENT = 35.0
SCORE_FUZZY = 25.0

# Abaixo disto, "parecido" vira "qualquer coisa". 0.72 foi escolhido por
# medida: deixa passar plural, acento e um caractere trocado, e barra
# palavras que só compartilham algumas letras.
FUZZY_THRESHOLD = 0.72

_SNIPPET_CHARS = 160


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_marks.lower().split())


@dataclass(frozen=True)
class FileSearchResult:
    """Um resultado já pronto para a UI.

    `handle` é um identificador OPACO da posição deste resultado na busca
    atual. É ele que a UI e as skills usam para agir sobre o arquivo, em vez
    do caminho — ver `FileSearchService.resolve_handle` para o porquê."""

    handle: str
    path: Path
    name: str
    extension: str
    size_bytes: int
    modified_at: datetime
    score: float
    match_type: str
    parent_label: str
    snippet: str = ""
    is_directory: bool = False

    def to_view(self) -> dict:
        """Forma enxuta para o QML. Sem caminho absoluto: a UI mostra a pasta
        pai, que é o que ajuda a reconhecer o arquivo, e o caminho completo
        fica no backend, atrás do handle."""
        return {
            "handle": self.handle,
            "name": self.name,
            "folder": self.parent_label,
            "extension": self.extension,
            "modified": _friendly_date(self.modified_at),
            "size": _friendly_size(self.size_bytes),
            "snippet": self.snippet,
            "matchType": self.match_type,
        }


def _friendly_size(size_bytes: int) -> str:
    for unit, threshold in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if size_bytes >= threshold:
            return f"{size_bytes / threshold:.1f} {unit}"
    return f"{size_bytes} B"


def _friendly_date(moment: datetime) -> str:
    """Data relativa quando é recente. "ontem" comunica melhor que
    "14/08/2026" para o que a pessoa acabou de mexer."""
    now = datetime.now(timezone.utc)
    delta = now - moment
    if delta < timedelta(hours=24) and moment.date() == now.date():
        return "hoje"
    if delta < timedelta(days=2):
        return "ontem"
    if delta < timedelta(days=7):
        return f"{delta.days} dias atrás"
    return moment.astimezone().strftime("%d/%m/%Y")


@dataclass(frozen=True)
class SearchFilters:
    """Filtros extraídos da própria frase (ver `parse_query`)."""

    extensions: tuple[str, ...] = ()
    modified_after: datetime | None = None
    min_size_bytes: int = 0
    text: str = ""


# Palavras que denunciam um filtro de tempo. Deliberadamente poucas: um
# parser de datas completo seria um projeto próprio, e estas cobrem o que as
# pessoas realmente digitam.
_TIME_HINTS: tuple[tuple[str, timedelta], ...] = (
    ("hoje", timedelta(days=1)),
    ("ontem", timedelta(days=2)),
    ("esta semana", timedelta(days=7)),
    ("essa semana", timedelta(days=7)),
    ("ultima semana", timedelta(days=7)),
    ("semana passada", timedelta(days=14)),
    ("este mes", timedelta(days=30)),
    ("esse mes", timedelta(days=30)),
    ("recentes", timedelta(days=7)),
    ("recente", timedelta(days=7)),
)

# Categorias faladas -> extensões.
_EXTENSION_GROUPS: dict[str, tuple[str, ...]] = {
    "pdf": (".pdf",),
    "pdfs": (".pdf",),
    "imagem": (".png", ".jpg", ".jpeg", ".gif", ".webp"),
    "imagens": (".png", ".jpg", ".jpeg", ".gif", ".webp"),
    "foto": (".png", ".jpg", ".jpeg"),
    "fotos": (".png", ".jpg", ".jpeg"),
    "video": (".mp4", ".mkv", ".avi", ".mov"),
    "videos": (".mp4", ".mkv", ".avi", ".mov"),
    "planilha": (".xlsx", ".xls", ".csv"),
    "planilhas": (".xlsx", ".xls", ".csv"),
    "documento": (".docx", ".doc", ".odt", ".pdf"),
    "documentos": (".docx", ".doc", ".odt", ".pdf"),
    "musica": (".mp3", ".flac", ".wav", ".m4a"),
    "musicas": (".mp3", ".flac", ".wav", ".m4a"),
}

# Palavras que não ajudam a casar nome de arquivo. Inclui os VERBOS de busca:
# `IntentRouter` já os remove quando a frase vem da Command Bar, mas
# `parse_query` também é chamada com a frase crua (skill `files.search`
# recebendo o texto do usuário direto), e deixar "acha" no termo faria a
# busca procurar por um arquivo chamado "acha".
_STOP_WORDS = frozenset(
    {"o", "a", "os", "as", "de", "do", "da", "dos", "das", "meu", "minha", "meus",
     "minhas", "aquele", "aquela", "um", "uma", "que", "com", "em", "no", "na",
     "arquivo", "arquivos", "file", "files", "modificados", "modificado", "baixei",
     "sobre", "chamado", "chamada", "the", "my",
     "acha", "achar", "ache", "encontra", "encontrar", "procura", "procurar",
     "busca", "buscar", "abre", "abrir", "onde", "esta", "fica", "find", "search",
     "open", "where",
     # Ligações de "o arquivo QUE FALA DE manguezal". Sem removê-las, a
     # consulta de conteúdo vira `"fala"* AND "manguezal"*` e não casa um
     # documento que só contém a segunda — o AND é o certo para termos de
     # busca e errado para palavras de ligação.
     "fala", "falam", "trata", "tratam", "menciona", "mencionam", "diz", "dizem",
     "contem", "contenha", "sobre", "about", "mentions", "talks"}
)


def parse_query(text: str) -> SearchFilters:
    """Extrai filtros da frase e devolve o que sobrou como texto de busca.

    Não é uma linguagem de consulta — é reconhecimento das poucas formas que
    aparecem de verdade: "PDFs de ontem", "imagens desta semana", "arquivos
    grandes". O resto da frase vira busca por nome/conteúdo."""
    normalized = _normalize(text)
    remaining = normalized
    extensions: list[str] = []
    modified_after: datetime | None = None
    min_size = 0

    for hint, window in _TIME_HINTS:
        if hint in remaining:
            candidate = datetime.now(timezone.utc) - window
            # Janela mais estreita vence: "hoje" é mais específico que
            # "recentes", e a frase pode conter os dois.
            if modified_after is None or candidate > modified_after:
                modified_after = candidate
            remaining = remaining.replace(hint, " ")

    for word, group in _EXTENSION_GROUPS.items():
        if re.search(rf"\b{re.escape(word)}\b", remaining):
            extensions.extend(group)
            remaining = re.sub(rf"\b{re.escape(word)}\b", " ", remaining)

    # `.ext` escrito literalmente na frase.
    for match in re.finditer(r"\.([a-z0-9]{1,6})\b", remaining):
        extensions.append(f".{match.group(1)}")
        remaining = remaining.replace(match.group(0), " ")

    if re.search(r"\b(grandes?|maiores?)\b", remaining):
        min_size = 10 * 1024 * 1024
        remaining = re.sub(r"\b(grandes?|maiores?)\b", " ", remaining)

    words = [w for w in remaining.split() if w not in _STOP_WORDS and len(w) > 1]
    return SearchFilters(
        extensions=tuple(dict.fromkeys(extensions)),
        modified_after=modified_after,
        min_size_bytes=min_size,
        text=" ".join(words),
    )


class FileSearchService:
    """Busca sobre o índice. Nunca toca o disco para procurar — só o índice."""

    def __init__(self, connection, *, index=None) -> None:
        self._conn = connection
        if index is None:
            from services.files.file_index import FileIndex

            index = FileIndex(connection)
        self._index = index
        # Caminhos reais da ÚLTIMA busca, por handle. Curto de propósito: um
        # handle não é um identificador durável, é uma referência à busca que
        # acabou de acontecer.
        self._handles: dict[str, Path] = {}

    # ------------------------------------------------------------------

    def search(self, query: str, *, limit: int = DEFAULT_LIMIT) -> list[FileSearchResult]:
        limit = max(1, min(int(limit), MAX_LIMIT))
        filters = parse_query(query)

        candidates: dict[str, tuple[float, str, str]] = {}  # path -> (score, tipo, snippet)

        if filters.text:
            self._match_by_name(filters, candidates)
            if self._index.content_search_available:
                self._match_by_content(filters, candidates)
        else:
            # Só filtros ("PDFs de ontem"): o resultado é a lista filtrada,
            # ordenada por data.
            self._match_by_filters_only(filters, candidates)

        if not candidates:
            self._handles = {}
            return []

        rows = self._fetch_rows(list(candidates))
        results: list[FileSearchResult] = []
        for row in rows:
            score, match_type, snippet = candidates[row["path"]]
            modified = datetime.fromisoformat(row["modified_at"])
            if not self._passes_filters(row, modified, filters):
                continue
            results.append(
                FileSearchResult(
                    handle="",  # atribuído depois da ordenação
                    path=Path(row["path"]),
                    name=row["name"],
                    extension=row["extension"],
                    size_bytes=row["size_bytes"],
                    modified_at=modified,
                    score=score,
                    match_type=match_type,
                    parent_label=self._folder_label(row["parent"], row["root_path"]),
                    snippet=snippet,
                    is_directory=bool(row["is_directory"]),
                )
            )

        # Score primeiro; empate desfeito pelo mais recente.
        results.sort(key=lambda item: (-item.score, -item.modified_at.timestamp()))
        results = results[:limit]

        self._handles = {}
        numbered: list[FileSearchResult] = []
        for position, item in enumerate(results, start=1):
            handle = f"file_{position}"
            self._handles[handle] = item.path
            numbered.append(
                FileSearchResult(
                    handle=handle, path=item.path, name=item.name, extension=item.extension,
                    size_bytes=item.size_bytes, modified_at=item.modified_at, score=item.score,
                    match_type=item.match_type, parent_label=item.parent_label,
                    snippet=item.snippet, is_directory=item.is_directory,
                )
            )
        # O log registra a CONTAGEM, nunca os caminhos: caminho de arquivo é
        # dado pessoal e não precisa estar num arquivo de log.
        logger.info("Busca de arquivos retornou %s resultados.", len(numbered))
        return numbered

    def recent(self, *, limit: int = DEFAULT_LIMIT, extensions: tuple[str, ...] = ()) -> list[FileSearchResult]:
        """Arquivos modificados mais recentemente.

        MODIFICADOS, não "abertos": o índice sabe o `mtime` do sistema de
        arquivos e nada além disso. Dizer "você abriu este arquivo ontem"
        seria afirmar algo que o JARVIS não tem como saber."""
        sql = "SELECT * FROM file_index WHERE is_directory = 0"
        params: list = []
        if extensions:
            placeholders = ",".join("?" for _ in extensions)
            sql += f" AND extension IN ({placeholders})"
            params += list(extensions)
        sql += " ORDER BY modified_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), MAX_LIMIT)))

        rows = self._conn.execute(sql, params).fetchall()
        self._handles = {}
        results = []
        for position, row in enumerate(rows, start=1):
            handle = f"file_{position}"
            path = Path(row["path"])
            self._handles[handle] = path
            results.append(
                FileSearchResult(
                    handle=handle, path=path, name=row["name"], extension=row["extension"],
                    size_bytes=row["size_bytes"],
                    modified_at=datetime.fromisoformat(row["modified_at"]),
                    score=SCORE_EXACT, match_type="recent",
                    parent_label=self._folder_label(row["parent"], row["root_path"]),
                )
            )
        return results

    def resolve_handle(self, handle: str) -> Path | None:
        """Handle -> caminho real, ou `None`.

        Existe para que nem a UI nem uma chamada de ferramenta proposta pelo
        modelo precisem manipular caminho. O modelo devolve `file_2`, não
        `C:\\Users\\...\\qualquer\\coisa` — e um caminho inventado
        simplesmente não resolve, o que elimina a classe inteira de injeção
        de caminho em uso de ferramenta."""
        return self._handles.get(handle)

    # ------------------------------------------------------------------

    def _match_by_name(self, filters: SearchFilters, candidates: dict) -> None:
        needle = filters.text
        tokens = needle.split()

        rows = self._conn.execute(
            "SELECT path, normalized_name FROM file_index WHERE is_directory = 0"
        ).fetchall()

        for row in rows:
            name = row["normalized_name"]
            path = row["path"]
            if name == needle:
                self._offer(candidates, path, SCORE_EXACT, "exact")
            elif name.startswith(needle):
                self._offer(candidates, path, SCORE_PREFIX, "prefix")
            elif needle in name:
                self._offer(candidates, path, SCORE_CONTAINS, "contains")
            elif len(tokens) > 1 and all(token in name for token in tokens):
                self._offer(candidates, path, SCORE_TOKEN, "token")
            else:
                ratio = difflib.SequenceMatcher(None, needle, name).ratio()
                if ratio >= FUZZY_THRESHOLD:
                    self._offer(candidates, path, SCORE_FUZZY * ratio, "fuzzy")
                elif tokens:
                    # Fuzzy por palavra: "parasita" contra "parasitas e
                    # doencas" tem razão baixa na string inteira, mas casa
                    # perfeitamente com uma das palavras.
                    name_words = name.split()
                    for token in tokens:
                        best = max(
                            (difflib.SequenceMatcher(None, token, word).ratio() for word in name_words),
                            default=0.0,
                        )
                        if best >= FUZZY_THRESHOLD:
                            self._offer(candidates, path, SCORE_FUZZY * best, "fuzzy")
                            break

    def _match_by_content(self, filters: SearchFilters, candidates: dict) -> None:
        try:
            rows = self._conn.execute(
                "SELECT f.path AS path, snippet(file_content_fts, 0, '', '', '…', 12) AS snip "
                "FROM file_content_fts JOIN file_index f ON f.id = file_content_fts.rowid "
                "WHERE file_content_fts MATCH ? LIMIT 50",
                (self._fts_query(filters.text),),
            ).fetchall()
        except Exception:
            # Consulta FTS malformada (o usuário digitou aspas soltas, por
            # exemplo) não pode derrubar a busca por nome, que já funcionou.
            logger.debug("Consulta de conteúdo ignorada (sintaxe FTS).")
            return

        for row in rows:
            snippet = (row["snip"] or "").strip()[:_SNIPPET_CHARS]
            self._offer(candidates, row["path"], SCORE_CONTENT, "content", snippet)

    def _match_by_filters_only(self, filters: SearchFilters, candidates: dict) -> None:
        rows = self._conn.execute(
            "SELECT path FROM file_index WHERE is_directory = 0 ORDER BY modified_at DESC LIMIT 200"
        ).fetchall()
        for row in rows:
            self._offer(candidates, row["path"], SCORE_CONTAINS, "metadata")

    @staticmethod
    def _fts_query(text: str) -> str:
        """Texto do usuário -> consulta FTS5 segura.

        Cada palavra vira um termo com prefixo (`palavra*`) e tudo é unido
        por AND. As aspas escapadas impedem que um `"` ou um `*` digitado
        vire sintaxe de consulta."""
        words = [word.replace('"', "") for word in text.split() if word]
        return " AND ".join(f'"{word}"*' for word in words) if words else '""'

    @staticmethod
    def _offer(candidates: dict, path: str, score: float, match_type: str, snippet: str = "") -> None:
        """Guarda o MELHOR casamento de cada arquivo. Um arquivo pode casar
        por nome e por conteúdo; vale o mais forte."""
        current = candidates.get(path)
        if current is None or score > current[0]:
            candidates[path] = (score, match_type, snippet or (current[2] if current else ""))
        elif snippet and not current[2]:
            candidates[path] = (current[0], current[1], snippet)

    def _fetch_rows(self, paths: list[str]):
        rows = []
        # Em blocos: SQLite tem teto de variáveis por consulta (999 por
        # padrão), e uma busca ampla pode passar disso.
        for start in range(0, len(paths), 500):
            chunk = paths[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows += self._conn.execute(
                f"SELECT * FROM file_index WHERE path IN ({placeholders})", chunk
            ).fetchall()
        return rows

    @staticmethod
    def _passes_filters(row, modified: datetime, filters: SearchFilters) -> bool:
        if filters.extensions and row["extension"] not in filters.extensions:
            return False
        if filters.modified_after is not None and modified < filters.modified_after:
            return False
        if filters.min_size_bytes and row["size_bytes"] < filters.min_size_bytes:
            return False
        return True

    @staticmethod
    def _folder_label(parent: str, root: str) -> str:
        """Pasta mostrada na UI: relativa à raiz indexada.

        `Documents\\Escola` comunica melhor que
        `C:\\Users\\davic\\OneDrive\\Documents\\Escola`, e mantém o caminho
        absoluto fora da tela."""
        try:
            relative = Path(parent).relative_to(Path(root).parent)
            return str(relative)
        except (ValueError, OSError):
            return Path(parent).name or parent
