"""Nome técnico -> nome legível, para provider e modelo (v1.6.0).

--------------------------------------------------------------------------
Por que este módulo existe
--------------------------------------------------------------------------
Bug real da v1.5.0: o HUD passou a mostrar a rota da última resposta e
exibia o identificador cru:

    ROUTE openai/gpt-oss-20b:free

Isso é um slug de API — namespace de organização, contagem de parâmetros e
um sufixo de faturamento. Nada disso é informação para quem está
conversando com o assistente.

A separação é a regra central: `ProviderRouter`, `env_config`, allowlists de
free-only, logs técnicos e o próprio `.env` continuam falando **só** em IDs
técnicos. Formatar é uma operação de APRESENTAÇÃO, aplicada na fronteira com
a UI — nunca antes, e nunca de volta para dentro. Formatar um ID e usá-lo
para rotear quebraria o roteamento; por isso `format_model_name` só é
chamado por quem monta a view.

Também por isso a formatação **não** vive no QML: um `.replace()` espalhado
por arquivo de interface vira seis regras divergentes, e a primeira delas a
ficar desatualizada mostra um nome errado sem ninguém perceber.

--------------------------------------------------------------------------
Nomes desconhecidos
--------------------------------------------------------------------------
O mapa cobre os modelos que o JARVIS realmente usa. Para qualquer outro, a
derivação genérica limpa o que é claramente ruído (namespace da organização,
sufixo `:free`) e devolve o resto legível — nunca uma string vazia e nunca
um nome inventado. Um modelo novo aparece com um nome razoável no dia em que
entrar no catálogo, sem precisar de código novo.
"""

import re

from services.providers.types import ProviderId

PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    ProviderId.OPENROUTER.value: "OpenRouter",
    ProviderId.NVIDIA.value: "NVIDIA NIM",
    ProviderId.GEMINI.value: "Gemini",
    ProviderId.GROQ.value: "Groq",
    ProviderId.CEREBRAS.value: "Cerebras",
    ProviderId.MISTRAL.value: "Mistral",
    ProviderId.ANTHROPIC.value: "Anthropic",
}

# Nomes curados dos modelos do catálogo real (ver `docs/providers.md`). A
# chave é o ID técnico SEM o sufixo `:free`, porque o mesmo modelo aparece
# com e sem ele conforme o provider.
MODEL_DISPLAY_NAMES: dict[str, str] = {
    "openai/gpt-oss-20b": "GPT-OSS 20B",
    "openai/gpt-oss-120b": "GPT-OSS 120B",
    "nvidia/nemotron-3-ultra-550b-a55b": "Nemotron 3 Ultra",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5": "Nemotron Super 49B",
    "z-ai/glm-5.2": "GLM 5.2",
    "moonshotai/kimi-k2.6": "Kimi K2.6",
    "deepseek/deepseek-r1": "DeepSeek R1",
    "meta-llama/llama-3.3-70b-instruct": "Llama 3.3 70B",
    "llama-3.3-70b-versatile": "Llama 3.3 70B",
    "gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini-3.5-flash-lite": "Gemini 3.5 Flash Lite",
    "mistralai/mistral-medium-3.5-128b": "Mistral Medium 3.5",
    "mistral-medium-latest": "Mistral Medium",
    "mistral-small-latest": "Mistral Small",
    "qwen/qwen3-235b-a22b": "Qwen3 235B",
}

# Siglas que devem ficar em caixa alta na derivação genérica — sem isto,
# `glm-5.2` viraria "Glm 5.2".
_UPPERCASE_TOKENS = frozenset(
    {"gpt", "glm", "llm", "moe", "ai", "oss", "r1", "v1", "v2", "v3", "vl", "sd", "tts", "stt"}
)
_SIZE_SUFFIX = re.compile(r"^\d+(\.\d+)?[bkm]$", re.IGNORECASE)


def format_provider_name(provider_id: str | ProviderId | None) -> str:
    """`"nvidia"` -> `"NVIDIA NIM"`. Um provider desconhecido volta
    capitalizado, nunca vazio."""
    if provider_id is None:
        return ""
    raw = provider_id.value if isinstance(provider_id, ProviderId) else str(provider_id)
    raw = raw.strip()
    if not raw:
        return ""
    return PROVIDER_DISPLAY_NAMES.get(raw, raw.replace("-", " ").replace("_", " ").title())


def _titleize_token(token: str) -> str:
    lowered = token.lower()
    if lowered in _UPPERCASE_TOKENS:
        return lowered.upper()
    if _SIZE_SUFFIX.match(lowered):
        # "20b" -> "20B" (contagem de parâmetros, convenção estabelecida).
        return lowered.upper()
    if any(char.isdigit() for char in lowered):
        # "3.5", "k2.6": versão/geração — mexer na caixa só estragaria.
        return token
    return lowered.capitalize()


def format_model_name(model_id: str | None) -> str:
    """`"openai/gpt-oss-20b:free"` -> `"GPT-OSS 20B"`.

    Nunca devolve o ID cru com namespace e sufixo de faturamento, e nunca
    devolve string vazia para uma entrada não vazia — o HUD sempre tem algo
    que uma pessoa consegue ler."""
    if not model_id:
        return ""
    raw = str(model_id).strip()
    if not raw:
        return ""

    # `:free`/`:nitro` são sufixos de ROTA (faturamento/prioridade), não parte
    # do nome do modelo. Removidos antes de procurar no mapa para que o mesmo
    # modelo case com ou sem eles.
    without_route = raw.split(":", 1)[0]
    if without_route in MODEL_DISPLAY_NAMES:
        return MODEL_DISPLAY_NAMES[without_route]

    # Derivação genérica: descarta o namespace da organização (`openai/`,
    # `nvidia/`) — ele identifica quem publicou, não o que o modelo é.
    bare = without_route.rsplit("/", 1)[-1]
    tokens = [token for token in re.split(r"[-_\s]+", bare) if token]
    if not tokens:
        return without_route
    return " ".join(_titleize_token(token) for token in tokens)


def format_route(provider_id: str | ProviderId | None, model_id: str | None) -> str:
    """Rótulo curto de uma linha para o HUD: `"NVIDIA NIM · Nemotron 3 Ultra"`.
    Devolve só a parte que existir."""
    provider = format_provider_name(provider_id)
    model = format_model_name(model_id)
    if provider and model:
        return f"{provider} · {model}"
    return provider or model
