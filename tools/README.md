# tools/

Reservado para as ferramentas que o JARVIS poderá usar para interagir com o computador e outros sistemas.

## Status

Ainda não implementado. Nenhuma execução de comando arbitrário está habilitada.

## Classificação de risco (planejada)

Toda ferramenta futura deverá se encaixar em uma destas três categorias:

- **READ** — ações seguras, somente leitura (ex.: ler um arquivo, consultar um status). Podem ser executadas livremente.
- **ACTION** — ações que modificam algo (ex.: criar/editar um arquivo, mover dados). Podem exigir confirmação dependendo do impacto.
- **DANGEROUS** — ações destrutivas ou sensíveis (ex.: deletar arquivos, executar comandos do sistema, mexer em configurações). **Sempre** exigem confirmação explícita do usuário antes de executar.

Essa classificação existe para que o sistema de permissões (ver [`CLAUDE.md`](../CLAUDE.md), seção "Segurança") possa decidir automaticamente quando pedir confirmação, em vez de confiar em julgamento caso a caso.

## Fora de escopo nesta etapa

Execução arbitrária de comandos no computador do usuário não está implementada e não deve ser adicionada sem uma decisão explícita e deliberada sobre o modelo de permissões.
