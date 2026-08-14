# Checklist de segurança — auditoria v1.2

Classificação honesta do estado real do código, item a item. `docs/security.md` explica **como** cada mecanismo funciona; este documento diz **o que existe, o que falta e o que não se aplica ainda**.

Modelo de ameaça: **aplicativo desktop local, single-machine**. Não há servidor, não há conta na nuvem, não há multi-tenancy remota. Isso muda o peso de várias defesas — está anotado caso a caso.

| # | Item | Status | Onde / observação |
|---|---|---|---|
| 1 | Esconder API keys | **IMPLEMENTED** | Lidas só do ambiente (`config/settings.py`); nunca em código, banco, QML ou log. `services/providers/secrets.py::mask_secret()` é a única forma sancionada de indicar existência. |
| 2 | Impedir secrets no Git | **IMPLEMENTED** | `.env`, `data/`, `.claude-flow/`, `.swarm/` ignorados. Varredura do histórico nesta auditoria: só fixtures de teste (`sk-or-v1-abcdefghij...`), **nenhum secret real**. |
| 3 | Exposição de credencial do banco | **NOT APPLICABLE YET** | SQLite local sem credencial. Vira relevante quando houver banco remoto. |
| 4 | Controle de acesso por registro/usuário | **IMPLEMENTED** | Todo método de `ConversationRepository`/`UserMemoryRepository` filtra por `user_id`, com `_owns()` antes de mutação. Testes IDOR em `tests/test_security_v1.py`. |
| 5 | Criptografar dado sensível | **PARTIAL** | Token de sessão cifrado (Windows DPAPI). O banco (`data/jarvis.db`) **não** tem encryption-at-rest — proteção é a do sistema de arquivos. Limitação documentada. |
| 6 | Exigir autenticação | **IMPLEMENTED** | HUD é auth-first: sem sessão válida só existe a `AuthScreen`; `JarvisApplication` só é construída após login (`AccountManager`). |
| 7 | Travar acesso a registro | **IMPLEMENTED** | Ver #4. O backend nunca aceita `user_id` vindo do QML — usa sempre o usuário da sessão. |
| 8 | Impedir alteração de campo protegido | **IMPLEMENTED** | `email_verified`, `plan`, `user_id`, dono de sessão e ids internos só mudam por métodos de serviço; nenhum slot do Bridge aceita esses campos do QML. |
| 9 | Segurança de sessão | **IMPLEMENTED** | Token de 256 bits (`secrets`), guardado como **SHA-256** no banco, expiração de 30 dias, revogação no logout, `delete_all_for_user()`. |
| 10 | Hash de senha | **IMPLEMENTED** | `scrypt` (N=2^15, r=8, p=1), salt único de 16 bytes, `hmac.compare_digest`. Auditado nesta versão, **não** trocado (estava adequado). |
| 11 | Rate limiting | **PARTIAL** | Login tem backoff progressivo (5 falhas → 30s, dobra até 15min). Verificação de e-mail tem cooldown de 60s e máximo de 5 tentativas. **Falta**: limite no envio de mensagem à IA (hoje só a trava "uma requisição por vez"). |
| 12 | Proteção contra bot/abuso | **NOT APPLICABLE YET** | Sem superfície remota. Relevante quando houver API pública. |
| 13 | Queries parametrizadas | **IMPLEMENTED** | 100% com `?`. Há teste de varredura estática que **falha o build** se alguém introduzir `execute(f"...")` em `services/` — ele já pegou um caso meu na v1.1. |
| 14 | Validar toda entrada | **PARTIAL** | Validados: username, e-mail, senha, código de verificação, título de chat (trim/limite), argumentos do CLI. **Falta**: limite explícito de tamanho na mensagem de chat e no conteúdo de anexo (anexos não existem ainda). |
| 15 | Escapar/sanitizar conteúdo do usuário | **IMPLEMENTED (v1.2)** | `services/markdown_safety.py` — HTML embutido é escapado (não removido), esquemas `javascript:`/`vbscript:`/`data:`/`file:` neutralizados, blocos de código preservados. 22 testes. |
| 16 | Restringir upload de arquivos | **MISSING** | **Anexos não foram implementados nesta versão** — ver "Não entregue" abaixo. Enquanto não existirem, não há superfície de upload. |
| 17 | Aparar/redigir resposta de provider | **PARTIAL** | Erros de provider são normalizados em `AppErrorCode` (o HUD nunca vê corpo HTTP cru nem stack trace). O **texto** da resposta é sanitizado para render (#15). Não há truncagem de tamanho de resposta. |
| 18 | Security headers | **NOT APPLICABLE YET** | Não há servidor HTTP. Registrado para o site/backend futuro. |
| 19 | HTTPS | **IMPLEMENTED (no que se aplica)** | Toda chamada de saída é HTTPS: OpenRouter e o download do modelo Vosk (que **recusa** URL não-HTTPS). Enforcement de servidor: não aplicável. |
| 20 | Scanning de dependências | **PARTIAL** | `pip-audit` **não está instalado** e não foi instalado sem autorização (adiciona dependência e consulta rede). Auditoria por inspeção: 6 dependências diretas, todas com limite superior de versão, todas em release recente. **Recomendado como próximo passo**, com sua autorização: `pip install pip-audit && pip-audit`. |

## Verificações executadas nesta auditoria

- **Secrets no histórico do Git** — varredura por `sk-or-v1-`, `sk-ant-api`, `AKIA`, `JARVIS_SMTP_PASSWORD=<valor>`. Resultado: **nenhum secret real**; os únicos matches são fixtures de teste evidentemente falsos. **A Gmail App Password usada nos seus testes locais nunca foi lida, exibida nem commitada.**
- **SQL** — varredura estática automatizada (`tests/test_security_v1.py::test_no_sql_is_built_by_string_formatting`), verde.
- **Isolamento entre usuários** — testes IDOR para conversas, memória e (novo na v1.2) regeneração de mensagem: `update_message_content` é escopado por `user_id`, então "Regenerate" não vira caminho para escrever na conversa alheia.
- **Execução/desserialização insegura** — `eval`, `exec`, `shell=True`, `pickle`: ausentes. Único `os.system` é `cls`/`clear` com string constante (revisado e aceito).
- **Markdown/HTML injection** — 22 testes novos, incluindo blocos de código, esquemas perigosos e um bug real do próprio sanitizador (cerca de código sendo "engolida" pelo padrão de código inline).

## Limitações que permanecem

1. **Plano FREE/PRO é local e alterável** por quem controla a máquina. Enforcement real exige backend remoto.
2. **Sem encryption-at-rest** do `data/jarvis.db`.
3. **Sem DPAPI** (ambiente sem `pywin32`), o token de sessão local fica em texto puro no arquivo — fora do Git, mas legível por quem já tem o disco.
4. **Código de verificação de 6 dígitos**: a força vem de expiração/uso único/limite de tentativas, não do hash.
5. **Sem rate limit no envio à IA** além da trava de concorrência.
6. **`pip-audit` não executado** (acima).

## Deixado explicitamente para o site/backend futuro

Nada disto faz parte da v1.2 e nenhuma linha foi escrita para eles:

- cookies seguros e sessão server-side;
- autenticação/contas na nuvem, recuperação de senha, revogação remota;
- enforcement de HTTPS e HTTP security headers (CSP, HSTS, X-Frame-Options);
- CSRF;
- rate limiting de API pública e proteção contra bots;
- RLS em banco remoto;
- infraestrutura de download/atualização assinada (instalador, `.exe`).
