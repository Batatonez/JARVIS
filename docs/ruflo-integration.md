# Integração com o Ruflo

> **Status: ferramenta de desenvolvimento/infraestrutura, não parte do app final.** O Ruflo (pacote npm `ruflo`, rebrand de `claude-flow`, `github.com/ruvnet/claude-flow`) foi instalado localmente para orquestração de agentes durante o desenvolvimento do JARVIS. Usuários finais do JARVIS **não** precisam instalar Node/npm/Ruflo/Claude Code — nada neste documento afeta o empacotamento do app (ver item 30 do pedido que originou este documento).

## Fronteira: Ruflo coordena, JARVIS decide modelo

```
RufloCoordinator (services/providers/ruflo_coordinator.py)
    │
    ├── create_swarm / register_role / distribute_task   ← PLANEJADO, não conectado
    └── get_state                                          ← real, leitura local só-leitura

ProviderRouter (services/providers/router.py)
    └── única autoridade sobre provider/modelo/custo — o Ruflo NUNCA decide isso
```

`RufloCoordinator` é uma interface (`ABC`) para responsabilidades de coordenação — criar swarm, registrar roles, distribuir tarefas, acompanhar estado, usar memory/coordination do Ruflo. **Nunca** tem autoridade sobre provider/modelo — essa é exclusiva do `ProviderRouter` (ver `docs/providers.md`).

`LocalRufloCoordinator` (a única implementação concreta nesta etapa) só lê o estado que o daemon do Ruflo já escreve em `.claude-flow/agents/store.json` — sem subprocess, sem MCP, sem rede. `create_swarm`/`register_role`/`distribute_task` levantam `NotYetImplementedError` deliberadamente: **nenhuma chamada de IA do JARVIS passa por `agent_execute` do Ruflo nesta etapa** (ver bug abaixo). Continuam disponíveis para experimentos de desenvolvimento fora do caminho de produção.

## Bug confirmado: `agent_execute` ignora `provider`/`openrouterModel`

Investigado e testado em sessões anteriores (não repetido aqui — ver histórico). Resumo:

1. `agent_spawn` (via `determineAgentModel()`, tier-2/router) **pode** registrar corretamente `agent.provider = "openrouter"` e `agent.openrouterModel = "openrouter/free"` no registro do agente — confirmado com um agente de teste real, usando `CLAUDE_FLOW_ROUTER_OPENROUTER_ALTS` para influenciar a decisão.
2. Porém `executeAgentTask()` (o código por trás de `agent_execute`) só lê `agent.modelId` para decidir o que chamar — **nunca lê `agent.provider` nem `agent.openrouterModel`**. Como o router de tier-2 usado no passo 1 nunca preenche `modelId` (só `provider`/`openrouterModel`), a execução cai no caminho padrão: resolve `agent.model` (ex.: `"sonnet"`) via `MODEL_MAP` para um ID nativo Anthropic (`"claude-sonnet-5"`), que a OpenRouter interpreta como `anthropic/claude-sonnet-5` — uma rota potencialmente paga, mesmo com `openrouter/free` pedido.
3. Também não existe hoje uma combinação de variáveis de ambiente/configuração pública que force `openrouter/free` de ponta a ponta em `agent_execute` sem editar o pacote instalado — confirmado com um dry-run 100% local (sem rede, sem `OPENROUTER_API_KEY`/`ANTHROPIC_API_KEY` no processo) que reproduziu exatamente esse comportamento (`executeAgentTask()` resolveu para `"claude-sonnet-5"` mesmo com o workaround de configuração aplicado).

**Por isso**: o JARVIS nunca depende do model routing interno do Ruflo. `ProviderRouter` (implementado nesta etapa) é quem decide provider/modelo/custo; quando o Ruflo for usado para coordenação de verdade, a chamada de IA em si continuará passando pelo `ProviderRouter`, nunca por `agent_execute`.

**Não foi patchado** — nem `node_modules`, nem cache do `npx`, nem fork. O JARVIS só se protege não dependendo desse caminho.

## Estado local do Ruflo — classificação para o Git

Auditoria feita antes do primeiro commit desta branch:

