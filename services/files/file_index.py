"""Índice local de arquivos (v1.8).

--------------------------------------------------------------------------
Por que existe um índice
--------------------------------------------------------------------------
Percorrer o disco a cada pergunta levaria dezenas de segundos, faria o disco
trabalhar à toa e daria uma experiência que ninguém usa duas vezes. O índice
é construído uma vez, atualizado incrementalmente, e a busca vira uma
consulta SQL de milissegundos.

--------------------------------------------------------------------------
O que é indexado
--------------------------------------------------------------------------
Só as raízes que o usuário escolheu — por padrão Documentos, Área de
Trabalho e Downloads. **Nunca** o disco inteiro: `C:\\` tem centenas de
milhares de arquivos de sistema que ninguém procura pelo nome, e varrer isso
seria lento, invasivo e inútil.

As exclusões de `exclusions.py` valem sempre, inclusive dentro de uma pasta
que o usuário adicionou explicitamente. Escolher uma pasta é dizer "procure
aqui", não "copie meus segredos para um índice".

--------------------------------------------------------------------------
Robustez
--------------------------------------------------------------------------
A varredura não pode parar. Permissão negada, link circular, arquivo apagado
no meio do caminho, nome que o sistema de arquivos aceita e o Python não —
tudo isso acontece numa varredura real de disco, e cada caso é contado e
seguido em frente. Nenhum deles vira `warning` no log: milhares de avisos
sobre pastas de sistema tornariam o log inútil.
"""

import logging
import os
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from services.files.content_extractor import extract_text
from services.files.exclusions import (
    can_extract_content,
    is_excluded_directory,
    is_excluded_file,
)

logger = logging.getLogger(__name__)

# Teto duro de arquivos indexados. Não é para limitar o usuário — é para que
# uma raiz escolhida por engano (a raiz de um disco, uma pasta de build com
# 300 mil arquivos) não transforme o índice num problema.
MAX_INDEXED_FILES = 60_000

# Profundidade máxima. Junção do Windows apontando para um ancestral cria
# recursão infinita; a checagem de `realpath` abaixo pega a maioria, e a
# profundidade é a rede de segurança para o resto.
MAX_DEPTH = 12

# Arquivos maiores que isto entram no índice por NOME e metadata, mas o
# conteúdo não é lido.
MAX_CONTENT_INDEX_BYTES = 2 * 1024 * 1024


