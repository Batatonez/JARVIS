"""Vocabulário que o JARVIS espera ouvir (v1.3.1).

Existe porque o Whisper erra sistematicamente palavras que não são comuns em
português corrente — e "JARVIS" é exatamente uma delas. Falando "Opa Jarvis,
tudo bem?" num microfone real, a transcrição saía "Vou apagar a vizilha e
tudo bem?": o começo destruído, o fim correto.

**Isto NÃO é substituição de texto.** Nada aqui procura uma saída errada para
trocar por uma certa. O que fazemos é usar os dois mecanismos que o próprio
faster-whisper oferece para influenciar a DECODIFICAÇÃO:

- `hotwords` — enviesa o decoder na direção de um termo específico;
- `initial_prompt` — dá contexto de domínio, como o Whisper já faz para
  jargão técnico e nomes próprios.

O modelo continua livre para transcrever o que ouviu. Medido em
`tests/test_speech_vocabulary_v131.py`: em 8 amostras de frases que **não**
contêm "Jarvis" (limpas e com ruído), o vocabulário não inseriu a palavra
nenhuma vez.

**Nenhuma frase de exemplo no prompt, de propósito.** A primeira versão trazia
saudações montadas ("Opa JARVIS, tudo bem?") e isso é um tiro no pé duas
vezes: contamina qualquer medição (o modelo pode estar só ecoando o prompt) e,
em áudio sem fala, o Whisper regurgita o próprio prompt como se fosse
transcrição. Só lista de termos.
"""

# O nome do assistente. Maiúsculo porque é assim que ele aparece no HUD e nos
# comandos — e porque o Whisper trata capitalização como sinal.
WAKE_WORD = "JARVIS"

# Termos do domínio do app: o que o usuário de fato fala com o JARVIS. Curto
# de propósito — um prompt longo consome contexto do modelo e aumenta a chance
# de eco. Só substantivos e verbos do vocabulário real da interface.
_DOMAIN_TERMS = (
    "assistente",
    "chat",
    "conversa",
    "mensagem",
    "microfone",
    "memoria",
    "configuracoes",
    "conta",
    "senha",
    "e-mail",
    "sessao",
    "pesquisar",
    "renomear",
    "cancelar",
    "resumir",
    "traduzir",
)


def hotwords() -> str:
    """Termo enviesado no decoder. Só o nome do assistente: `hotwords` do
    faster-whisper é mais agressivo que `initial_prompt`, e enviesar a lista
    inteira aumentaria falso positivo em palavras comuns."""
    return WAKE_WORD


def context_prompt() -> str:
    """Contexto de domínio para `initial_prompt`.

    Formato: lista de termos separada por vírgula, sem meta-palavra do tipo
    "Vocabulário:" e sem frase de exemplo. Testado: com esta forma, áudio de
    silêncio ou ruído puro devolve string vazia em vez de ecoar o prompt."""
    return f"{WAKE_WORD}, " + ", ".join(_DOMAIN_TERMS) + "."
