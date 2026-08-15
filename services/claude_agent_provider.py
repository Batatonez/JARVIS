"""ClaudeAgentProvider: implementação real de AIService usando o Claude Agent SDK
(pacote `claude-agent-sdk`, módulo `claude_agent_sdk`).

Único módulo do projeto que importa o SDK — o resto do JARVIS conhece apenas
a abstração `AIService`. Nenhuma ferramenta do agente fica habilitada nesta
versão: `allowed_tools=[]` e um callback `can_use_tool` que nega tudo agem
como duas camadas independentes de defesa (ver `_deny_all_tools`), e
`setting_sources=[]` evita herdar configuração pessoal do Claude Code
instalado na máquina (`~/.claude`, skills/hooks/MCPs pessoais, etc.).
"""

import logging
from typing import NoReturn

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    PermissionResultDeny,
    ProcessError,
    ResultMessage,
    TextBlock,
)

from services.ai_service import AIService, AIServiceUnavailableError
from services.runtime_identity import build_system_prompt

logger = logging.getLogger(__name__)

# Mensagens seguras para o usuário a partir do `AssistantMessage.error` /
# `ResultMessage.subtype` do SDK — nunca incluem detalhes técnicos ou segredos.
_AI_ERROR_MESSAGES = {
    "authentication_failed": "Falha de autenticação com o serviço de inteligência.",
    "billing_error": "Problema de cobrança no serviço de inteligência.",
    "rate_limit": "O serviço de inteligência está sobrecarregado. Tente novamente em instantes.",
    "invalid_request": "A solicitação enviada ao serviço de inteligência foi rejeitada.",
    "server_error": "O serviço de inteligência retornou um erro interno.",
    "unknown": "Ocorreu um erro inesperado no serviço de inteligência.",
}


class ClaudeAgentProviderError(AIServiceUnavailableError):
    """Levantada quando a sessão do Claude Agent SDK falha."""


async def _deny_all_tools(tool_name: str, input_data: dict, context: object) -> PermissionResultDeny:
    """`can_use_tool` que nega qualquer ferramenta — segunda camada de defesa
    além de `allowed_tools=[]`. Uma conversa normal nunca deveria chegar aqui."""
    return PermissionResultDeny(
        message="Ferramentas estão desabilitadas nesta versão do JARVIS.",
        interrupt=True,
    )


class ClaudeAgentProvider(AIService):
    def __init__(self, *, model: str, cwd: str, client: ClaudeSDKClient | None = None) -> None:
        self._model = model
        self._cwd = cwd
        # `client` é aceito para injeção em testes (evita processos reais);
        # em uso normal, o provider cria o ClaudeSDKClient em start().
        self._injected_client = client
        self._client: ClaudeSDKClient | None = None

    def is_available(self) -> bool:
        return True

    @property
    def session_active(self) -> bool:
        return self._client is not None

    @property
    def backend_name(self) -> str:
        return "Claude Agent SDK"

    def _build_options(self, memory_context: str) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            system_prompt=build_system_prompt(memory_context),
            model=self._model,
            cwd=self._cwd,
            allowed_tools=[],
            disallowed_tools=[],
            permission_mode="default",
            can_use_tool=_deny_all_tools,
            mcp_servers={},
            setting_sources=[],  # não herda ~/.claude, .claude/settings.json etc.
        )

    async def start(self, *, memory_context: str = "", preferences=None) -> None:
        if self._client is not None:
            return  # sessão já ativa: idempotente, não reconecta

        client = self._injected_client or ClaudeSDKClient(options=self._build_options(memory_context))
        try:
            await client.connect()
        except Exception as exc:
            _raise_friendly(exc, phase="conectar")
        self._client = client

    async def ask(self, message: str) -> str:
        if self._client is None:
            raise ClaudeAgentProviderError("A sessão de IA não foi iniciada.")

        text_parts: list[str] = []
        error_code: str | None = None
        try:
            await self._client.query(message)
            async for msg in self._client.receive_response():
                if isinstance(msg, AssistantMessage):
                    if msg.error:
                        error_code = msg.error
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                elif isinstance(msg, ResultMessage) and msg.is_error and not text_parts:
                    error_code = error_code or msg.subtype or "unknown"
        except Exception as exc:
            _raise_friendly(exc, phase="conversar com")

        if error_code:
            logger.error("Agent SDK retornou erro: %s", error_code)
            raise ClaudeAgentProviderError(
                _AI_ERROR_MESSAGES.get(error_code, _AI_ERROR_MESSAGES["unknown"])
            )

        text = "".join(text_parts).strip()
        if not text:
            raise ClaudeAgentProviderError("O serviço de inteligência não retornou nenhuma resposta.")
        return text

    async def close(self) -> None:
        if self._client is None:
            return  # já encerrado: idempotente
        try:
            await self._client.disconnect()
        except Exception:
            logger.exception("Erro ao encerrar a sessão do Agent SDK.")
        finally:
            self._client = None


def _raise_friendly(exc: Exception, *, phase: str) -> NoReturn:
    if isinstance(exc, CLINotFoundError):
        logger.error("Claude Code CLI não encontrado (necessário para o Agent SDK).")
        raise ClaudeAgentProviderError(
            "O backend de IA (Claude Agent SDK) não está instalado corretamente."
        ) from exc
    if isinstance(exc, CLIConnectionError):
        logger.error("Falha de conexão com o processo do Agent SDK ao %s.", phase)
        raise ClaudeAgentProviderError(
            "Não foi possível conectar ao serviço de inteligência."
        ) from exc
    if isinstance(exc, ProcessError):
        logger.error("Processo do Agent SDK falhou (exit code %s).", exc.exit_code)
        raise ClaudeAgentProviderError(
            "O serviço de inteligência encerrou de forma inesperada."
        ) from exc
    if isinstance(exc, CLIJSONDecodeError):
        logger.error("Falha ao interpretar a resposta do Agent SDK.")
        raise ClaudeAgentProviderError(
            "Ocorreu um erro inesperado ao processar a resposta do serviço de inteligência."
        ) from exc
    if isinstance(exc, ClaudeSDKError):
        logger.error("Erro inesperado do Agent SDK: %s", type(exc).__name__)
        raise ClaudeAgentProviderError(
            "Ocorreu um erro inesperado no serviço de inteligência."
        ) from exc
    logger.error("Erro inesperado ao %s o Agent SDK: %s", phase, type(exc).__name__)
    raise ClaudeAgentProviderError(
        "Ocorreu um erro inesperado ao falar com o serviço de inteligência."
    ) from exc
