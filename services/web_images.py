"""Imagens relevantes vindas de pesquisa web (v1.3, itens 24-29).

**Escopo honesto.** O JARVIS ainda NÃO tem um `WebSearchService` real — não
existe provider de busca conectado nesta versão, e este módulo não finge que
existe (item 26). O que ele entrega é o que dá para entregar de verdade:

1. `WebImageResult` — o tipo que a UI consome (item 27).
2. `is_visual_subject()` — a decisão de relevância (itens 24-25).
3. `validate_image_url()` — a barreira de segurança (itens 28-29), que é a
   parte que precisa estar pronta ANTES de qualquer provider entrar, não
   depois.
4. `ImageSearchService` — a fronteira abstrata, com uma implementação
   `UnavailableImageSearchService` que devolve "sem imagens" sem erro.

Quando um provider real for plugado, ele implementa `ImageSearchService` e
tudo que já está testado aqui continua valendo. Nenhum scraping frágil de
Google Images (proibido pelo item 26).

--------------------------------------------------------------------------
SSRF e URL insegura (item 28)
--------------------------------------------------------------------------
`validate_image_url()` recusa, ANTES de qualquer requisição:

- esquema que não seja `https` (bloqueia `file:`, `data:`, `javascript:`,
  `ftp:`, e também `http:` em claro);
- `localhost`, `127.0.0.0/8`, `::1`, `0.0.0.0`;
- qualquer IP privado, link-local, loopback, multicast ou reservado
  (RFC 1918, 169.254/16, 100.64/10 CGNAT, IPv6 ULA...);
- host com credenciais embutidas (`https://user:senha@host/`);
- porta fora de 443.

E quando houver fetch de fato, `IMAGE_FETCH_LIMITS` define os tetos que o
provider é obrigado a respeitar: timeout, redirects, MIME, bytes e dimensões.
"""

import ipaddress
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebImageResult:
    """Uma imagem que acompanha uma resposta baseada em pesquisa web.

    `source_url` é a PÁGINA de origem, não o arquivo: clicar na imagem no HUD
    abre a página que a contextualiza (item 27), nunca baixa nem executa nada.
    """

    image_url: str
    thumbnail_url: str
    source_url: str
    source_name: str
    alt_text: str = ""
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class ImageFetchLimits:
    """Tetos que qualquer provider de imagem é obrigado a respeitar.

    Existem como dado (e não espalhados como números mágicos dentro de um
    cliente HTTP) para poderem ser testados e auditados sem rede."""

    timeout_seconds: float = 8.0
    max_redirects: int = 3
    max_bytes: int = 5 * 1024 * 1024
    max_width: int = 4096
    max_height: int = 4096
    allowed_mime_types: frozenset[str] = frozenset(
        {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}
    )


IMAGE_FETCH_LIMITS = ImageFetchLimits()

_ALLOWED_SCHEMES = frozenset({"https"})
_ALLOWED_PORTS = frozenset({443})
_BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
)
# `.local` (mDNS) e `.internal` resolvem para a rede da própria máquina.
_BLOCKED_SUFFIXES = (".local", ".internal", ".localdomain", ".home.arpa")


class UnsafeImageUrlError(ValueError):
    """URL de imagem recusada. A mensagem diz o motivo sem repetir a URL —
    ecoar uma URL hostil de volta para a UI é um vetor por si só."""


# Faixas que o `ipaddress` NÃO classifica como privadas mas que, na prática,
# são rede interna alcançável. `100.64.0.0/10` é o Shared Address Space da
# RFC 6598 (CGNAT), usado por operadoras e por muitos roteadores domésticos:
# `is_private` devolve False para ele, e sem esta lista um `https://100.64.x.x`
# passaria pela validação.
_EXTRA_BLOCKED_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT (RFC 6598)
    ipaddress.ip_network("192.0.0.0/24"),  # IETF Protocol Assignments
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking (RFC 2544)
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64
)


