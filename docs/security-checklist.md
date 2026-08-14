# Checklist de segurança — auditoria v1.3

Classificação honesta do estado real do código, item a item. `docs/security.md` explica **como** cada mecanismo funciona; este documento diz **o que existe, o que falta e o que não se aplica ainda**.

Modelo de ameaça: **aplicativo desktop local, single-machine**. Não há servidor, não há conta na nuvem, não há multi-tenancy remota. Isso muda o peso de várias defesas — está anotado caso a caso.

| # | Item | Status | Onde / observação |
|---|---|---|---|
| 1 | Esconder API keys | **IMPLEMENTED** | Lidas só do ambiente (`config/settings.py`); nunca em código, banco, QML ou log. `services/providers/secrets.py::mask_secret()` é a única forma sancionada de indicar existência. |
| 2 | Impedir secrets no Git | **IMPLEMENTED** | `.env`, `data/`, `.claude/`, `.claude-flow/`, `.swarm/` ignorados. Modelos de voz (Vosk e Whisper) vivem em `data/models/`, também fora do Git. |
| 3 | Exposição de credencial do banco | **NOT APPLICABLE YET** | SQLite local sem credencial. Vira relevante quando houver banco remoto. |
| 4 | Controle de acesso por registro/usuário | **IMPLEMENTED** | Todo método de repositório filtra por `user_id`, com `_owns()` antes de mutação. Novos na v1.3 (`recovery_codes`, `user_settings`, `pending_email_changes`) seguem a mesma regra. Testes IDOR em `tests/test_security_v1.py` e `tests/test_account_deletion_v13.py`. |
| 5 | Criptografar dado sensível | **PARTIAL** | Token de sessão e **segredo TOTP** cifrados com Windows DPAPI (`services/secret_protection.py`). O banco (`data/jarvis.db`) **não** tem encryption-at-rest — proteção é a do sistema de arquivos. Limitação documentada e exibida no HUD quando o DPAPI não está disponível. |
| 6 | Exigir autenticação | **IMPLEMENTED** | HUD é auth-first. Na v1.3, com 2FA ativo **nenhuma sessão é criada** após só a senha: `AccountManager.login` levanta `TwoFactorRequiredError` e a sessão só nasce em `complete_two_factor()`. |
| 7 | Travar acesso a registro | **IMPLEMENTED** | Ver #4. O backend nunca aceita `user_id` vindo do QML — usa sempre o usuário da sessão. `complete_two_factor` lê o `user_id` pendente de RAM, nunca de parâmetro. |
| 8 | Impedir alteração de campo protegido | **IMPLEMENTED** | `email_verified`, `plan`, `totp_enabled`, `manual_title`, dono de sessão e ids internos só mudam por métodos de serviço; nenhum slot do Bridge aceita esses campos do QML. |
| 9 | Segurança de sessão | **IMPLEMENTED** | Token de 256 bits (`secrets`), guardado como **SHA-256**, expiração de 30 dias, revogação no logout. v1.3: tela "Active Sessions" (nunca mostra token nem hash — `session_id` é prefixo do hash, inútil para autenticar), `delete_others_for_user()` e revogação automática das outras sessões na troca de senha. |
| 10 | Hash de senha | **IMPLEMENTED** | `scrypt` (N=2^15, r=8, p=1), salt único de 16 bytes, `hmac.compare_digest`. Os **códigos de recuperação** usam o mesmo hashing (são credenciais de força total). |
| 11 | Rate limiting | **IMPLEMENTED (v1.3)** | Login com backoff progressivo (5 falhas → 30s, dobra até 15min, nunca permanente). **Novo**: mesmo backoff no segundo fator (`register_totp_failure`) — sem ele, um código de 6 dígitos seria quebrável em horas. Verificação e troca de e-mail: cooldown de 60s e máximo de 5 tentativas. **Falta**: limite no envio de mensagem à IA (hoje só a trava "uma requisição por vez"). |
| 12 | Proteção contra bot/abuso | **NOT APPLICABLE YET** | Sem superfície remota. |
| 13 | Queries parametrizadas | **IMPLEMENTED** | 100% com `?`. Teste de varredura estática **falha o build** se alguém introduzir `execute(f"...")` em `services/` — ele já pegou dois casos meus (v1.1 e v1.3). Única exceção auditada: `PRAGMA user_version = {int(target)}`, que não aceita parâmetro ligado e recebe um contador interno. |
| 14 | Validar toda entrada | **IMPLEMENTED (v1.3)** | Username (charset, tamanho, sem control chars/homoglyph invisível), e-mail (forma + tamanho RFC), senha (mínimo de 8), display name (sanitizado e limitado), título de chat, código TOTP, código de recuperação, chave de dispositivo de áudio, argumentos do CLI. **Falta**: limite explícito de tamanho na mensagem de chat. |
| 15 | Escapar/sanitizar conteúdo do usuário | **IMPLEMENTED** | `services/markdown_safety.py` — HTML escapado (não removido), esquemas `javascript:`/`vbscript:`/`data:`/`file:` neutralizados, blocos de código preservados. **v1.3**: imagens Markdown são removidas antes de tudo (item 29). |
| 16 | Restringir upload de arquivos | **NOT APPLICABLE YET** | Anexos continuam não implementados; não há superfície de upload. |
| 17 | Aparar/redigir resposta de provider | **PARTIAL** | Erros de provider normalizados em `AppErrorCode` (o HUD nunca vê corpo HTTP cru nem stack trace). Texto sanitizado para render (#15). Não há truncagem de tamanho de resposta. |
| 18 | Security headers | **NOT APPLICABLE YET** | Não há servidor HTTP. |
| 19 | HTTPS | **IMPLEMENTED** | Toda saída é HTTPS: OpenRouter, download do modelo Vosk (recusa URL não-HTTPS) e do Whisper (Hugging Face, com validação de hash pelo `huggingface_hub`). **v1.3**: `validate_image_url()` recusa qualquer esquema que não seja `https`. |
| 20 | Scanning de dependências | **PARTIAL** | `pip-audit` continua não instalado (adiciona dependência e consulta rede — exige sua autorização). Dependências diretas passaram de 6 para 8 (`faster-whisper`, `qrcode`), todas com limite superior de versão. Recomendado: `pip install pip-audit && pip-audit`. |
| 21 | **SSRF / URL insegura** (novo v1.3) | **IMPLEMENTED** | `services/web_images.py::validate_image_url()` recusa, **antes de qualquer requisição**: esquema ≠ https, `localhost`/`.local`/`.internal`, loopback, IP privado, link-local (`169.254.169.254` — metadata de nuvem), CGNAT `100.64/10`, ULA IPv6, credenciais embutidas na URL, porta ≠ 443 e caracteres de controle. Limites de fetch declarados como dado auditável (`IMAGE_FETCH_LIMITS`). 30 testes. |
| 22 | **Account enumeration** (novo v1.3) | **IMPLEMENTED** | Login por username OU e-mail resolve os dois campos numa **query só** (dois SELECTs em sequência tornariam "existe como e-mail" mensuravelmente diferente de "não existe"). Conta inexistente e senha errada levantam a **mesma exceção com a mesma mensagem**, e um hash descartável iguala o tempo. Erro de e-mail duplicado nunca revela de quem é. |
| 23 | **Identidade duplicada** (novo v1.3) | **IMPLEMENTED** | `UNIQUE` em `normalized_username` e `normalized_email` no **banco**, não só no serviço — fecha a janela de corrida entre checagem e escrita. A migração 4 **aborta e reporta** se encontrar duplicata legada, sem nunca apagar, fundir ou reescrever e-mail de ninguém. |
| 24 | **Bypass de 2FA** (novo v1.3) | **IMPLEMENTED** | O 2FA só fica ativo depois do primeiro código correto. Desativar exige senha recente **e** segundo fator. Regenerar códigos de recuperação recusa código de recuperação como fator (senão um código vazado se auto-renovaria para sempre). `cancel_enrollment` só age quando o 2FA está inativo. |
| 25 | **Vazamento de segredo TOTP / recovery code** (novo v1.3) | **IMPLEMENTED** | Segredo cifrado por DPAPI no banco; nunca em log (teste `test_secret_never_appears_in_logs` cobre inclusive a `otpauth://` URI, que **contém** o segredo); nunca em evento, exceção ou provider de IA. Recovery codes só como hash; plaintext existe uma vez, na RAM do Bridge, e é limpo ao fechar a tela. |
| 26 | **Path traversal na exclusão de conta** (novo v1.3) | **IMPLEMENTED** | `account_deletion.user_data_dir()` resolve e compara contra a raiz antes de remover; `..`, caminho absoluto, separador invertido e symlink caem no mesmo teste. Nada fora de `data/users/` é removido em nenhuma hipótese. |
| 27 | **Injeção de imagem remota por Markdown** (novo v1.3) | **IMPLEMENTED** | `strip_markdown_images()` roda antes do escape de HTML. Sem isso, `![](file:///C:/Users/...)` faria o renderizador do Qt ler arquivo local, e `![](http://127.0.0.1:8080/admin)` viraria varredura de porta interna disparada por texto de modelo. Até `https://` externo é removido (pixel de rastreio vaza IP). |

## Verificações executadas nesta auditoria

- **SQL** — varredura estática automatizada, verde. Pegou um caso real meu nesta versão (`f"SELECT {colunas} FROM users"`), corrigido para SQL literal em vez de abrir exceção na regra.
- **Isolamento entre contas** — `tests/test_account_deletion_v13.py` cria duas contas populadas e prova que apagar a primeira deixa a segunda **inteira e capaz de logar**.
- **Segredos em log** — `assertLogs(level="DEBUG")` durante todo o fluxo de ativação do 2FA, procurando o segredo e a URI de provisionamento.
- **Vetores do RFC 4226** — `services/totp.py` bate com os valores de teste do padrão (Appendix D). Não inventamos algoritmo.
- **Duplicata de e-mail no banco REAL** — checagem somente-leitura antes de qualquer migração: **0 conflitos** (1 conta, e-mail único, nenhuma conta legacy sem e-mail).
- **Execução/desserialização insegura** — `eval`, `exec`, `shell=True`, `pickle`: ausentes.

## Limitações que permanecem

1. **Plano FREE/PRO é local e alterável** por quem controla a máquina. Enforcement real exige backend remoto.
2. **Banco sem encryption-at-rest.** Quem tem acesso ao arquivo lê chats e memória. Senhas continuam protegidas por scrypt e o segredo TOTP por DPAPI.
3. **DNS rebinding em imagens remotas.** `validate_image_url()` cobre IP **literal**; um nome que resolve para IP privado precisa ser barrado no momento da conexão, pelo provider que fizer o fetch. Está documentado no módulo — e hoje não há provider de busca conectado, então a superfície não existe ainda.
4. **`pip-audit` não executado** (ver #20).
5. **Sem limite de tamanho na mensagem de chat** (ver #14).
6. **Sem provider de busca web/imagem.** A fronteira (`ImageSearchService`) e toda a validação estão prontas e testadas, mas `create_image_search_service()` devolve o placeholder — o JARVIS não finge que a pesquisa funciona.

## Deixado explicitamente para o site/backend futuro

Security headers, CSRF, CORS, WAF, rate limiting distribuído, rotação de chave server-side, auditoria centralizada e enforcement real de plano.
