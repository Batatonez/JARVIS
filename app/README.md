# app/

Responsável pela aplicação principal e pela interface do JARVIS (chat, HUD, entrada/saída de voz).

## Status

Ainda não implementado. Nenhuma tecnologia de interface foi escolhida.

## Responsabilidade futura

- Renderizar a interface com que o usuário interage (chat de texto, HUD visual).
- Capturar entrada de voz e texto do usuário e repassar ao orquestrador (`services/`).
- Exibir respostas em texto e reproduzir respostas em voz.
- Não deve conter lógica de negócio, memória ou orquestração — isso pertence a `services/`. O `app/` é a camada de apresentação.

## Decisões em aberto (não decidir prematuramente)

- Framework de interface (ex.: Electron, Tauri, PySide, web) — a ser avaliado quando a etapa de interface começar, com base nos requisitos reais do HUD e não antes.
