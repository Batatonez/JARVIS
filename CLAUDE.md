# CLAUDE.md

Este arquivo orienta o Claude Code (e qualquer agente derivado) sempre que estiver trabalhando neste repositório.

## Identidade do projeto

- O projeto se chama **JARVIS**.
- É um **assistente pessoal modular para Windows**, pertencente a Davi.
- O **Claude Code é o principal agente de desenvolvimento** deste projeto e, futuramente, também fará parte do funcionamento em tempo real do sistema (como "cérebro" do assistente).
- O projeto está atualmente no **JARVIS Core v0.1**: um núcleo executável por terminal, sem IA conectada. Muitas pastas ainda existem apenas como estrutura documentada para crescimento futuro — isso é intencional, não um esquecimento.

## Organização

Antes de implementar qualquer funcionalidade:

1. Entender a arquitetura existente (ver [`docs/architecture.md`](docs/architecture.md)).
2. Verificar se já existe código ou documentação relacionada no repositório.
3. Reutilizar componentes quando fizer sentido.
4. Evitar duplicação de lógica ou de documentação.
5. Manter módulos pequenos, organizados e com responsabilidade única.

Não criar arquivos, pastas ou abstrações desnecessárias. Não implementar funcionalidades além do que foi pedido explicitamente. Se uma pasta ainda não precisa de código, um `README.md` explicando sua finalidade é suficiente — não crie arquivos apenas para preencher espaço.

## Segurança

Nunca:

- Deletar arquivos importantes sem necessidade clara e sem confirmação.
- Executar comandos destrutivos sem confirmação explícita do usuário.
- Armazenar senhas, tokens ou API keys em arquivos versionados (use variáveis de ambiente — ver [`config/README.md`](config/README.md)).
- Desativar proteções ou validações apenas para fazer algo funcionar.
- Conceder permissões excessivas a uma integração, ferramenta ou subagente.

Quando uma ação futura puder causar perda de dados ou alterações importantes no computador do usuário, ela deve exigir confirmação explícita antes de ser executada. Ver [`tools/README.md`](tools/README.md) para a classificação de ações (READ / ACTION / DANGEROUS) que deverá orientar isso.

## Memória

A pasta [`memory/`](memory/) contém a memória persistente sobre o usuário.

- Antes de responder ou implementar algo que dependa de informações pessoais previamente registradas, consultar os arquivos relevantes em `memory/`.
- Não salvar automaticamente qualquer informação. Salvar apenas o que tiver utilidade futura clara e duradoura.
- Evitar duplicar informações já registradas — atualizar o que já existe em vez de repetir.
- [`memory/profile.md`](memory/profile.md): informações relativamente estáveis sobre o usuário (quem é, contexto geral).
- [`memory/preferences.md`](memory/preferences.md): preferências relevantes para o funcionamento do JARVIS (estilo de resposta, ferramentas preferidas, etc).

**Metadados técnicos não são memória pessoal.** Informações obtidas de fontes técnicas/ambientais — configuração do Git, nome de usuário do Windows, caminhos de arquivos, variáveis de ambiente, terminal, metadados de sistema, contas conectadas, nome da máquina, dados da sessão atual — podem ser usadas tecnicamente quando necessário (ex.: resolver um caminho), mas **nunca** devem virar memória persistente sobre o usuário em `memory/` sem uma razão clara ou sem o usuário fornecer/autorizar isso explicitamente. Um e-mail de configuração do Git, por exemplo, não deve ser salvo como e-mail do usuário só porque está tecnicamente disponível.

## Projetos

A pasta [`projects/`](projects/) contém contexto persistente sobre projetos acompanhados pelo JARVIS (não confundir com o próprio projeto JARVIS).

- Cada projeto acompanhado deve ter seu próprio arquivo ou subdiretório quando necessário.
- Antes de trabalhar em um projeto existente, consultar o contexto já salvo sobre ele em `projects/`.

## Daily

A pasta [`daily/`](daily/) é usada para registros relacionados a dias específicos, no formato `YYYY-MM-DD.md`.

- Não criar registros diários vazios automaticamente.
- Só criar um registro do dia quando houver conteúdo real para registrar.

## Documentação

Mudanças importantes de arquitetura devem ser refletidas em [`docs/architecture.md`](docs/architecture.md). A documentação não deve contradizer o estado real do código — ao alterar a arquitetura, atualize a documentação na mesma tarefa.

## Estado atual do projeto

O JARVIS está no **JARVIS Core v0.1**: um núcleo executável por terminal (`python main.py`), com orquestração básica, comandos internos, leitura somente-leitura de memória e um event bus interno — tudo usando apenas a biblioteca padrão do Python. Nenhuma dependência externa foi instalada, nenhum framework de interface gráfica foi escolhido, e nenhuma IA, voz, automação, MCP ou integração externa foi implementada ainda. Ver [`docs/architecture.md`](docs/architecture.md) para o detalhamento completo de implementado vs. planejado. Ao trabalhar em tarefas futuras, não presuma que uma camada existe só porque está no diagrama de arquitetura — verifique o estado real do código antes de assumir.
