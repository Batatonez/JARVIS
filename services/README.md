# services/

Serviços internos que compõem o "motor" do JARVIS, entre a interface (`app/`) e o Claude Code.

## Status

Ainda não implementado.

## Responsabilidades futuras (um serviço por preocupação)

- **Comunicação com Claude** — enviar/receber mensagens do Claude Code, gerenciar sessões.
- **Memória** — ler e escrever em `memory/`, `projects/` e `daily/` de forma consistente.
- **Voz** — integração com STT (fala → texto) e TTS (texto → fala).
- **Eventos** — orquestrar hooks e eventos entre app, Claude Code e ferramentas.
- **Estado** — gerenciar o estado da sessão atual do assistente.

Cada serviço deve ser um módulo independente e pequeno, sem depender de detalhes internos dos outros.
