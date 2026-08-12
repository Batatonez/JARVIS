"""Memória de longo prazo por usuário (v1.1).

Não confundir com o histórico de conversa:

    User
    ├── Conversation A   (mensagens — pertencem a UM chat)
    ├── Conversation B
    └── LongTermMemory   (fatos sobre o usuário — atravessam TODOS os chats)

O problema que isto resolve: dizer "meu nome é Davi" no chat 1 e o JARVIS
não saber disso no chat 2.

**O que NÃO fazemos**: mandar o histórico de todas as conversas para o
provider a cada mensagem. Isso seria ruim de privacidade, custo, tokens,
relevância e performance. Em vez disso, extraímos poucos fatos duráveis e
recuperamos só os relevantes.

**Extração deliberadamente conservadora.** Uma frase só vira memória quando
bate num padrão claro de fato durável ("meu nome é X", "prefiro Y",
"lembre que Z"). "Quanto é 5 + 5?" não vira memória — e isso é testado. É
melhor lembrar de menos e estar certo do que encher a memória de lixo: uma
memória errada contamina todas as conversas futuras.

Isto é heurístico e local, de propósito: nenhuma chamada de IA é feita para
decidir o que memorizar (custaria dinheiro a cada mensagem e criaria
dependência de rede num caminho que precisa ser barato e previsível).
"""

import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

logger = logging.getLogger(__name__)

# Teto de memórias por usuário. Sem isto, a memória cresceria sem limite e o
# contexto enviado ao provider junto. Ao estourar, a mais antiga sai.
MAX_MEMORIES_PER_USER = 200
# Quantas memórias, no máximo, entram no contexto de uma chamada.
MAX_MEMORIES_IN_CONTEXT = 20


class MemoryCategory(str, Enum):
    IDENTITY = "identity"  # nome, como quer ser chamado
    PREFERENCE = "preference"  # "prefiro respostas curtas"
    PROJECT = "project"  # "meu projeto se chama BatataMC"
    USER_FACT = "user_fact"  # outros fatos duráveis
    EXPLICIT = "explicit_memory"  # "lembre que ..."


@dataclass(frozen=True)
class UserMemory:
    id: str
    user_id: str
    category: MemoryCategory
    content: str
    created_at: datetime
    updated_at: datetime
    source_conversation_id: str | None = None


# ---------------------------------------------------------------------------
# Extração
# ---------------------------------------------------------------------------

# Cada padrão captura o "miolo" do fato no grupo 1. Ordem importa: o primeiro
# que casar define a categoria.
#
# IMPORTANTE: estes padrões são aplicados ao texto **sem acento**
# (`_fold_accents`), então devem ser escritos sem acento — `meu nome e`,
# não `meu nome é`. É o que faz "Meu nome e Davi" (como se digita de
# verdade) funcionar tão bem quanto "Meu nome é Davi".
_PATTERNS: tuple[tuple[re.Pattern[str], MemoryCategory, str], ...] = (
    # Pedido explícito de memorização — o sinal mais forte que existe.
    (re.compile(r"^\s*(?:lembre|lembra|memorize|anote|guarde)\s+(?:que\s+|disso\s*:\s*|isso\s*:\s*)?(.+)$", re.I),
     MemoryCategory.EXPLICIT, "{0}"),
    (re.compile(r"^\s*(?:remember|note)\s+(?:that\s+)?(.+)$", re.I),
     MemoryCategory.EXPLICIT, "{0}"),
    # Identidade.
    (re.compile(r"\b(?:meu\s+nome\s+e|me\s+chamo|pode\s+me\s+chamar\s+de)\s+([A-Za-z][\w'\- ]{1,40})", re.I),
     MemoryCategory.IDENTITY, "O nome do usuário é {0}."),
    (re.compile(r"\b(?:my\s+name\s+is|call\s+me)\s+([A-Za-z][\w'\- ]{1,40})", re.I),
     MemoryCategory.IDENTITY, "O nome do usuário é {0}."),
    # Preferências.
    (re.compile(r"\b(?:eu\s+)?prefiro\s+(.{3,120})", re.I),
     MemoryCategory.PREFERENCE, "O usuário prefere {0}."),
    (re.compile(r"\b(?:eu\s+)?(?:gosto|nao\s+gosto)\s+de\s+(.{3,120})", re.I),
     MemoryCategory.PREFERENCE, None),  # None = guarda a frase original
    (re.compile(r"\bmeu\s+(?:jogo|filme|livro|time|comida|cor)\s+favorit[oa]\s+e\s+(.{2,80})", re.I),
     MemoryCategory.PREFERENCE, None),
    # Projetos.
    (re.compile(r"\bmeu\s+projeto\s+(?:se\s+chama|chama-se|e)\s+(.{2,80})", re.I),
     MemoryCategory.PROJECT, "O projeto do usuário se chama {0}."),
    (re.compile(r"\b(?:estou|to)\s+(?:trabalhando|desenvolvendo)\s+(?:no|na|em)\s+(.{3,100})", re.I),
     MemoryCategory.PROJECT, None),
    # Fatos pessoais duráveis.
    (re.compile(r"\b(?:eu\s+)?(?:moro|trabalho|estudo)\s+(?:em|na|no|com)\s+(.{2,80})", re.I),
     MemoryCategory.USER_FACT, None),
)

