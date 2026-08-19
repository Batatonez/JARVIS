# Camada de precisão e verificação

## O bug que originou isto

Perguntado "oq era el ninho?", o JARVIS respondeu, com confiança, que era um
chocolate da Nestlé.

O modelo não reconheceu o termo. Em vez de dizer isso, completou a lacuna com
a coisa mais próxima que conhecia. Esse é o modo de falha central de um
assistente, e ele não se corrige pedindo humildade no system prompt: o modelo
continua sendo um completador de texto que sempre tem algo a completar.

**Uma resposta errada dita com confiança é pior que "preciso verificar isso".**
A camada de precisão existe para tornar esse princípio verdadeiro no código.

## Arquitetura

```
mensagem
   ↓
preflight            determinístico, microssegundos, sem chamar modelo
   ↓
AccuracyDecision     DIRECT | CLARIFY | VERIFY | RESEARCH
   ↓
evidência real       só de ferramenta que executou de verdade
   ↓
orientação           entra no prompt DESTA mensagem
   ↓
(o modelo redige)
   ↓
verificação          quando a decisão pede; no máximo uma revisão
   ↓
resposta + fontes reais
```

| Módulo | Responsabilidade |
|---|---|
| `services/accuracy/models.py` | tipos serializáveis (decisão, evidência, fonte, claim, atividade) |
| `services/accuracy/preflight.py` | classifica a mensagem sem chamar modelo |
| `services/accuracy/context.py` | estado por request: fontes, evidência, atividade |
| `services/accuracy/service.py` | orquestra a request |
| `services/accuracy/verifier.py` | confere o rascunho, com saída estruturada |
| `services/search/web_search.py` | interface de busca — e a indisponibilidade honesta |

## Como o caso "el ninho" é detectado

Nada no código conhece "el ninho", "El Niño" ou "Nestlé". O que é detectado
são **propriedades da frase**:

1. é uma pergunta de definição ("o que é X", "quem é X");
2. o X não tem morfologia de substantivo comum do idioma;
3. o X tem forma de nome próprio, estrangeirismo ou identificador de produto.

Os sinais são ortográficos: artigo estrangeiro inicial (`el `, `la `, `der `),
agrupamento de letras incomum, mistura de letras e dígitos, inicial maiúscula
no meio da frase. Do outro lado, terminações derivacionais produtivas
(`-ção`, `-dade`, `-ismo`, `-ese`, `-tion`, `-ology`) identificam palavra
comum do idioma e mantêm "fotossíntese" e "capitalismo" no caminho rápido.

**Não há blacklist de frases.** Um teste percorre as strings literais do
código (ignorando docstrings) para garantir que nenhuma lista de palavras
proibidas está sendo comparada contra a resposta.

## Freshness

| Nível | Exemplo | Comportamento |
|---|---|---|
| `STATIC` | fotossíntese, história antiga, matemática | responde direto |
| `SEMI_STABLE` | specs, compatibilidade, documentação | verifica |
| `VOLATILE` | preço, versão atual, CEO, clima, placar | exige evidência externa |

Os marcadores são casados com **fronteira de palavra**. Isso não é detalhe:
com busca por substring, "capitalismo" casava com o marcador "api"
(c-**api**-talismo) e uma pergunta estática virava pesquisa.

## Busca web — estado real

**O JARVIS não tem busca web.** Nenhum provedor está integrado e esta versão
não integra nenhum. `services/web_images.py` busca imagens com proteção
SSRF; não é pesquisa de texto.

`UnavailableWebSearch` é a implementação padrão. Ela `is_available() == False`
e `search()` **levanta** em vez de devolver lista vazia — lista vazia
significaria "pesquisei e não achei", uma afirmação falsa sobre o que
aconteceu.

Consequências, todas testadas:

- a atividade `SEARCHING_WEB` nunca é emitida sem busca real;
- `sources` fica vazio, e a interface não mostra botão de fontes;
- a orientação ao modelo diz explicitamente para admitir que não verificou e
  não inventar fontes nem links.

Não implementei scraping de buscador: é frágil, costuma violar termos de uso,
e produziria uma busca que falha em silêncio — pior que não ter busca, porque
cria a expectativa de que existe. Quando houver uma API real, ela implementa
`WebSearchService` e tudo acima passa a funcionar sem mudar a camada.

## Fontes não podem ser fabricadas

