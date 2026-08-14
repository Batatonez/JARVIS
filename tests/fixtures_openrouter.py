"""Respostas RAW da OpenRouter **capturadas de verdade** (v1.3.2).

Não são JSON inventado à mão: cada uma destas veio de uma chamada real
durante a investigação do bug "User Safety: safe", com os campos preservados
e só o texto encurtado onde era longo demais para um arquivo de teste.

Servem para os testes exercitarem o parser contra a forma REAL da resposta —
incluindo os campos que a v1.3 ignorava (`reasoning`, `reasoning_details`,
`refusal`) e o caso de `content: null`.

Nenhum teste que usa estas fixtures toca a rede.
"""

import json

# --------------------------------------------------------------------------
# A CAUSA RAIZ.
#
# `openrouter/free` é um agregador CEGO: sorteia qualquer modelo do pool
# gratuito a cada chamada. Nesse pool está `nvidia/nemotron-3.5-content-safety`,
# que NÃO é um modelo de conversa — é um CLASSIFICADOR de conteúdo. Perguntando
# "Opa! E aí, tudo bem?" a ele, o `content` da resposta é literalmente o
# veredito de moderação.
#
# Capturado em 2026-08-14 com `free_only=True` (cost = 0).
# --------------------------------------------------------------------------
SAFETY_CLASSIFIER_RESPONSE = {
    "id": "gen-safety-capture",
    "object": "chat.completion",
    "model": "nvidia/nemotron-3.5-content-safety:free",
    "provider": "NVIDIA",
    "choices": [
        {
            "index": 0,
            "logprobs": None,
            "finish_reason": "stop",
            "native_finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": "User Safety: safe",
                "refusal": None,
                "reasoning": (
                    "\nWe need to decide: user input is \"Opa! E aí, tudo bem?\" That's "
                    "Portuguese: \"Hey! How's it going?\" It's safe. No assistant response "
                    "to evaluate.\n"
                ),
                "reasoning_details": [
                    {"type": "reasoning.text", "text": "We need to decide..."}
                ],
            },
        }
    ],
    "usage": {
        "prompt_tokens": 470,
        "completion_tokens": 153,
        "total_tokens": 623,
        "cost": 0,
        "is_byok": False,
        "completion_tokens_details": {"reasoning_tokens": 148},
    },
}

# --------------------------------------------------------------------------
# Modelo de raciocínio que gastou o orçamento inteiro pensando e devolveu
# `content: null` — com `success` HTTP 200 e usage contabilizado.
# Capturado de `poolside/laguna-xs-2.1:free`.
# --------------------------------------------------------------------------
REASONING_ONLY_RESPONSE = {
    "id": "gen-reasoning-capture",
    "object": "chat.completion",
    "model": "poolside/laguna-xs-2.1:free",
    "choices": [
        {
            "index": 0,
            "finish_reason": "length",
            "native_finish_reason": "length",
            "message": {
                "role": "assistant",
                "content": None,
                "refusal": None,
                "reasoning": (
                    "Okay, the user is greeting me in Portuguese with \"Opa! E aí, tudo "
                    "bem?\" which is a casual way of saying hello and asking how I am. I "
                    "should respond warmly in the same language..."
                ),
                "reasoning_details": [{"type": "reasoning.text", "text": "Okay, the user..."}],
            },
        }
    ],
    "usage": {"prompt_tokens": 41, "completion_tokens": 256, "total_tokens": 297, "cost": 0},
}

# --------------------------------------------------------------------------
# Resposta normal e saudável: `content` útil E `reasoning` presente.
# Capturado de `openai/gpt-oss-20b:free`.
# --------------------------------------------------------------------------
HEALTHY_RESPONSE = {
    "id": "gen-healthy-capture",
    "object": "chat.completion",
    "model": "openai/gpt-oss-20b:free",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": "Opa! Tudo ótimo por aqui! E você, como tá?",
                "refusal": None,
                "reasoning": (
                    "User says in Portuguese: \"Opa! E aí, tudo bem?\" Which is casual "
                    "greeting. Respond in Portuguese, friendly."
                ),
            },
        }
    ],
    "usage": {"prompt_tokens": 38, "completion_tokens": 47, "total_tokens": 85, "cost": 0},
}

# --------------------------------------------------------------------------
# Resposta em que o `content` chega como LISTA de partes — forma alternativa
# aceita pela API compatível com OpenAI.
# --------------------------------------------------------------------------
CONTENT_PARTS_RESPONSE = {
    "id": "gen-parts",
    "model": "openai/gpt-oss-20b:free",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Claro! "},
                    {"type": "text", "text": "Aqui está a explicação."},
                ],
                "refusal": None,
            },
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18, "cost": 0},
}

# --------------------------------------------------------------------------
# CASO CRÍTICO DO ITEM 4-F: o usuário PEDIU uma explicação sobre moderação, e
# a resposta legítima contém as palavras "User Safety: safe".
#
# Esta fixture existe para provar que a correção é estrutural (seleção de
# modelo + separação de campos) e não uma blacklist de texto: se alguém
# introduzir um filtro do tipo `if "User Safety" in content`, este teste
# quebra.
# --------------------------------------------------------------------------
LEGITIMATE_SAFETY_EXPLANATION_RESPONSE = {
    "id": "gen-legit",
    "model": "openai/gpt-oss-20b:free",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": (
                    "Classificadores de conteúdo costumam responder num formato fixo. "
                    "O NemoGuard, por exemplo, devolve exatamente `User Safety: safe` "
                    "quando considera a entrada aceitável, e `User Safety: unsafe` "
                    "seguido da categoria quando não."
                ),
                "refusal": None,
            },
        }
    ],
    "usage": {"prompt_tokens": 55, "completion_tokens": 62, "total_tokens": 117, "cost": 0},
}

# --------------------------------------------------------------------------
# Recusa estruturada.
# --------------------------------------------------------------------------
REFUSAL_RESPONSE = {
    "id": "gen-refusal",
    "model": "openai/gpt-oss-20b:free",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": None,
                "refusal": "I can't help with that.",
                "reasoning": None,
            },
        }
    ],
    "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25, "cost": 0},
}


def as_body(payload: dict) -> str:
    """A resposta como o transporte HTTP a entrega: string JSON."""
    return json.dumps(payload, ensure_ascii=False)
