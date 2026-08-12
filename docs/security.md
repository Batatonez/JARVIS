# Segurança do JARVIS

> Documento honesto sobre o que o JARVIS v1.0 protege, **como** protege, e — igualmente importante — o que ele **não** protege. Nada aqui descreve proteção que não existe no código.

Modelo de ameaça desta versão: **aplicativo desktop local, single-machine**. Não há servidor, não há conta na nuvem, não há multi-tenancy remota. O banco fica no disco do próprio usuário. Isso muda o peso de várias defesas — está anotado caso a caso.

## Senhas

`services/password_hashing.py` — `hashlib.scrypt` (biblioteca padrão do Python; scrypt é um dos KDFs recomendados pelo OWASP Password Storage Cheat Sheet, junto com Argon2id).

| Propriedade | Como |
|---|---|
| Nunca em texto puro | Só o hash vai ao banco (`users.password_hash`) |
| Salt único por senha | 16 bytes de `secrets.token_bytes()` — dois usuários com a mesma senha têm hashes diferentes (testado) |
| Custo | `N=2^15, r=8, p=1`, `maxmem=64MiB` (~0,2s por hash nesta máquina) |
| Comparação | `hmac.compare_digest` (tempo constante) |
| Formato | `scrypt$n$r$p$salt$hash` — auto-descritivo, permite subir o custo no futuro sem invalidar hashes antigos |
| Nunca logada | Nem a senha nem o hash aparecem em log (auditado por varredura) |

**Escolha deliberada**: `scrypt` em vez de `bcrypt`/`argon2-cffi` para não somar dependência externa só para isto. Não trocamos de algoritmo na v1.0 — a implementação da v0.9 foi auditada e está correta, e trocar por trocar invalidaria contas existentes sem ganho concreto.

## Proteção contra força bruta no login

`services/user_repository.py`. A partir da **5ª falha consecutiva**, cada nova falha aplica um cooldown que dobra (30s → 60s → 120s...), com teto de **15 minutos**.

- O bloqueio **nunca é permanente**: um lockout permanente seria negação de serviço contra o próprio dono da conta, que é quem mais erra a própria senha.
- Um login bem-sucedido zera o contador.
- Durante o cooldown, até a senha correta é recusada (`AccountLockedError`) — com o tempo restante na mensagem, senão o usuário legítimo não entenderia o que houve.

## Enumeração de contas

- **Login**: usuário inexistente e senha errada levantam a *mesma* exceção com a *mesma* mensagem. Além disso, quando o usuário não existe, ainda executamos um `verify_password` descartável — sem isso, a diferença de tempo (centenas de ms de scrypt) revelaria quais usernames existem. Testado.
- **Cadastro**: conflito de **username** dá erro específico (é um identificador público escolhido pelo usuário — sem isso o cadastro fica inutilizável). Conflito de **e-mail** dá mensagem genérica, porque confirmar "este e-mail já tem conta" vaza informação sobre terceiros.

Ressalva honesta: num app local com um único usuário, enumeração tem impacto próximo de zero. Isto está implementado corretamente pensando numa futura versão com backend.

## Sessões

`services/session_repository.py` + `services/session_store.py`.

```
token (256 bits, secrets.token_urlsafe)
  ├─ SHA-256 ──> banco (sessions.token_hash)   ← nunca o token em si
  └─ DPAPI  ──> data/session.local             ← credencial real, no disco do usuário
```

- O banco guarda **só o hash**. Quem obtiver uma cópia do `jarvis.db` **não** consegue se passar por um usuário logado. (Isto mudou na v1.0 — a v0.9 guardava o token em claro; a migração converteu os tokens existentes sem invalidar nenhuma sessão.)
- SHA-256 puro é o certo aqui, e não scrypt: o token tem 256 bits de entropia de `secrets`, não há espaço de busca a encarecer artificialmente (ao contrário de uma senha humana).
- Expiração de 30 dias, validada contra o relógio real; sessão vencida é apagada no momento em que é usada.
- `delete_all_for_user()` existe para revogar tudo (base para um "sair de todos os dispositivos" futuro).
- O token local é cifrado com **Windows DPAPI** (`win32crypt`, já dependência transitiva do `pyttsx3`) — a chave é a do próprio usuário do Windows, e o JARVIS não inventa nem gerencia criptografia própria. Sem `win32crypt`, degrada para texto puro no arquivo, que continua fora do Git; isso está registrado como limitação abaixo.