`SourceRegistry.register` é o **único** caminho para uma fonte existir, e ele
exige a URL que uma ferramenta retornou. Um modelo pode escrever
`"sources": ["https://..."]` no meio do texto — isso é texto, não passa por
aqui, e nunca vira fonte na interface.

O registry também:

- aceita só `http`/`https` (`javascript:`, `data:` e `file:` são recusados);
- recusa URL com credencial embutida (`https://user:senha@host`);
- recusa fonte web **sem** URL — uma fonte que não se pode abrir é uma
  afirmação, não uma fonte;
- recusa `MODEL_KNOWLEDGE` como fonte;
- deduplica por host + caminho canônico;
- sanitiza título (remove controle e marcação, limita tamanho).

## Activity Trace ≠ chain-of-thought

A distinção não é de grau, é de natureza. Um evento significa **"esta
operação foi executada"**, e é criado pelo código que a executa.

A garantia é estrutural, não editorial:

- nenhum método aceita texto livre — só tipo, status e metadata;
- a metadata passa por uma **allowlist** de chaves (`count`, `sources`,
  `provider`, `error`…). Truncar não bastaria: sessenta caracteres de
  raciocínio ainda são raciocínio;
- o rótulo é uma **chave de tradução**, não texto gerado — não há campo por
  onde texto do modelo chegue à interface.

O trace é criado por request e morre com ela. Uma pesquisa numa mensagem não
aparece na seguinte.

## Verificador

É um **sinal de revisão**, nunca uma fonte. Um segundo passe do mesmo modelo
não transforma uma afirmação em fato comprovado — modelos compartilham erros
de treino, e dois concordando sobre algo falso continua sendo falso. O
resultado nunca vira `EvidenceItem` nem aparece como fonte.

- saída **estruturada** (JSON), com o prompt proibindo explicação — pedir
  raciocínio produziria exatamente o texto que o projeto garante não vazar;
- **uma revisão, no máximo**. Insistir até passar otimizaria para enganar o
  verificador em vez de para dizer a verdade;
- **sem evidência nenhuma**, "não sustentado" vira `INSUFFICIENT_EVIDENCE`
  (incerteza explícita), não uma revisão que faria o modelo inventar algo que
  passasse;
- falha do verificador **degrada**: o usuário recebe a resposta, sem a
  afirmação de que foi verificada.

## Recusa e safety

Uma recusa estruturada **não** é verificada nem revisada. Mandar uma recusa ao
verificador e depois "corrigi-la" seria usar a camada de precisão para obter o
que a safety negou. A cadeia termina na recusa, como desde a v1.6.

## Injeção de prompt

Conteúdo de fonte web e de documento é **dado**, nunca instrução. A orientação
enviada ao modelo diz isso explicitamente, e a identidade de runtime repete o
princípio. Uma página que diga "ignore as instruções anteriores" é conteúdo da
página.

Nenhuma execução de ferramenta parte de texto de fonte: ações continuam
passando por `app/actions.py` e `app/skills.py`, com permissão, risco e
confirmação.

## Latência

| Caminho | Custo |
|---|---|
| `oi`, `2+2`, pedido criativo | preflight (regex) e nada mais |
| pergunta factual estável | preflight + geração normal |
| `VERIFY` | + uma chamada isolada de verificação |
| `RESEARCH` | + busca (quando existir) + verificação |

O fast path não emite nem atividade: "oi" não teve interpretação a mostrar, e
um evento ali seria teatro.

## Limitações

- **Não há busca web.** É a limitação central. A camada decide corretamente
  que precisa de evidência externa e admite não tê-la — mas não pesquisa.
- **A interface ainda não mostra o Activity Trace.** Os dados existem,
  tipados e serializáveis, e o `i18n` já tem os rótulos nos três idiomas; o
  componente QML e o painel de fontes não foram construídos nesta entrega.
- **Sem busca semântica.** Exigiria modelo de embeddings no instalador.
- **Sem verificação cross-model.** A arquitetura comporta (o verificador
  recebe um `AIService` qualquer), mas nenhuma estratégia de consenso foi
  implementada — e "maioria vence" não seria implementada de qualquer forma:
  evidência real tem prioridade sobre concordância entre modelos.
- **O preflight é heurístico.** Ele erra para o lado barato (verificar algo
  que já se sabia), mas erra. Um termo comum com ortografia incomum pode ser
  mandado para verificação sem necessidade.
