# config/

Reservado para configurações do JARVIS (não confundir com `.claude/`, que é configuração específica do Claude Code).

## Status

Ainda não implementado.

## Regras

- **Nunca** armazenar segredos (senhas, tokens, API keys) diretamente neste repositório.
- Quando integrações e APIs forem adicionadas futuramente, usar variáveis de ambiente (arquivo `.env`, já coberto pelo `.gitignore`) ou outro mecanismo apropriado de gerenciamento de segredos — nunca hardcoded em arquivos versionados.
- Configurações não sensíveis (ex.: preferências de comportamento padrão, flags de funcionalidades) podem ficar aqui em formato simples (ex.: `.json`, `.yaml`, `.toml`) quando essa necessidade surgir.