# Frases que nunca devem virar memória, mesmo batendo num padrão acima:
# perguntas e pedidos pontuais.
_NEVER_REMEMBER = re.compile(
    r"^\s*(?:quanto|quantos|qual|quais|quem|quando|onde|por\s*que|porque|como|o\s+que|"
    r"what|who|when|where|why|how)\b",
    re.I,
)

_MAX_MEMORY_CHARS = 300


def _fold_accents(text: str) -> str:
    """Remove acentos **preservando o comprimento** (1 caractere entra, 1
    sai). Isso é o que permite casar os padrões contra o texto sem acento
    ("meu nome e davi") e ainda assim recortar o trecho capturado do texto
    ORIGINAL, com acentuação e caixa intactas.

    Em português informal a acentuação é omitida o tempo todo; sem isto,
    "Meu nome e Davi" simplesmente não viraria memória."""
    folded: list[str] = []
    for char in text:
        decomposed = unicodedata.normalize("NFD", char)
        base = "".join(c for c in decomposed if not unicodedata.combining(c))
        # Se a decomposição não devolver exatamente 1 caractere, mantém o
        # original — o mapeamento 1:1 é o que torna os spans confiáveis.
        folded.append(base if len(base) == 1 else char)
    return "".join(folded)


def extract_memories(text: str) -> list[tuple[MemoryCategory, str]]:
    """Extrai fatos duráveis de UMA mensagem do usuário. Devolve
    `[(categoria, conteúdo)]` — quase sempre vazio, e isso é o esperado:
    a maioria das mensagens não contém fato durável."""
    if not text:
        return []
    stripped = text.strip()
    if not stripped or len(stripped) > 2000:
        return []

    folded = _fold_accents(stripped)
    assert len(folded) == len(stripped)  # invariante do mapeamento 1:1

    # Uma pergunta não afirma um fato sobre o usuário. "Qual é meu nome?"
    # não pode virar memória só porque contém "meu nome".
    if _NEVER_REMEMBER.match(folded) or stripped.rstrip().endswith("?"):
        return []

    found: list[tuple[MemoryCategory, str]] = []
    for pattern, category, template in _PATTERNS:
        # Casa no texto sem acento, mas recorta do original.
        match = pattern.search(folded)
        if not match:
            continue
        start, end = match.span(1)
        captured = stripped[start:end].strip().rstrip(".,;:!")
        if not captured:
            continue
        content = template.format(captured) if template else stripped
        content = content.strip()[:_MAX_MEMORY_CHARS]
        if content:
            found.append((category, content))
        # Uma memória por mensagem: o padrão mais específico já casou, e
        # múltiplas extrações da mesma frase geram quase-duplicatas.
        break
    return found


def _normalize_for_dedup(text: str) -> str:
    """Chave de deduplicação: minúsculas, sem acento, sem pontuação, sem
    espaço redundante. "Meu nome é Davi." e "meu nome e davi" colidem — que
    é exatamente o que queremos."""
    lowered = text.strip().lower()
    decomposed = unicodedata.normalize("NFKD", lowered)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", without_accents).strip()