| Caminho | Classificação | Motivo |
|---|---|---|
| `.claude-flow/` | **IGNORE** (`.gitignore`) | Runtime do daemon: agentes, métricas, políticas, logs, PID, estado de swarm. Regenerável, específico da máquina, nunca reproduzível entre ambientes. |
| `.swarm/` | **IGNORE** (`.gitignore`) | SQLite/AgentDB (memória vetorial), backups automáticos, estado do bandit de roteamento. Mesmo raciocínio de banco de dados local do JARVIS (`data/`). |
| `.claude/` | **IGNORE** (`.gitignore`) | Pacote de agentes/skills/commands/hooks instalado automaticamente pelo `ruflo init` — não autorado pelo JARVIS. Inclui hooks ativos (`PreToolUse`/`PostToolUse`/`SessionStart`/`Stop`/...) apontando para scripts não auditados em `.claude/helpers/` (entre eles um `auto-commit.sh`). Ver "Efeito colateral encontrado" abaixo. |
| `.mcp.json` | **TRACK** | Vazio (`{"mcpServers": {}}`), sem segredo, configuração reproduzível padrão de um projeto Claude Code. |
| `claude-flow.config.json` | **TRACK** | Configuração reproduzível (topologia de swarm, backend de memória, etc.) sem segredos e sem caminho específico desta máquina. |
| `CLAUDE.before-ruflo.md` | **IGNORE** (não versionado) | Backup local explícito, comparado byte-a-byte contra `CLAUDE.md` atual — **idênticos**, nenhuma regra do projeto foi alterada pela instalação do Ruflo. Nada a fundir. |

## Efeito colateral encontrado (não corrigido nesta etapa)

`.claude/settings.json` (instalado pelo Ruflo) registra hooks do Claude Code para `PreToolUse`/`PostToolUse`/`UserPromptSubmit`/`SessionStart`/`SessionEnd`/`Stop`/`PreCompact`/`SubagentStart`/`SubagentStop`/`Notification`, todos apontando para `.claude/helpers/hook-handler.cjs`/`auto-memory-hook.mjs` — **esses hooks estão ativos nesta sessão** (explica os banners `Microsoft Windows [versão ...]` que aparecem nos `system-reminder` de `SessionStart`/`UserPromptSubmit`). Também define `permissions.deny: ["Read(./.env)", "Read(./.env.*)"]`, que bloqueou a leitura de `.env.example` por todas as ferramentas disponíveis nesta sessão (Read, Bash, PowerShell, Grep) — por isso `.env.example` não pôde ser atualizado com `OPENROUTER_API_KEY=` (ver `docs/providers.md`, seção Secrets).

Nenhuma ação foi tomada sobre isso (fora de escopo desta tarefa) — registrado aqui para decisão deliberada futura: revisar o conteúdo de `.claude/helpers/*.sh`/`*.cjs`/`*.mjs` (em especial `auto-commit.sh`) antes de decidir manter os hooks ativos, desativá-los, ou adotar seletivamente.

## Smoke test manual (opcional) — `openrouter/free`

Só rode isto se `OPENROUTER_API_KEY` já estiver no ambiente da sessão **e** você autorizar explicitamente antes. Nunca automatizado, nunca chamado por `pytest`.

O que seria chamado, exatamente:

```python
import asyncio
from services.providers import ProviderRouter, RouteRequest, build_default_registry

async def main():
    router = ProviderRouter(build_default_registry())
    result = await router.execute(RouteRequest(prompt="Responda apenas: JARVIS FREE ROUTER OK", free_only=True))
    print(result)

asyncio.run(main())
```

Isso envia **uma** requisição HTTP real para `https://openrouter.ai/api/v1/chat/completions` com `model="openrouter/free"`. Se a resposta não confirmar custo zero (`served_model`/`cost`), `router.execute()` levanta `NoFreeModelAvailableError` em vez de devolver como sucesso — ver `docs/providers.md`, seção free-only.

## Limitações conhecidas (não endereçadas nesta etapa)

- **Serena MCP** — apareceu com falha de conexão anteriormente; não foi investigado nem corrigido aqui (fora de escopo).
- **ONNX/HNSW** — o wizard do Ruflo já causou uma falha de alocação de memória (~4.4 GB); o cache HNSW foi ajustado para `128` e o wizard **não** foi executado novamente nesta etapa.
- **333 MCP tools / ~61.550 tokens de schema** — registrado como limitação futura ("MCP schema optimization"), não resolvido aqui.
- **Headroom** — já conectado ao Claude Code; configuração não foi alterada.
