# config/

Configurações do JARVIS.

## Status: implementado (JARVIS Core v0.2)

- **`settings.py`** — `Settings`: nome do app, versão do Core, caminhos importantes (memória, logs) e configuração do provider de IA (`anthropic_api_key`, `anthropic_model`, `anthropic_timeout`, `anthropic_max_tokens`, todos lidos de variáveis de ambiente). Caminhos são sempre derivados de `PROJECT_ROOT` (calculado a partir da localização do próprio arquivo) — nada é hardcoded para uma máquina específica.
- **`logging_config.py`** — configura logging da biblioteca padrão: console mostra apenas avisos/erros, arquivo (`logs/jarvis.log`, ignorado pelo Git) registra tudo em nível INFO+ para desenvolvimento.

## Variáveis de ambiente (ver [`.env.example`](../.env.example))

| Variável | Obrigatória | Efeito |
|---|---|---|
| `ANTHROPIC_API_KEY` | Não | Se presente, `create_ai_service()` conecta o `ClaudeProvider`; se ausente, usa `UnavailableAIService` (fallback seguro, sem erro). |
| `JARVIS_ANTHROPIC_MODEL` | Não | Modelo usado pelo `ClaudeProvider` (padrão: `claude-sonnet-5`). |
| `JARVIS_ANTHROPIC_TIMEOUT` | Não | Timeout em segundos para chamadas à API (padrão: `30`). |
| `JARVIS_ANTHROPIC_MAX_TOKENS` | Não | Máximo de tokens de saída por resposta (padrão: `1024`). |

## Regras

- **Nunca** armazenar segredos (senhas, tokens, API keys) diretamente neste repositório — `ANTHROPIC_API_KEY` só existe via variável de ambiente, nunca com valor default no código.
- Use um arquivo `.env` local (baseado em [`.env.example`](../.env.example)) ou defina as variáveis diretamente no ambiente — `.env` já está no `.gitignore`.
- Nenhuma informação sensível deve ser registrada em log — `services/claude_provider.py` só loga tipo/status de erro, nunca a chave ou o corpo da requisição.