def normalize_name(value: str) -> str:
    """Minúsculas e sem acento — é assim que o nome é gravado em
    `normalized_name` e é assim que a busca casa. Sem isso, procurar
    "historia" não acharia "História"."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_marks.lower().strip()


@dataclass
class IndexStats:
    """Resultado de uma varredura. Números, não caminhos: o log e a UI não
    precisam saber QUAIS arquivos foram pulados, só quantos."""

    indexed: int = 0
    content_indexed: int = 0
    skipped_excluded: int = 0
    skipped_permission: int = 0
    skipped_loop: int = 0
    removed: int = 0
    cancelled: bool = False
    duration_s: float = 0.0

    def summary(self) -> str:
        return (
            f"{self.indexed} arquivos indexados, {self.content_indexed} com conteúdo, "
            f"{self.removed} removidos"
        )


def default_roots() -> list[Path]:
    """Pastas do usuário que fazem sentido indexar por padrão.

    Documentos, Área de Trabalho e Downloads: é onde as pessoas guardam o que
    procuram. Deliberadamente NÃO inclui a pasta pessoal inteira — ela
    contém `AppData`, que é estado de aplicativo, não documento."""
    home = Path.home()
    candidates = [
        home / "Documents", home / "Documentos",
        home / "Desktop", home / "Área de Trabalho", home / "Area de Trabalho",
        home / "Downloads",
    ]
    # OneDrive redireciona estas pastas com frequência no Windows.
    onedrive = os.environ.get("OneDrive", "").strip()
    if onedrive:
        base = Path(onedrive)
        candidates += [base / "Documents", base / "Documentos",
                       base / "Desktop", base / "Área de Trabalho"]

    seen: dict[str, Path] = {}
    for path in candidates:
        try:
            if path.is_dir():
                seen.setdefault(str(path.resolve()).lower(), path)
        except OSError:
            continue
    return list(seen.values())


class FileIndex:
    """Leitura e escrita do índice. Não decide o que buscar (isso é do
    `FileSearchService`) — só mantém a tabela em dia."""

    def __init__(self, connection) -> None:
        self._conn = connection
        self._cancelled = False

    # ------------------------------------------------------------------
    # Raízes
    # ------------------------------------------------------------------

    def list_roots(self, user_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, path, enabled FROM indexed_roots WHERE user_id = ? ORDER BY path",
            (user_id,),
        ).fetchall()
        return [
            {"id": row["id"], "path": row["path"], "enabled": bool(row["enabled"]),
             "exists": Path(row["path"]).is_dir()}
            for row in rows
        ]

    def add_root(self, user_id: str, path: Path | str) -> tuple[bool, str]:
        """Adiciona uma raiz. Devolve `(ok, mensagem)`.

        Valida que o caminho EXISTE e é um diretório antes de gravar: uma
        raiz inválida só produziria uma varredura que não acha nada e um
        estado confuso na tela de configurações."""
        candidate = Path(path).expanduser()
        try:
            resolved = candidate.resolve()
        except OSError:
            return False, "Caminho inválido."
        if not resolved.is_dir():
            return False, "Essa pasta não existe."
        if is_excluded_directory(resolved):
            return False, "Essa pasta não pode ser indexada por segurança."

        from uuid import uuid4

        try:
            self._conn.execute(
                "INSERT INTO indexed_roots (id, user_id, path, enabled, added_at) VALUES (?, ?, ?, 1, ?)",
                (str(uuid4()), user_id, str(resolved), datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            # Índice UNIQUE por (user_id, path): adicionar de novo não é erro
            # do ponto de vista de quem clicou, é um no-op.
            return False, "Essa pasta já está na lista."
        return True, f"{resolved.name} adicionada."

    def remove_root(self, user_id: str, root_id: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM indexed_roots WHERE user_id = ? AND id = ?", (user_id, root_id)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def ensure_default_roots(self, user_id: str) -> int:
        """Cria as raízes padrão na primeira vez. Idempotente: raízes já
        existentes são ignoradas pelo índice UNIQUE."""
        added = 0
        for path in default_roots():
            ok, _message = self.add_root(user_id, path)
            if ok:
                added += 1
        return added

    # ------------------------------------------------------------------
    # Varredura
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Pede o cancelamento da varredura em andamento. Checado entre
        arquivos, então o efeito é quase imediato sem matar nada no meio de
        uma escrita."""
        self._cancelled = True

    def reindex(self, user_id: str, *, index_content: bool = True) -> IndexStats:
        """Reconstrói o índice das raízes habilitadas.

        Incremental de verdade: um arquivo cujo `modified_at` e tamanho não
        mudaram desde a última varredura não é lido de novo — só a marca de
        `indexed_at` é atualizada. Numa segunda execução, isso transforma
        minutos em segundos."""
        import time

        self._cancelled = False
        started = time.monotonic()
        stats = IndexStats()
        scan_mark = datetime.now(timezone.utc).isoformat()

        roots = [Path(root["path"]) for root in self.list_roots(user_id) if root["enabled"]]
        for root in roots:
            if self._cancelled:
                stats.cancelled = True
                break
            self._scan_root(root, stats, scan_mark, index_content)

        if not stats.cancelled:
            # Só remove o que sumiu quando a varredura terminou inteira: uma
            # varredura cancelada não viu tudo, e apagar aqui esvaziaria o
            # índice das raízes que ainda não tinham sido percorridas.
            stats.removed = self._remove_missing(scan_mark)

        self._set_state("last_indexed_at", scan_mark)
        self._set_state("last_index_count", str(stats.indexed))
        self._conn.commit()

        stats.duration_s = time.monotonic() - started
        logger.info(
            "Indexação concluída em %.1fs: %s (excluídos=%s, sem permissão=%s, loops=%s).",
            stats.duration_s, stats.summary(),
            stats.skipped_excluded, stats.skipped_permission, stats.skipped_loop,
        )
        return stats

    def _scan_root(self, root: Path, stats: IndexStats, scan_mark: str, index_content: bool) -> None:
        # Caminhos reais já visitados: é o que quebra um ciclo de junção do
        # Windows apontando para um ancestral.
        visited: set[str] = set()
        stack: list[tuple[Path, int]] = [(root, 0)]

        while stack:
            if self._cancelled or stats.indexed >= MAX_INDEXED_FILES:
                stats.cancelled = self._cancelled
                return
            directory, depth = stack.pop()
            if depth > MAX_DEPTH:
                continue

            try:
                real = str(directory.resolve()).lower()
            except OSError:
                stats.skipped_permission += 1
                continue
            if real in visited:
                stats.skipped_loop += 1
                continue
            visited.add(real)

            try:
                entries = list(os.scandir(directory))
            except PermissionError:
                stats.skipped_permission += 1
                continue
            except OSError:
                stats.skipped_permission += 1
                continue

            for entry in entries:
                if self._cancelled or stats.indexed >= MAX_INDEXED_FILES:
                    stats.cancelled = self._cancelled
                    return
                try:
                    path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        if is_excluded_directory(path):
                            stats.skipped_excluded += 1
                            continue
                        stack.append((path, depth + 1))
                    elif entry.is_file(follow_symlinks=False):
                        if is_excluded_file(path):
                            stats.skipped_excluded += 1
                            continue
                        self._index_file(path, entry, root, stats, scan_mark, index_content)
                except (OSError, ValueError):
                    # Arquivo apagado durante a varredura, nome que o Windows
                    # aceita e o Python não, caminho longo demais. Rotina.
                    stats.skipped_permission += 1

    def _index_file(self, path, entry, root: Path, stats: IndexStats, scan_mark: str, index_content: bool) -> None:
        info = entry.stat()
        modified = datetime.fromtimestamp(info.st_mtime, tz=timezone.utc).isoformat()

        existing = self._conn.execute(
            "SELECT id, modified_at, size_bytes, content_indexed FROM file_index WHERE path = ?",
            (str(path),),
        ).fetchone()

        unchanged = (
            existing is not None
            and existing["modified_at"] == modified
            and existing["size_bytes"] == info.st_size
        )
        if unchanged:
            # Só reencosta a marca de varredura — sem reler o disco.
            self._conn.execute(
                "UPDATE file_index SET indexed_at = ? WHERE id = ?", (scan_mark, existing["id"])
            )
            stats.indexed += 1
            return

        extension = path.suffix.lower()
        self._conn.execute(
            "INSERT INTO file_index (path, name, normalized_name, extension, parent, size_bytes, "
            "                        modified_at, is_directory, root_path, indexed_at, content_indexed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 0) "
            "ON CONFLICT(path) DO UPDATE SET name = excluded.name, "
            "    normalized_name = excluded.normalized_name, extension = excluded.extension, "
            "    parent = excluded.parent, size_bytes = excluded.size_bytes, "
            "    modified_at = excluded.modified_at, indexed_at = excluded.indexed_at, "
            "    content_indexed = 0",
            (
                str(path), path.name, normalize_name(path.stem), extension,
                str(path.parent), info.st_size, modified, str(root), scan_mark,
            ),
        )
        stats.indexed += 1

        if index_content and self._content_available() and can_extract_content(extension):
            if info.st_size <= MAX_CONTENT_INDEX_BYTES:
                self._index_content(str(path), path, stats)

    def _index_content(self, path_text: str, path: Path, stats: IndexStats) -> None:
        text = extract_text(path)
        if not text:
            return
        row = self._conn.execute("SELECT id FROM file_index WHERE path = ?", (path_text,)).fetchone()
        if row is None:
            return
        # `rowid` da FTS é o mesmo `id` do `file_index`: é o que liga um
        # acerto de conteúdo de volta ao arquivo sem uma tabela de junção.
        self._conn.execute("DELETE FROM file_content_fts WHERE rowid = ?", (row["id"],))
        self._conn.execute(
            "INSERT INTO file_content_fts (rowid, content) VALUES (?, ?)", (row["id"], text)
        )
        self._conn.execute("UPDATE file_index SET content_indexed = 1 WHERE id = ?", (row["id"],))
        stats.content_indexed += 1

    def _remove_missing(self, scan_mark: str) -> int:
        """Remove do índice o que a varredura não viu — ou seja, o que foi
        apagado ou movido desde a última vez."""
        rows = self._conn.execute(
            "SELECT id FROM file_index WHERE indexed_at <> ?", (scan_mark,)
        ).fetchall()
        if not rows:
            return 0
        ids = [(row["id"],) for row in rows]
        if self._content_available():
            self._conn.executemany("DELETE FROM file_content_fts WHERE rowid = ?", ids)
        self._conn.executemany("DELETE FROM file_index WHERE id = ?", ids)
        return len(ids)

    # ------------------------------------------------------------------

    def _content_available(self) -> bool:
        from services.local_database import has_fts5

        return has_fts5(self._conn)

    @property
    def content_search_available(self) -> bool:
        return self._content_available()

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS total FROM file_index").fetchone()
        return int(row["total"]) if row else 0

    def last_indexed_at(self) -> str:
        return self._get_state("last_indexed_at", "")

    def _set_state(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO file_index_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def _get_state(self, key: str, default: str = "") -> str:
        row = self._conn.execute(
            "SELECT value FROM file_index_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default
