"""Busca web — interface, e a implementação honesta de que ela não existe.

--------------------------------------------------------------------------
Estado real, dito sem rodeio
--------------------------------------------------------------------------
**O JARVIS não tem busca web.** Nenhum provedor de busca está integrado, e
esta versão não integra nenhum. `services/web_images.py` busca IMAGENS com
proteção contra SSRF; não é pesquisa de texto e não serve para isto.

Este módulo existe por um motivo específico: a camada de precisão precisa
poder DECIDIR que uma pergunta exige evidência externa, PEDIR essa evidência,
e receber de volta uma resposta clara de "não disponível" — para então
admitir isso ao usuário.

A alternativa seria o comportamento que estamos corrigindo: decidir que
precisa pesquisar e, na falta de ferramenta, deixar o modelo preencher a
lacuna. Foi assim que "el ninho" virou chocolate.

--------------------------------------------------------------------------
Por que não implementei scraping
--------------------------------------------------------------------------
Raspar resultado de buscador é frágil (quebra quando o HTML muda), costuma
violar os termos de uso do serviço, e é detectado e bloqueado. Montar isso
para poder marcar a funcionalidade como pronta produziria uma busca que
falha em silêncio — pior que não ter busca, porque cria a expectativa de que
existe.

Quando houver uma API de busca de verdade (com chave, contrato e limites
conhecidos), ela implementa `WebSearchService` e tudo acima passa a
funcionar sem mudança na camada de precisão.

--------------------------------------------------------------------------
A regra que isto garante
--------------------------------------------------------------------------
`SEARCHING_WEB` só aparece no Activity Trace se `search()` de uma
implementação DISPONÍVEL for chamado. Como a implementação padrão declara
`is_available() == False`, o caminho de busca nem é iniciado — e a
interface, portanto, nunca mostra "Pesquisando na web" sem pesquisa.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 5


@dataclass(frozen=True)
class WebSearchResult:
    """Um resultado bruto de busca.

    Ainda NÃO é uma fonte: vira `SourceReference` só ao passar por
    `SourceRegistry.register`, que valida a URL. Manter os dois tipos
    separados é o que impede um resultado malformado de virar link clicável
    na interface."""

    title: str
    url: str
    snippet: str = ""
    published_at: datetime | None = None
    metadata: dict = field(default_factory=dict)


class SearchUnavailableError(Exception):
    """Busca pedida sem implementação disponível.

    Não é um erro de programação — é o estado normal deste projeto hoje.
    Quem chama trata admitindo a limitação ao usuário."""


@runtime_checkable
class WebSearchService(Protocol):
    """Contrato de qualquer backend de busca.

    `Protocol` e não classe base: um backend futuro pode ser um cliente de
    API que não tem por que herdar nada daqui."""

    def is_available(self) -> bool:
        """Há uma busca REAL configurada e utilizável agora?

        Consultado antes de cada uso. É este booleano que decide se a
        atividade "Pesquisando na web" pode sequer começar."""
        ...

    async def search(self, query: str, *, max_results: int = DEFAULT_MAX_RESULTS) -> list[WebSearchResult]:
        """Executa a busca. Levanta `SearchUnavailableError` se indisponível."""
        ...


class UnavailableWebSearch:
    """A implementação padrão — e, hoje, a única real.

    Existe para que o resto do sistema tenha um objeto com o qual falar. Ela
    é honesta em vez de vazia: `is_available()` devolve `False`, e `search()`
    levanta em vez de devolver lista vazia.

    A diferença importa. Lista vazia significaria "pesquisei e não achei
    nada" — uma afirmação falsa sobre o que aconteceu. A exceção significa
    "não pesquisei", que é a verdade."""

    reason = "Nenhum serviço de busca web está configurado."

    def is_available(self) -> bool:
        return False

    async def search(self, query: str, *, max_results: int = DEFAULT_MAX_RESULTS) -> list[WebSearchResult]:
        raise SearchUnavailableError(self.reason)


def create_web_search_service() -> WebSearchService:
    """A busca configurada neste ambiente.

    Hoje devolve sempre `UnavailableWebSearch`. Quando existir um backend,
    é aqui que ele é escolhido — pelo mesmo padrão de
    `services/ai_service.py::create_ai_service`, que já resolve provider por
    configuração e cai para um fallback seguro."""
    return UnavailableWebSearch()