def _host_is_blocked_ip(hostname: str) -> bool:
    """`True` se o host for um IP literal que não pode ser alcançado.

    Só cobre IP LITERAL. Um nome que resolve para IP privado (rebinding por
    DNS) precisa ser barrado no momento da conexão, pelo provider — está
    documentado em `IMAGE_FETCH_LIMITS` e é responsabilidade de quem
    implementar o fetch."""
    try:
        address = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return False
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return True
    return any(address in network for network in _EXTRA_BLOCKED_NETWORKS if address.version == network.version)


def validate_image_url(url: str) -> str:
    """Devolve a URL se ela for segura; senão levanta `UnsafeImageUrlError`.

    Chamada obrigatória antes de a URL chegar ao QML ou a qualquer cliente
    HTTP."""
    candidate = (url or "").strip()
    if not candidate:
        raise UnsafeImageUrlError("URL de imagem vazia.")
    # Caracteres de controle em URL são sinal de tentativa de contrabando de
    # cabeçalho/parser; recusamos antes até de parsear.
    if any(ch.isspace() or not ch.isprintable() for ch in candidate):
        raise UnsafeImageUrlError("URL de imagem contém caracteres inválidos.")

    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise UnsafeImageUrlError("URL de imagem malformada.") from exc

    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeImageUrlError("Só imagens https são aceitas.")
    if parts.username or parts.password:
        raise UnsafeImageUrlError("URL de imagem com credenciais embutidas foi recusada.")

    hostname = (parts.hostname or "").lower()
    if not hostname:
        raise UnsafeImageUrlError("URL de imagem sem host.")
    if hostname in _BLOCKED_HOSTNAMES or hostname.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeImageUrlError("Endereço local não é uma fonte de imagem válida.")
    if _host_is_blocked_ip(hostname):
        raise UnsafeImageUrlError("Endereço de rede interna não é uma fonte de imagem válida.")

    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeImageUrlError("Porta inválida na URL de imagem.") from exc
    if port is not None and port not in _ALLOWED_PORTS:
        raise UnsafeImageUrlError("Porta não permitida para imagem remota.")

    return candidate


def is_safe_image_url(url: str) -> bool:
    try:
        validate_image_url(url)
    except UnsafeImageUrlError:
        return False
    return True


def sanitize_results(results) -> list[WebImageResult]:
    """Filtra uma lista de resultados, descartando (com log) o que não passa
    na validação. Nunca levanta: uma imagem ruim não pode derrubar a resposta
    de texto (item 70)."""
    safe: list[WebImageResult] = []
    for result in results or []:
        try:
            validate_image_url(result.image_url)
            validate_image_url(result.thumbnail_url or result.image_url)
            validate_image_url(result.source_url)
        except UnsafeImageUrlError as exc:
            logger.info("Imagem descartada por política de segurança: %s", exc)
            continue
        if result.width > IMAGE_FETCH_LIMITS.max_width or result.height > IMAGE_FETCH_LIMITS.max_height:
            logger.info("Imagem descartada por dimensões acima do limite.")
            continue
        safe.append(result)
    return safe


# ----------------------------------------------------------------------
# Relevância (itens 24-25)
# ----------------------------------------------------------------------

# Assuntos em que ver ajuda de verdade. Lista de sinais, não de respostas:
# a decisão é "este assunto é visual?", nunca "qual imagem mostrar".
_VISUAL_HINTS = (
    # lugares e arquitetura
    "onde fica", "como é", "como e o", "paisagem", "cidade", "praia", "montanha",
    "edifício", "edificio", "prédio", "predio", "arquitetura", "monumento", "torre",
    "ponte", "castelo", "igreja", "museu", "mapa", "localização", "localizacao",
    # natureza
    "animal", "animais", "bicho", "planta", "flor", "árvore", "arvore", "fruta",
    "espécie", "especie", "raça", "raca",
    # objetos e produtos
    "produto", "modelo", "aparência", "aparencia", "design", "cor de", "formato",
    "peça", "peca", "ferramenta", "carro", "moto",
    # obras
    "pintura", "quadro", "escultura", "obra de arte", "pôster", "poster",
    "foto", "imagem", "fotografia",
)

