# integrations/

Reservado para integrações externas ao JARVIS.

## Status

Ainda não implementado. Nenhuma integração está habilitada.

## Escopo futuro

- Servidores **MCP** (Model Context Protocol).
- APIs externas (ex.: calendário, e-mail, clima, serviços de terceiros).
- Outros serviços externos que o JARVIS venha a consumir.

## Princípio

Cada integração deve ser isolada em seu próprio módulo, com permissões explícitas e mínimas necessárias (ver [`tools/README.md`](../tools/README.md) sobre classificação de risco). Nenhuma integração deve ter acesso amplo por padrão.
