# config/

Configurações do JARVIS.

## Status: implementado (JARVIS Backend v0.4)

- **`settings.py`** — `Settings`: nome do app, versão do Core, caminhos importantes (memória, logs), limite de histórico de conversa (`max_conversation_messages`) e configuração do provider de IA (`anthropic_api_key`, `agent_model`, lidos de variáveis de ambiente). Caminhos são sempre derivados de `PROJECT_ROOT` (calculado a partir da localização do próprio arquivo) — nada é hardcoded para uma máquina específica.
- **`logging_config.py`** — configura logging da biblioteca padrão: console mostra apenas avisos/erros, arquivo (`logs/jarvis.log`, ignorado pelo Git) registra tudo em nível INFO+ para desenvolvimento.

## Variáveis de ambiente (ver [`.env.example`](../.env.example))

| Variável | Obrigatória | Efeito |
|---|---|---|
| `ANTHROPIC_API_KEY` | Não | Se presente, `create_ai_service()` constrói o `ClaudeAgentProvider` (quem lê o valor de fato, ao conectar, é o próprio Agent SDK); se ausente, usa `UnavailableAIService` (fallback seguro, sem erro). |
| `JARVIS_AGENT_MODEL` | Não | Modelo usado pelo `ClaudeAgentProvider` — aceita os aliases do Agent SDK (`sonnet`, `opus`, `haiku`) ou um nome de modelo completo (padrão: `sonnet`). |
| `JARVIS_MAX_CONVERSATION_MESSAGES` | Não | Quantas mensagens o histórico de conversa em runtime mantém antes de descartar as mais antigas (padrão: `200`). Só afeta `app/conversation.py`, nunca `memory/`. |

## Regras

- **Nunca** armazenar segredos (senhas, tokens, API keys) diretamente neste repositório — `ANTHROPIC_API_KEY` só existe via variável de ambiente, nunca com valor default no código.
- Use um arquivo `.env` local (baseado em [`.env.example`](../.env.example)) ou defina as variáveis diretamente no ambiente — `.env` já está no `.gitignore`. O Agent SDK não carrega `.env` sozinho.
- Nenhuma informação sensível deve ser registrada em log — `services/claude_agent_provider.py` só loga tipo/status de erro, nunca a chave ou o corpo de uma resposta.
- Nunca pedir, solicitar ou tentar obter uma API key do usuário durante o desenvolvimento — trabalhar sem IA real conectada é o estado normal deste projeto (ver [`CLAUDE.md`](../CLAUDE.md)).
