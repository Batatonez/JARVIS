# memory/

Memória persistente sobre o usuário (Davi) — o que o JARVIS "sabe" entre conversas.

## Arquivos

- **`profile.md`** — informações relativamente estáveis sobre o usuário (quem é, contexto geral, papéis).
- **`preferences.md`** — preferências que devem orientar o comportamento do JARVIS (estilo de resposta, ferramentas, hábitos de trabalho).

## Regras de uso

- Antes de responder ou implementar algo que dependa de contexto pessoal, consultar os arquivos relevantes aqui.
- Não salvar informação automaticamente — apenas o que tiver utilidade futura clara.
- Atualizar entradas existentes em vez de duplicá-las.
- Nada aqui deve conter senhas, tokens ou credenciais.

## Evolução futura (não implementada nesta etapa)

Esta primeira versão é só Markdown, lido diretamente pelo Claude Code. A estrutura foi pensada para evoluir, sem quebrar compatibilidade, para:

- Memória de curto prazo vs. longo prazo
- Classificação automática do que deve ser salvo
- Busca semântica sobre o histórico
- Embeddings e banco vetorial

Nenhuma dessas capacidades existe ainda — os arquivos `.md` continuam sendo a fonte da verdade até que uma dessas evoluções seja explicitamente implementada.