**Investigado e não adotado**: Windows Credential Manager (`CredWrite`) daria armazenamento gerenciado pelo SO. DPAPI foi mantido porque já estava implementado, validado, e resolve o mesmo problema (proteção em repouso ligada à conta do Windows) sem somar dependência nem risco de regressão numa versão de estabilização.

## Logout

`AccountManager.logout()`, nesta ordem: encerra a Application Layer (o que **cancela a requisição de IA pendente** e **desliga o microfone/TTS** via `JarvisApplication.stop()`), invalida a sessão no banco, apaga o token local, limpa o estado sensível em RAM. **Não** apaga conta, chats nem memória.

## Isolamento entre usuários

Reforçado na **query**, não só na UI: todo método de `ConversationRepository` filtra por `user_id` no `WHERE`, com um `_owns()` antes de qualquer mutação. Testes IDOR-style (`tests/test_security_v1.py::UserIsolationTests`) tentam ler/renomear/apagar a conversa de outro usuário passando o ID direto — todos falham como deveriam.

Memória por conta vive em `data/users/<user-id>/memory/`, onde `<user-id>` é um **UUID interno**, nunca o username digitado — um username malicioso como `../outro` não teria como escapar do diretório.

## SQL injection

Todas as queries são parametrizadas (`?`). Há um teste de varredura estática (`test_no_sql_is_built_by_string_formatting`) que falha o build se alguém introduzir `execute(f"...")` em `services/`. A única exceção auditada é `PRAGMA user_version = {int(target)}` em `local_database.py` — PRAGMA não aceita parâmetro ligado, e o valor é um contador interno (`int()`), nunca entrada do usuário.

## Path traversal / Zip Slip

- **Modelo de voz**: `services/vosk_model_manager.py` resolve cada entrada do `.zip` e confere `Path.is_relative_to(destino)` **antes** de extrair. Testado com um `.zip` malicioso construído localmente.
- **Memória por usuário**: caminho derivado de UUID, não de entrada do usuário (acima).
- **Download**: HTTPS obrigatório, arquivo temporário dentro do diretório de destino, limpeza garantida.

## Verificação de e-mail

`services/email_verification_*.py`.

| Regra | Valor | Onde é validada |
|---|---|---|
| Expiração | 5 minutos | Backend, contra `expires_at` no banco |
| Cooldown de reenvio | 60 segundos | Backend, contra `resend_available_at` |
| Uso único | sim | Desafio é consumido no acerto |
| Novo invalida o anterior | sim | Emitir um novo consome todos os ativos, na mesma transação |
| Máximo de tentativas | 5 | Desafio é queimado ao estourar |

O frontend **nunca** é autoridade sobre tempo: o `Timer` do QML só decrementa a exibição; os valores vêm de timestamps persistidos. Fechar e reabrir o JARVIS mostra o tempo real restante (testado).

**Honestidade sobre a força do código**: um código de 6 dígitos tem 10^6 possibilidades. Ele é guardado com hash `scrypt` (reusando `password_hashing.py` — nenhuma criptografia nova foi inventada), mas se o banco vazar, 10^6 é força-brutável offline em segundos, independentemente do hash. A segurança real vem dos três limites acima (expiração curta + uso único + limite de tentativas), não da força do hash. Um código mais longo seria mais forte e menos usável; a escolha foi consciente.

Sem SMTP configurado, o JARVIS **nunca finge ter enviado**: retorna `EMAIL_SERVICE_NOT_CONFIGURED` e a UI diz isso claramente.

## Segredos

Nunca em código, banco, QML, log, README ou Git. Sempre lidos do ambiente:

| Variável | Uso |
|---|---|
| `OPENROUTER_API_KEY` | Provider Router (`services/providers/openrouter_provider.py`) |
| `ANTHROPIC_API_KEY` | Claude Agent SDK |
| `JARVIS_SMTP_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` / `JARVIS_EMAIL_FROM` | Envio de e-mail |

