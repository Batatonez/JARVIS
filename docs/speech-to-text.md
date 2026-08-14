# Speech-to-Text no JARVIS (v1.3)

## O problema que motivou esta versão

Até a v1.2, falar "Opa, tudo bem?" no microfone produzia `opa bem` — ou só
`bem`. Palavras sumiam no meio da frase.

**A causa raiz não era o modelo.** Era o modo de uso do Vosk.

`KaldiRecognizer.AcceptWaveform()` devolve `True` quando o *endpointer* do
Kaldi decide que uma utterance terminou — tipicamente numa micro-pausa, como
a vírgula depois de "Opa". Nesse instante o texto daquela utterance fica
disponível em `Result()`, e o reconhecedor **reinicia** para uma utterance
nova.

O código da v1.2 ignorava o retorno de `AcceptWaveform` e lia apenas
`FinalResult()` no fim da captura. Tudo que o endpointer havia fechado pelo
caminho era descartado silenciosamente, e sobrava só o último trecho.

A correção está em `services/vosk_stt_provider.py::transcribe_pcm`: cada
`AcceptWaveform() is True` tem seu `Result()` lido e acumulado, e
`FinalResult()` acrescenta o resto. `tests/test_stt_v13.py` tem um teste
(`test_final_result_alone_would_lose_words`) cuja única função é quebrar a
suíte se alguém reintroduzir o bug.

## Arquitetura

```text
AudioCapture (services/audio_capture.py)
    → buffer PCM int16 mono 16kHz, inteiro, em RAM
        ↓
BufferedSTTProvider (services/stt_service.py)
        ↓
   ┌────┴────────────────────────┐
FasterWhisperSTTProvider     VoskSTTProvider
   (principal)                 (fallback leve)
```

A captura foi separada dos engines. O provider recebe o **buffer completo** e
transcreve de uma vez — o que elimina por construção toda a classe de bug de
"pedaço da frase perdido entre resultado parcial e final".

Política de escolha (`create_stt_service`):

```text
voice_input_enabled=False        → UNAVAILABLE
faster-whisper instalado         → FasterWhisperSTTProvider
senão, Vosk instalado            → VoskSTTProvider (fallback)
nenhum modelo                    → SETUP_REQUIRED
```

Nunca levanta exceção e nunca baixa modelo sozinho. `JARVIS_STT_ENGINE=vosk`
força o fallback em máquinas fracas.

## Benchmark: por que o modelo `base`

Frases sintetizadas com a voz SAPI pt-BR local (Microsoft Maria) → WAV → cada
engine transcreve. Sem microfone real, sem internet além do download dos
modelos, reproduzível.

> **Limite honesto:** áudio de TTS é mais limpo que microfone real. Os números
> são **comparativos entre engines**, não um WER absoluto.

| Engine | Disco | Carga | Latência média | Acertos (5 frases) |
|---|---|---|---|---|
| Vosk small-pt-0.3 | 51 MB | 0,22 s | 0,64 s | 2/5, sem pontuação |
| faster-whisper `tiny` | 75 MB | 0,40 s | 0,22 s | 3/5 |
| **faster-whisper `base`** | **141 MB** | **0,25 s** | **0,38 s** | **5/5** |
| faster-whisper `small` | 464 MB | 0,68 s | 1,16 s | 5/5 |

Frases: `Opa, tudo bem?` · `Meu nome é Davi.` · `Abre um novo chat.` ·
`Como está o tempo hoje?` · `Quero pesquisar sobre inteligência artificial.`

Erros observados:

- **Vosk**: `Meu nome é Davi.` → `meu nome é vi`; `Abre` → `abrir`. Sem
  pontuação e sem capitalização.
- **`tiny`**: `Davi` → `David`; `chat` → `chate`.
- **`base`** e **`small`**: todas corretas, com pontuação e capitalização.

**`base` é o menor modelo que acerta tudo.** `small` custa 3,3× o disco e 3× a
latência por zero ganho de acerto nestas frases. Por isso o default é `base`
(`Settings.whisper_model_size`), sobrescrevível por `JARVIS_WHISPER_MODEL`.

> Estas frases são **benchmark**, nunca gabarito: não existe nenhuma correção
> hardcoded para elas em lugar nenhum do código.

## Sample rate: 16 kHz direto do dispositivo

Whisper e Vosk querem 16 kHz. Pedir uma taxa que o dispositivo não suporta ou
falha alto (`PortAudioError`) ou — pior — é aceito pelo driver e produz áudio
distorcido em silêncio.