# Assuntos em que imagem normalmente é ruído. Vence os sinais visuais quando
# os dois aparecem: "como é a sintaxe de..." não pede foto.
_NON_VISUAL_HINTS = (
    "calcule", "calcular", "quanto é", "quanto e ", "some ", "divida", "multiplique",
    "equação", "equacao", "derivada", "integral", "porcentagem",
    "código", "codigo", "função", "funcao", "bug", "erro de", "stack trace",
    "compilar", "sintaxe", "regex", "sql", "api", "python", "javascript",
    "bom dia", "boa tarde", "boa noite", "tudo bem", "obrigado", "valeu",
    "resuma", "resumo", "traduza", "tradução", "traducao", "explique o conceito",
)

_MIN_QUERY_LENGTH = 8


def is_visual_subject(text: str) -> bool:
    """Decide se vale acompanhar a resposta de uma imagem (itens 24-25).

    Deliberadamente CONSERVADOR: na dúvida, `False`. Uma imagem irrelevante
    polui a resposta e custa banda; a ausência dela não quebra nada. Por isso
    os sinais de "não visual" têm precedência sobre os de "visual"."""
    query = (text or "").strip().lower()
    if len(query) < _MIN_QUERY_LENGTH:
        return False
    if any(hint in query for hint in _NON_VISUAL_HINTS):
        return False
    return any(hint in query for hint in _VISUAL_HINTS)


# ----------------------------------------------------------------------
# Markdown não controla imagem remota (item 29)
# ----------------------------------------------------------------------

_MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def strip_markdown_images(text: str) -> str:
    """Remove `![alt](url)` do texto da IA, deixando só o `alt`.

    O item 29 é explícito: a IA **não** pode injetar imagem remota escrevendo
    Markdown. Toda imagem exibida precisa ter passado pelo pipeline validado
    de `WebImageResult`. Sem isto, uma resposta com `![](file:///C:/...)` ou
    `![](http://127.0.0.1:8080/admin)` faria o renderizador do Qt buscar
    aquele recurso — leitura de arquivo local e varredura de porta interna
    disparadas por texto de modelo.

    O `alt` é preservado como texto porque costuma ser a legenda que dá
    sentido à frase; jogar fora deixaria a resposta truncada."""

    def _replace(match: re.Match) -> str:
        alt = (match.group(1) or "").strip()
        return alt

    return _MARKDOWN_IMAGE.sub(_replace, text or "")


# ----------------------------------------------------------------------
# Fronteira do provider
# ----------------------------------------------------------------------


class ImageSearchService(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        """Existe um provider de busca de imagem configurado?"""

    @abstractmethod
    async def search(self, query: str, *, limit: int = 3) -> list[WebImageResult]:
        """Imagens relevantes para a consulta. Deve devolver lista vazia (e
        NUNCA levantar) quando não houver resultado."""


class UnavailableImageSearchService(ImageSearchService):
    """Estado real do JARVIS na v1.3: nenhum provider de busca conectado.

    Existe para o resto do sistema poder chamar a fronteira sem `if` espalhado
    e sem fingir que a pesquisa funciona (item 26)."""

    def is_available(self) -> bool:
        return False

    async def search(self, query: str, *, limit: int = 3) -> list[WebImageResult]:
        return []


def create_image_search_service(settings) -> ImageSearchService:
    """Hoje sempre devolve o placeholder — nenhum provider de busca de imagem
    está configurado nesta versão. A função existe para o ponto de decisão já
    estar no lugar certo quando um provider entrar."""
    return UnavailableImageSearchService()