`services/providers/secrets.py::mask_secret()` é a única forma sancionada de indicar "está configurado" em debug — no máximo os 4 últimos caracteres. Nenhum log imprime chave, senha, hash, token de sessão ou código de verificação (auditado por varredura em toda a `services/`, `app/` e `frontend/`).

## Privacidade ao chamar a IA

A partir da v1.0 o texto pode **sair da máquina** (OpenRouter). Duas defesas, em `services/context_builder.py`, aplicadas em `JarvisCore.build_memory_context()` — ou seja, em um ponto único, para que todo provider receba a memória já tratada:

1. **Data minimization** — só identidade de runtime + perfil/preferências, truncados a `JARVIS_MAX_MEMORY_CONTEXT_CHARS` (padrão 4000). Nunca o banco, nunca chats de outro usuário, nunca metadados internos.
2. **Sanitização** — rede de segurança contra segredo que tenha vazado *para dentro* da memória (o usuário escreve `profile.md` à mão e pode colar uma chave lá sem pensar). Chaves de provider, `Authorization: Bearer`, hashes no nosso formato, atribuições `*_API_KEY=`/`*_TOKEN=`/`*_PASSWORD=` e blobs hex longos viram `[REDACTED]`. Testado, inclusive que texto legítimo **não** é mutilado.

O JARVIS nunca *coloca* segredo no contexto: senha, hash, token de sessão e código de verificação vivem só no banco/RAM e não passam por esse caminho. A sanitização é defesa em profundidade, não a defesa principal.

## Erros de provider

Normalizados em `AppErrorCode` (`PROVIDER_NOT_CONFIGURED`, `PROVIDER_UNAVAILABLE`, `PROVIDER_RATE_LIMITED`, `NO_FREE_MODEL_AVAILABLE`, ...). O usuário nunca vê stack trace nem corpo cru de resposta HTTP.

## Controle de custo (`free_only`)

Ligado por padrão. Ver `docs/providers.md` para o mecanismo de duas camadas e sua limitação honesta (a segunda camada é detecção pós-chamada, não prevenção — a requisição HTTP já aconteceu quando o custo é conhecido).

## Itens revisados e conscientemente aceitos

- `os.system("cls"/"clear")` em `app/commands.py` (comando `/clear` do terminal): string **constante**, sem qualquer entrada do usuário — não há vetor de injeção. Mantido.
- Nenhum `eval`, `exec`, `shell=True`, `pickle` ou desserialização insegura existe no projeto (auditado por varredura).

## Limitações conhecidas (honestas)

1. **Plano FREE/PRO é local e alterável.** Quem controla a máquina pode editar `users.plan` no SQLite. Enforcement de verdade **exige backend remoto** — não existe nesta versão, e não fingimos que existe. O `app/entitlements.py` é fundação de arquitetura, não paywall.
2. **Não há encryption-at-rest do banco.** `data/jarvis.db` (chats, memória, hashes) fica em disco sem cifra própria; a proteção é a do sistema de arquivos/conta do Windows. Só o token de sessão é cifrado (DPAPI).
3. **Sem DPAPI** (ambiente sem `pywin32`), o token de sessão local fica em texto puro no arquivo — fora do Git, mas legível por quem já tem acesso ao disco do usuário.
4. **Sem cloud/auth remota**: não há recuperação de senha, não há revogação remota, não há verificação de dispositivo.
5. **Código de verificação de 6 dígitos**: força vem de expiração/uso único/limite de tentativas, não do hash (detalhado acima).
6. **Cancelamento de requisição de IA** cancela a `asyncio.Task` e descarta o resultado, mas a requisição HTTP em voo (em thread de executor) não é abortada no socket — ela termina e é ignorada. Sem impacto de correção; anotado por transparência.
7. **Ruflo** (`docs/ruflo-integration.md`) é ferramenta de desenvolvimento, opcional, e **não** é fonte de verdade de provider/modelo/custo — tem bug confirmado de model routing.
