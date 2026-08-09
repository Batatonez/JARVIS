"""ClaudeProvider: implementação real de AIService usando a API da Anthropic.

Este é o único módulo do projeto que importa o SDK oficial (`anthropic`) —
o resto do JARVIS conhece apenas a abstração `AIService`. Nenhuma chamada de
API acontece fora deste arquivo.
"""

import logging

import anthropic

from services.ai_service import AIService, AIServiceUnavailableError

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Você é o JARVIS, um assistente pessoal em desenvolvimento. Responda de "
    "forma direta e natural, em português do Brasil por padrão. Nesta versão "
    "você é apenas um núcleo de conversação por terminal: não tem voz, não "
    "tem interface gráfica/HUD, não executa ações no computador do usuário, "
    "não usa ferramentas ou MCP, e não guarda memória de conversas "
    "anteriores. Não afirme ter nenhuma dessas capacidades."
)


class ClaudeProviderError(AIServiceUnavailableError):
    """Levantada quando a chamada à API da Anthropic falha."""


class ClaudeProvider(AIService):
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float,
        max_tokens: int,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        # `client` é aceito para injeção em testes (evita chamadas reais);
        # em uso normal, o próprio provider cria o client da SDK.
        self._client = client or anthropic.Anthropic(api_key=api_key, timeout=timeout)

    def is_available(self) -> bool:
        return True

    def ask(self, message: str) -> str:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": message}],
            )
        except anthropic.APIStatusError as exc:
            logger.error(
                "Erro da API da Anthropic (status %s, tipo %s)",
                exc.status_code,
                type(exc).__name__,
            )
            raise ClaudeProviderError(
                "O serviço de inteligência retornou um erro. Tente novamente em instantes."
            ) from exc
        except anthropic.APIConnectionError as exc:
            logger.error("Falha de conexão com a API da Anthropic: %s", type(exc).__name__)
            raise ClaudeProviderError(
                "Não foi possível conectar ao serviço de inteligência. Verifique sua conexão."
            ) from exc
        except anthropic.AnthropicError as exc:
            logger.error("Erro inesperado do SDK da Anthropic: %s", type(exc).__name__)
            raise ClaudeProviderError(
                "Ocorreu um erro inesperado ao falar com o serviço de inteligência."
            ) from exc

        return _extract_text(response)


def _extract_text(response: object) -> str:
    blocks = getattr(response, "content", [])
    parts = [block.text for block in blocks if getattr(block, "type", None) == "text"]
    text = "".join(parts).strip()
    if not text:
        raise ClaudeProviderError("O serviço de inteligência não retornou nenhuma resposta.")
    return text
