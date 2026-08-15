"""`CerebrasProvider` — Cerebras (`https://api.cerebras.ai/v1`),
OpenAI-compatible. Quarto provider da cadeia (v1.4.0, item 12).

Lê `CEREBRAS_API_KEY`. Desativável via `JARVIS_CEREBRAS_ENABLED=0`. Modelos
sobrescrevíveis via `JARVIS_CEREBRAS_MODELS=modelo1,modelo2`.

--------------------------------------------------------------------------
CATÁLOGO — validado por chamada real, LIMITAÇÃO HONESTA (item 12)
--------------------------------------------------------------------------
`GET /v1/models` desta conta lista apenas 3 modelos de chat: `gemma-4-31b`,
`gpt-oss-120b`, `zai-glm-4.7`. Uma chamada mínima em CADA UM devolveu:

    HTTP 402 {"type": "payment_required_error", "code": "payment_required"}

Ou seja: **nenhum modelo Cerebras pôde ser positivamente confirmado como
utilizável de graça com a key desta conta** — o plano associado a esta
`CEREBRAS_API_KEY` não tem quota gratuita disponível para nenhum dos três.

Isto NÃO é tratado como bug: `raise_for_status` (em `http_support.py`)
classifica HTTP 402 como `CapacityExhaustedError`, um erro **recuperável** —
o `ProviderRouter` avança para o próximo modelo e, quando os dois se
esgotam do mesmo jeito, para o próximo provider (Mistral), sem nunca
oferecer pagar automaticamente. Comportamento correto e testado
(`tests/test_provider_router_v14.py::CerebrasPaymentRequiredTests`).

Os dois modelos abaixo continuam como default porque a arquitetura do
provider é suportada e correta — outra conta Cerebras com quota gratuita
disponível usaria exatamente este mesmo código com sucesso. Ver o relatório
desta versão para a limitação documentada explicitamente.
"""

from services.providers.openai_compatible import OpenAICompatibleProvider
from services.providers.types import ProviderId


class CerebrasProvider(OpenAICompatibleProvider):
    id = ProviderId.CEREBRAS
    label = "Cerebras"
    env_key_name = "CEREBRAS_API_KEY"
    env_enabled_name = "JARVIS_CEREBRAS_ENABLED"
    env_models_name = "JARVIS_CEREBRAS_MODELS"
    base_url = "https://api.cerebras.ai/v1"
    default_models = (
        "gpt-oss-120b",
        "zai-glm-4.7",
    )
