"""Bootstrap da suíte de testes — roda ANTES de qualquer módulo de teste.

Existe por um motivo de segurança concreto, descoberto na v1.1: quando o
carregamento automático do `.env` foi adicionado (`config/env_loader.py`), a
suíte passou a enxergar as credenciais REAIS do desenvolvedor
(`OPENROUTER_API_KEY`, `JARVIS_SMTP_*`). Isso quebrou vários testes que
assumiam "nenhum provider configurado" — mas o problema de verdade não era o
teste quebrado: era a suíte poder tocar chave real, gastar requisição ou
enviar e-mail de verdade.

Aqui o ambiente de teste é tornado **hermético**, de duas formas:

1. `JARVIS_DISABLE_DOTENV=1` — o loader ignora o `.env` do projeto.
2. As variáveis sensíveis são removidas do processo, cobrindo também o caso
   de o desenvolvedor tê-las exportado no próprio shell.

Testes que precisam de credencial usam valores fake explícitos; testes que
precisam de `.env` criam um temporário (ver `tests/test_env_loader.py`).
"""

import os

# 1. Nunca carregar o `.env` do projeto durante os testes.
os.environ["JARVIS_DISABLE_DOTENV"] = "1"

# 2. Nunca herdar credencial real do ambiente de quem roda a suíte.
_SENSITIVE_ENV_VARS = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "NVIDIA_API_KEY",
    "OLLAMA_API_KEY",
    "JARVIS_SMTP_HOST",
    "JARVIS_SMTP_PORT",
    "JARVIS_SMTP_USERNAME",
    "JARVIS_SMTP_PASSWORD",
    "JARVIS_SMTP_USE_TLS",
    "JARVIS_EMAIL_FROM",
    # Configuração de comportamento que mudaria o resultado dos testes.
    "JARVIS_FREE_ONLY",
    "JARVIS_PROVIDER_MAX_TOKENS",
    "JARVIS_PROVIDER_TIMEOUT_S",
    "JARVIS_MAX_CONVERSATION_MESSAGES",
    "JARVIS_MAX_MEMORY_CONTEXT_CHARS",
    "JARVIS_AGENT_MODEL",
    "JARVIS_STT_MODEL_PATH",
    "JARVIS_VOICE_INPUT",
    "JARVIS_VOICE_OUTPUT",
    "JARVIS_TTS_VOICE",
    "JARVIS_DEV",
)

for _name in _SENSITIVE_ENV_VARS:
    os.environ.pop(_name, None)