A v1.3 **negocia**: `sd.check_input_settings(samplerate=16000)` antes de abrir.
Quando o PortAudio aceita (o caso comum no WASAPI do Windows), a conversão é
feita pelo motor de áudio do sistema e **nenhum resample nosso acontece**.

O `LinearResampler` só entra quando o dispositivo recusa 16 kHz. Ele ganhou
duas correções e uma limitação documentada:

1. **Borda de bloco** — a v1.2 guardava `self._pos = pos - n`, que podia ficar
   negativo e fazer a interpolação extrapolar para trás, fora do sinal. Agora a
   cauda de cada bloco é preservada como âncora do próximo.
2. **Anti-aliasing** — média móvel de largura ≈ `ratio` antes da interpolação
   (só engaja quando `ratio >= 1.5`).
3. **Limitação medida:** interpolação linear perde qualidade contra um sinc
   janelado. No benchmark, `Abre um novo chat` saiu correta pelo resampler
   polifásico do `av` e como `chate` pelo nosso — com e sem o filtro. A média
   móvel **não** recupera essa diferença. É por isso que a correção principal é
   não usar este caminho. Um sinc janelado exigiria `numpy` na captura, que
   precisa continuar funcionando no caminho só-Vosk.

## VAD: não cortar palavra nenhuma

`SilenceDetector` (`services/audio_capture.py`) é lógica pura, testável com
níveis sintéticos, sem microfone.

| Parâmetro | Valor | Por quê |
|---|---|---|
| `calibration_seconds` | 0,35 s | mede o ruído do ambiente; **nunca** encerra aqui — é o começo da frase |
| `trailing_silence_seconds` | 1,2 s | quem diz "Opa, tudo bem?" faz micro-pausa depois de "Opa"; 0,4 s cortaria exatamente esse caso |
| `min_speech_seconds` | 0,4 s | um estalo curto não encerra a captura |
| `max_seconds` | 60 s | teto duro: nunca ouvir para sempre |
| `silence_timeout_seconds` | 8 s | ninguém falou: encerra em vez de travar o "Test Microphone" |

O limiar é adaptativo: `max(piso_absoluto, ruído_medido × 3)`. O piso cobre
ambiente silencioso demais (onde qualquer múltiplo do ruído seria ~0); o
multiplicador cobre ambiente barulhento.

O Whisper ainda aplica o VAD interno dele por cima, com
`min_silence_duration_ms=500` e `speech_pad_ms=400` — padding generoso de
propósito, para não cortar o ataque da primeira nem o fim da última palavra.

## v1.3.1 — começo de frase e a palavra "JARVIS"

Sintoma relatado: falando **"Opa Jarvis, tudo bem?"** saía **"Vou apagar a
vizilha e tudo bem?"** — começo destruído, fim correto.

### Diagnóstico

O áudio limpo de TTS **acertava em todas as variantes de parâmetro**,
inclusive a configuração da v1.3. Isso descartou o modelo e os parâmetros de
decodificação, e apontou para a captura. Reproduzindo as degradações que um
microfone real introduz, uma a uma:

| Degradação simulada | Transcrição |
|---|---|
| referência (limpo) | `Opa, Jarvis, tudo bem!` |
| **início cortado 150 ms** | `Opa de arvies, tudo bem.` |
| **início cortado 300 ms** | `de árvores, tudo bem.` |
| volume 1% | `Opa, Jarvis, tudo bem!` |
| **ruído SNR 10 dB** | `contas nases tudo bem.` |
| reverb (mic distante) | `Opa, Jarvis, tudo bem.` |

Duas causas independentes reproduzem a assinatura exata — **começo destruído,
"tudo bem" correto**. Volume baixo sozinho não quebra nada (o Whisper
normaliza), e reverb sozinho também não.

### Causa 1 — o começo era perdido de verdade

`stream.start()` retorna quando o PortAudio aceita o pedido, **não** quando o
dispositivo começa a entregar amostras. Como o retorno de `start_listening()`
é o que faz o HUD mostrar `LISTENING`, e `LISTENING` é o sinal para o usuário
falar, a pessoa começava a falar antes de existir captura.

Correção: `BufferedSTTProvider._wait_for_first_audio()` segura o retorno até o
primeiro bloco chegar (`AudioCapture.receiving`), com teto de 400 ms e
`asyncio.sleep` — nunca bloqueia o event loop, e um dispositivo mudo não trava
a UI.

### Causa 2 — "JARVIS" não é palavra comum em português

Sob ruído, o decoder escolhe qualquer sequência mais provável. A correção usa
os dois mecanismos que o próprio faster-whisper oferece
(`services/speech_vocabulary.py`):