class LongTermMemoryRepository:
    """Persistência das memórias. **Toda** query é escopada por `user_id` —
    nunca existe leitura só por `memory_id` (seria um IDOR)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def remember(
        self,
        *,
        user_id: str,
        category: MemoryCategory,
        content: str,
        source_conversation_id: str | None = None,
    ) -> UserMemory:
        """Insere ou atualiza (upsert por `dedup_key`). Repetir o mesmo fato
        não cria uma segunda linha — só atualiza `updated_at`."""
        content = content.strip()[:_MAX_MEMORY_CHARS]
        dedup_key = _normalize_for_dedup(content)
        now = datetime.now(timezone.utc)

        existing = self._conn.execute(
            "SELECT id, created_at FROM user_memories WHERE user_id = ? AND dedup_key = ?",
            (user_id, dedup_key),
        ).fetchone()

        if existing is not None:
            self._conn.execute(
                "UPDATE user_memories SET content = ?, category = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (content, category.value, now.isoformat(), existing["id"], user_id),
            )
            self._conn.commit()
            return UserMemory(
                id=existing["id"],
                user_id=user_id,
                category=category,
                content=content,
                created_at=datetime.fromisoformat(existing["created_at"]),
                updated_at=now,
                source_conversation_id=source_conversation_id,
            )

        memory_id = str(uuid4())
        self._conn.execute(
            "INSERT INTO user_memories "
            "(id, user_id, category, content, dedup_key, source_conversation_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                memory_id,
                user_id,
                category.value,
                content,
                dedup_key,
                source_conversation_id,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        self._conn.commit()
        self._enforce_limit(user_id)
        return UserMemory(
            id=memory_id,
            user_id=user_id,
            category=category,
            content=content,
            created_at=now,
            updated_at=now,
            source_conversation_id=source_conversation_id,
        )

    def list_memories(self, user_id: str) -> list[UserMemory]:
        rows = self._conn.execute(
            "SELECT * FROM user_memories WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)
        ).fetchall()
        return [self._row_to_memory(row) for row in rows]

    def forget(self, *, user_id: str, memory_id: str) -> bool:
        """Escopado por usuário de propósito: um `memory_id` de outra conta
        nunca é apagável (nem descobrível) daqui."""
        cursor = self._conn.execute(
            "DELETE FROM user_memories WHERE id = ? AND user_id = ?", (memory_id, user_id)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def clear(self, user_id: str) -> int:
        cursor = self._conn.execute("DELETE FROM user_memories WHERE user_id = ?", (user_id,))
        self._conn.commit()
        return cursor.rowcount

    def relevant_for(self, *, user_id: str, query: str, limit: int = MAX_MEMORIES_IN_CONTEXT) -> list[UserMemory]:
        """Memórias relevantes para a mensagem atual.

        Ranking simples e local (sem embeddings, sem chamada de IA): conta
        palavras em comum entre a mensagem e o conteúdo da memória; empates
        e ausência de sobreposição caem para as mais recentes. Identidade
        tem um pequeno bônus — saber o nome do usuário quase sempre ajuda.

        Deliberadamente burro: o custo precisa ser desprezível, e um ranking
        ruim aqui só significa "contexto um pouco menos útil", nunca um erro
        de correção."""
        memories = self.list_memories(user_id)
        if not memories:
            return []

        query_words = {w for w in _normalize_for_dedup(query).split() if len(w) > 2}

        def score(memory: UserMemory) -> tuple[int, float]:
            memory_words = {w for w in _normalize_for_dedup(memory.content).split() if len(w) > 2}
            overlap = len(query_words & memory_words)
            if memory.category is MemoryCategory.IDENTITY:
                overlap += 1
            return (overlap, memory.updated_at.timestamp())

        ranked = sorted(memories, key=score, reverse=True)
        return ranked[:limit]

    def _enforce_limit(self, user_id: str) -> None:
        total = self._conn.execute(
            "SELECT COUNT(*) AS total FROM user_memories WHERE user_id = ?", (user_id,)
        ).fetchone()["total"]
        if total <= MAX_MEMORIES_PER_USER:
            return
        excess = total - MAX_MEMORIES_PER_USER
        self._conn.execute(
            "DELETE FROM user_memories WHERE id IN ("
            "  SELECT id FROM user_memories WHERE user_id = ? ORDER BY updated_at ASC LIMIT ?"
            ")",
            (user_id, excess),
        )
        self._conn.commit()
        logger.info("Memória do usuário atingiu o teto; %s entrada(s) antiga(s) removida(s).", excess)

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> UserMemory:
        return UserMemory(
            id=row["id"],
            user_id=row["user_id"],
            category=MemoryCategory(row["category"]),
            content=row["content"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            source_conversation_id=row["source_conversation_id"],
        )


def format_memories_for_context(memories: list[UserMemory]) -> str:
    """Renderiza as memórias como texto para o system prompt. Só o conteúdo
    — nunca IDs, timestamps ou nomes de tabela (o modelo não precisa, e
    expor internals é ruído e risco)."""
    if not memories:
        return ""
    lines = [f"- {memory.content}" for memory in memories]
    return "Fatos que o usuário já contou em conversas anteriores:\n" + "\n".join(lines)
