# config/

Configurações do JARVIS.

## Status: implementado (básico, JARVIS Core v0.1)

- **`settings.py`** — `Settings`: nome do app, versão do Core, e caminhos importantes (memória, logs), todos derivados de `PROJECT_ROOT` (calculado a partir da localização do próprio arquivo) — nada é hardcoded para uma máquina específica.
- **`logging_config.py`** — configura logging da biblioteca padrão: console mostra apenas avisos/erros, arquivo (`logs/jarvis.log`, ignorado pelo Git) registra tudo em nível INFO+ para desenvolvimento.

## Regras

- **Nunca** armazenar segredos (senhas, tokens, API keys) diretamente neste repositório.
- Quando integrações e APIs forem adicionadas futuramente, usar variáveis de ambiente (arquivo `.env`, já coberto pelo `.gitignore`) ou outro mecanismo apropriado de gerenciamento de segredos — nunca hardcoded em arquivos versionados.
- Nenhuma informação sensível deve ser registrada em log.
