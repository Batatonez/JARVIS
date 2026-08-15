"""`GroqProvider` — Groq (`https://api.groq.com/openai/v1`), OpenAI-compatible.
Terceiro provider da cadeia de fallback, atrás de NVIDIA (v1.4.0, item 11).

Lê `GROQ_API_KEY`. Desativável via `JARVIS_GROQ_ENABLED=0`. Modelos
sobrescrevíveis via `JARVIS_GROQ_MODELS=modelo1,modelo2`.

**Achado real durante o desenvolvimento**: `urllib.request` sem um
`User-Agent` explícito era bloqueado pela Cloudflare na frente da API da
Groq (HTTP 403, "error code: 1010" — bloqueio por assinatura de
user-agent, nada a ver com a API key). Corrigido centralmente em
`services/providers/http_support.py::DEFAULT_USER_AGENT`, aplicado a todos
os providers desta versão.

--------------------------------------------------------------------------
CATÁLOGO — validado por chamada real (item 11: no máximo 2 modelos)
--------------------------------------------------------------------------
`GET /openai/v1/models` desta conta lista 15 modelos; excluindo áudio
(`whisper-large-v3*`), TTS (`canopylabs/orpheus-*`) e classificadores
(`meta-llama/llama-prompt-guard-*`), sobram modelos de chat de verdade.
Validados com uma chamada mínima:

    llama-3.3-70b-versatile   AVAILABLE — resposta imediata, ~390ms,
                               conteúdo visível mesmo com orçamento de
                               tokens mínimo (8 tokens). Pick primário:
                               rápido, confiável, bom equilíbrio geral.
    openai/gpt-oss-20b        AVAILABLE — modelo de raciocínio; com
                               orçamento de 8 tokens o conteúdo veio vazio
                               (gastou tudo em reasoning, o mesmo padrão já
                               documentado para a OpenRouter), mas com o
                               orçamento real de produção (1024) isso não é
                               esperado ser um problema — mantido como
                               secundário por coding/reasoning.
"""

from services.providers.openai_compatible import OpenAICompatibleProvider
from services.providers.types import ProviderId


class GroqProvider(OpenAICompatibleProvider):
    id = ProviderId.GROQ
    label = "Groq"
    env_key_name = "GROQ_API_KEY"
    env_enabled_name = "JARVIS_GROQ_ENABLED"
    env_models_name = "JARVIS_GROQ_MODELS"
    base_url = "https://api.groq.com/openai/v1"
    default_models = (
        "llama-3.3-70b-versatile",
        "openai/gpt-oss-20b",
    )
