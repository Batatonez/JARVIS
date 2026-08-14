"""`ChatTitleService` — título automático inteligente para uma conversa
(v1.3, itens 19-23).

O problema com o que existia: `derive_title()` copiava as primeiras palavras
da primeira mensagem. "Meu reconhecimento de voz está entendendo tudo errado"
virava exatamente isso na sidebar — verboso e sem virar um rótulo.

Aqui o título é **inferido** a partir da primeira mensagem e da primeira
resposta, com a IA que já existe (`AIService`/`ProviderRouter`). Restrições
não-negociáveis:

- `free_only` continua valendo (item 22): este serviço não escolhe provider
  nem modelo, ele usa o `AIService` que o resto do app já usa, com o mesmo
  kill-switch de custo.
- Prompt curto e orçamento de tokens pequeno — é um rótulo de 2 a 6
  palavras, não uma resposta.
- **Falhar é normal.** Sem IA configurada, sem rota gratuita, timeout ou
  resposta esquisita: cai para o título derivado do texto, e o chat segue.
  Nunca levanta, nunca repete a chamada em laço.
- **Título manual sempre vence** (item 23). Quem decide isso é o
  `ConversationRepository` (`manual_title`), que recusa sobrescrever — a
  regra vive na persistência, não numa checagem que alguém pode esquecer de
  fazer antes de chamar.
"""

import logging
import re

from services.ai_service import AIService

logger = logging.getLogger(__name__)

MIN_WORDS = 2
MAX_WORDS = 6
MAX_TITLE_LENGTH = 60

# O prompt pede o rótulo e nada mais. A pós-limpeza abaixo existe porque
# modelos pequenos gostam de responder "Título: ..." ou com aspas mesmo
# quando o prompt proíbe.
_PROMPT = (
    "Gere um título curto para esta conversa, em português.\n"
    "Regras: de 2 a 6 palavras; sem aspas; sem markdown; sem ponto final; "
    "sem emoji; não copie a mensagem inteira; descreva o ASSUNTO.\n"
    "Responda apenas com o título.\n\n"
    "Mensagem do usuário: {user}\n"
    "Resposta do assistente: {assistant}\n"
)

_CONTEXT_CHARS = 500
# Orçamento pequeno (item 22): um rótulo de 6 palavras cabe folgado. Não é
# tão apertado quanto os 32 tokens que quebraram o smoke test da v1.0 (um
# modelo de raciocínio gastou tudo antes de escrever), mas continua barato.
_MAX_TITLE_TOKENS = 64

_STRIP_PREFIX = re.compile(r"^\s*(t[ií]tulo|title)\s*[:\-]\s*", re.IGNORECASE)
_MARKDOWN_CHARS = re.compile(r"[*_`#>\[\]()]")
# Emoji e símbolos pictográficos — faixas suplementares do Unicode.
_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U00002190-\U000021ff\U00002600-\U000027bf\U0000fe00-\U0000fe0f]"
)


def clean_title(raw: str) -> str:
    """Normaliza o que o modelo devolveu para as regras do item 21.
    Devolve string vazia quando o resultado não serve como título."""
    text = (raw or "").strip()
    # Modelos pequenos às vezes devolvem várias linhas; a primeira não-vazia
    # é o título, o resto é justificativa que ninguém pediu.
    for line in text.splitlines():
        if line.strip():
            text = line.strip()
            break
    else:
        return ""

    text = _STRIP_PREFIX.sub("", text)
    text = _MARKDOWN_CHARS.sub("", text)
    text = _EMOJI.sub("", text)
    text = text.strip().strip('"').strip("'").strip()
    text = " ".join(text.split())
    text = text.rstrip(".,;:!")

    if not text:
        return ""
    words = text.split()
    if len(words) < MIN_WORDS:
        return ""
    if len(words) > MAX_WORDS:
        words = words[:MAX_WORDS]
        text = " ".join(words)
    return text[:MAX_TITLE_LENGTH].strip()


class ChatTitleService:
    def __init__(self, ai_service: AIService) -> None:
        self._ai = ai_service

    async def suggest(self, *, user_message: str, assistant_message: str = "") -> str | None:
        """Título inferido, ou `None` quando não foi possível.

        `None` não é erro — é o caso normal em ambiente sem IA configurada, e
        o chamador simplesmente mantém o título derivado do texto."""
        if not (user_message or "").strip():
            return None
        # `ask_isolated` (e não `ask`): gerar o título pela sessão do usuário
        # injetaria o prompt do título no histórico da conversa. Providers que
        # não suportam requisição isolada simplesmente não geram título — o
        # chat continua com o título derivado do texto.
        if not self._ai.is_available() or not self._ai.supports_isolated_requests:
            return None

        prompt = _PROMPT.format(
            user=(user_message or "").strip()[:_CONTEXT_CHARS],
            assistant=(assistant_message or "").strip()[:_CONTEXT_CHARS],
        )

        try:
            raw = await self._ai.ask_isolated(prompt, max_tokens=_MAX_TITLE_TOKENS)
        except Exception:
            # Uma falha ao nomear um chat nunca pode aparecer para o usuário
            # como erro do chat em si.
            logger.debug("Não foi possível gerar título automático; mantendo o título atual.")
            return None

        title = clean_title(raw or "")
        if not title:
            logger.debug("Título automático descartado por não atender às regras de formato.")
            return None
        return title