- `hotwords="JARVIS"` — enviesa o decoder para o termo;
- `initial_prompt` — lista de termos do app (chat, conversa, microfone,
  renomear...), **sem nenhuma frase de exemplo**;
- meio segundo de silêncio nas duas pontas, para o encoder ter embalo.

**Isto não é substituição de texto.** Nada procura uma saída errada para
trocar por uma certa — o que o engine devolve é o que sai, e há teste
(`test_no_text_substitution_anywhere`) que fixa isso.

### Resultado medido (provider real)

| Caso | Antes (v1.3) | Depois (v1.3.1) |
|---|---|---|
| limpo | `Opa, Jarvis, tudo bem.` | `Opa, JARVIS, tudo bem.` |
| início cortado 150 ms | `Opa de arvies, tudo bem.` | `Opa, JARVIS, tudo bem.` |
| ruído SNR 10 dB | `contas nases tudo bem.` | `Conta, JARVIS, tudo bem.` |
| volume 10% + SNR 10 dB | `contas, árvores, tudo bem?` | `Conta, JARVIS, tudo bem.` |
| **acertos de "JARVIS"** | **0/7** | **4/7** |

Frases curtas, que era o pior caso, ficaram exatas: `Jarvis` → `JARVIS`;
`Oi Jarvis` → `Oi, JARVIS.`; `Jarvis, abre um novo chat` → `JARVIS, abre um
novo chat.` (antes saía "abrem").

**O que ainda falha:** início cortado em 300 ms e SNR ≤ 5 dB. São casos em que
o áudio simplesmente não existe ou está enterrado em ruído — nenhum viés de
decodificação inventa o que não foi gravado. A correção da causa 1 é o que
evita chegar nesse estado.

### Duas armadilhas encontradas no caminho

1. **Prompt contaminado.** A primeira versão do vocabulário continha a frase
   de teste literalmente. O resultado (6/6) era ilusório — o modelo podia
   estar só ecoando o prompt. Refeito com lista de termos: 4/7, medição
   honesta.
2. **Eco do prompt em ruído.** Sem `vad_filter`, áudio sem fala fazia o
   Whisper devolver `"JARVIS. Vocabulario. Vocabulario."`. Uma guarda que
   descartava saídas parecidas com o prompt "resolveu" — e quebrou `Jarvis`
   falado sozinho, que é 100% vocabulário. A solução certa era manter
   `vad_filter=True`, que já é o mecanismo desenhado para isso: silêncio e
   ruído puro devolvem string vazia.

### Se ainda errar no seu microfone

O dispositivo padrão do sistema aqui é uma webcam (`EMEET SmartCam S600`) —
microfone distante, SNR baixo, exatamente a faixa que ainda falha. Há 26
entradas disponíveis nesta máquina, incluindo headsets. **Abra
`Conta → Voz e microfone` e selecione o microfone que você usa de fato**: a
escolha é lembrada por conta, por chave estável.

## Microfone: escolha, persistência e teste

`services/audio_devices.py` enumera **todos** os dispositivos de entrada —
nunca "device 0".

**A preferência persistida é a chave estável, não o índice.** O índice do
PortAudio muda quando um USB é conectado/removido ou depois de um reboot;
persistir "device 3" faria o JARVIS abrir o microfone errado em silêncio. A
chave é `host_api + nome` (`"WASAPI:HyperX QuadCast"`), guardada em
`user_settings` por conta.

Se o dispositivo salvo sumir: cai no padrão do sistema e marca
`DeviceResolution.fell_back`, que o HUD mostra como um aviso discreto. Nunca
crasha, nunca troca em silêncio.

`TEST MICROPHONE` grava com auto-stop pelo VAD, mostra o nível em tempo real e
exibe `HEARD: "..."`. Esse texto **não** vai para a IA e **não** vira mensagem
do chat.

## Setup

`python setup.py` prepara os dois engines. Modelo já instalado e válido nunca é
baixado de novo (`WhisperModelManager.is_installed` checa arquivos obrigatórios
+ tamanho mínimo; `VoiceModelManager.is_complete` checa `conf/` + piso de
tamanho).

O download do Whisper vai para um diretório temporário irmão do destino e só é
renomeado depois de validar — uma queda de rede nunca deixa um modelo pela
metade que `is_installed` consideraria pronto. Não há `.zip` envolvido, então
não existe superfície de Zip Slip aqui (diferente do Vosk, que tem proteção
explícita em `_extract_safely`).

**`pytest` nunca baixa modelo real**: todos os testes usam fakes e diretórios
temporários.
