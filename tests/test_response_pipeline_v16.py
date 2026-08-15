"""Endurecimento da cadeia de resposta (v1.6.0) — Partes A, B, C e D.

Nenhum teste aqui toca rede, `.env`, banco real, microfone ou engine de TTS:
transportes falsos, providers com chave fictícia e fakes de voz.

As quatro áreas cobertas:

A. Raciocínio interno nunca chega ao usuário (chat, banco, Copy, TTS,
   Regenerate, auto-title), em NENHUM provider.
B. Identificador técnico de modelo/provider nunca aparece cru na UI.
C. Markdown e emoji nunca chegam ao sintetizador de voz.
D. Recusa e estado de geração são escopados à request; nada atravessa turnos.
"""

import json
import unittest

from app.models import AppErrorCode
from services.provider_ai_service import ProviderRouterAIService, classify_provider_error
from services.providers.cerebras_provider import CerebrasProvider
from services.providers.display_names import (
    format_model_name,
    format_provider_name,
    format_route,
)
from services.providers.exceptions import ProviderRefusedError
from services.providers.gemini_provider import GeminiProvider
from services.providers.groq_provider import GroqProvider
from services.providers.http_support import HttpResponse, extract_reasoning, parse_openai_chat_message
from services.providers.mistral_provider import MistralProvider
from services.providers.nvidia_provider import NvidiaProvider
from services.providers.openrouter_provider import OpenRouterProvider
from services.providers.registry import ProviderRegistry
from services.providers.router import ProviderRouter, RequestContext
from services.providers.types import ProviderId, RouteRequest
from services.speech_sanitizer import sanitize_text_for_tts, strip_emoji

# A cadeia de pensamento observada no bug real relatado na v1.6.0.
_LEAKED_REASONING = (
    "Okay, the user just called me. Looking back at the conversation, "
    "from the memory I need to figure out what they want. Since they're annoyed..."
)
_VISIBLE = "Desculpe. Vou direto ao ponto."


class _Transport:
    def __init__(self, *steps) -> None:
        self._steps = list(steps)
        self.calls: list[dict] = []

    async def __call__(self, url: str, headers: dict, body: bytes, timeout_s: float) -> HttpResponse:
        payload = json.loads(body.decode())
        self.calls.append({"model": payload.get("model"), "messages": payload.get("messages")})
        index = min(len(self.calls) - 1, len(self._steps) - 1)
        return self._steps[index]


def _openai_body(*, content, model="m", extra_message_fields=None, finish_reason="stop"):
    message = {"role": "assistant", "content": content}
    if extra_message_fields:
        message.update(extra_message_fields)
    return HttpResponse(
        status=200,
        body=json.dumps(
            {
                "id": "gen-test",
                "model": model,
                "choices": [{"message": message, "finish_reason": finish_reason}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ),
    )


def _registry(*providers) -> ProviderRegistry:
    registry = ProviderRegistry()
    for provider in providers:
        registry.register(provider)
    return registry


def _request(prompt="oi"):
    return RouteRequest(prompt=prompt, free_only=True, max_tokens=64)


# ======================================================================
# PARTE A — REASONING (#1-20)
# ======================================================================


class ReasoningSeparationTests(unittest.IsolatedAsyncioTestCase):
    async def _execute(self, provider):
        router = ProviderRouter(_registry(provider), provider_order=(provider.id,))
        return await router.execute(_request())

    async def test_normal_content_is_shown(self) -> None:
        provider = OpenRouterProvider(
            api_key="k", transport=_Transport(_openai_body(content=_VISIBLE, model="openrouter/free"))
        )
        result = await self._execute(provider)
        self.assertEqual(result.output, _VISIBLE)

    async def test_reasoning_and_content_yields_only_content(self) -> None:
        provider = OpenRouterProvider(
            api_key="k",
            transport=_Transport(
                _openai_body(
                    content=_VISIBLE,
                    model="openrouter/free",
                    extra_message_fields={"reasoning": _LEAKED_REASONING},
                )
            ),
        )
        result = await self._execute(provider)
        self.assertEqual(result.output, _VISIBLE)
        self.assertNotIn("Okay, the user", result.output)
        # Continua disponível para diagnóstico — só nunca como conteúdo.
        self.assertIn("Okay, the user", result.reasoning)

    def test_every_known_reasoning_field_is_captured(self) -> None:
        """Um campo não lido é um campo que ninguém separou — era assim que o
        raciocínio chegava ao chat."""
        for field in (
            "reasoning",
            "reasoning_content",
            "thinking",
            "thought",
            "thoughts",
            "analysis",
            "internal_reasoning",
        ):
            message = parse_openai_chat_message(
                json.loads(
                    _openai_body(
                        content=_VISIBLE, extra_message_fields={field: _LEAKED_REASONING}
                    ).body
                )
            )
            self.assertEqual(message.visible_content, _VISIBLE, field)
            self.assertIn("Okay, the user", message.reasoning, field)

    def test_reasoning_from_several_fields_is_joined_not_dropped(self) -> None:
        joined = extract_reasoning({"reasoning": "um", "thinking": "dois"})
        self.assertIn("um", joined)
        self.assertIn("dois", joined)

    def test_reasoning_alone_is_an_empty_response_never_promoted(self) -> None:
        message = parse_openai_chat_message(
            json.loads(
                _openai_body(content=None, extra_message_fields={"reasoning": _LEAKED_REASONING}).body
            )
        )
        self.assertFalse(message.has_visible_content)
        self.assertTrue(message.has_internal_only_content)
        self.assertEqual(message.visible_content, "")

    async def test_openai_compatible_providers_never_leak_reasoning(self) -> None:
        """NVIDIA, Groq, Cerebras e Mistral compartilham o mesmo parser —
        o teste roda os quatro para que uma divergência futura apareça."""
        for factory in (NvidiaProvider, GroqProvider, CerebrasProvider, MistralProvider):
            provider = factory(
                api_key="k",
                models=("modelo-x",),
                transport=_Transport(
                    _openai_body(
                        content=_VISIBLE,
                        model="modelo-x",
                        extra_message_fields={"reasoning_content": _LEAKED_REASONING},
                    )
                ),
            )
            result = await self._execute(provider)
            self.assertEqual(result.output, _VISIBLE, factory.__name__)
            self.assertNotIn("Okay, the user", result.output, factory.__name__)

    async def test_gemini_thought_parts_are_internal_only(self) -> None:
        body = json.dumps(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": _LEAKED_REASONING, "thought": True},
                                {"text": _VISIBLE},
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {},
            }
        )
        provider = GeminiProvider(
            api_key="k", models=("gemini-3.5-flash",), transport=_Transport(HttpResponse(200, body))
        )
        result = await self._execute(provider)
        # Nunca a concatenação das duas partes.
        self.assertEqual(result.output, _VISIBLE)
        self.assertNotIn("Okay, the user", result.output)

    async def test_fallback_never_leaks_the_previous_providers_reasoning(self) -> None:
        first = OpenRouterProvider(
            api_key="k",
            transport=_Transport(
                _openai_body(
                    content=None, model="openrouter/free",
                    extra_message_fields={"reasoning": _LEAKED_REASONING},
                )
            ),
        )
        second = NvidiaProvider(
            api_key="k", models=("nv",), transport=_Transport(_openai_body(content="Resposta boa", model="nv"))
        )
        router = ProviderRouter(
            _registry(first, second), provider_order=(ProviderId.OPENROUTER, ProviderId.NVIDIA)
        )
        result = await router.execute(_request())
        self.assertEqual(result.output, "Resposta boa")
        self.assertNotIn("Okay, the user", result.output)
        self.assertNotIn("Okay, the user", result.reasoning or "")
        self.assertTrue(result.fallback_used)

    def test_legitimate_text_containing_the_word_reasoning_survives(self) -> None:
        """Prova de que a solução é estrutural: nenhuma blacklist textual."""
        for text in (
            "Meu reasoning sobre esse problema é o seguinte.",
            "From the memory of the trip, I remember the beach.",
            "Looking back at the conversation we had, você tinha razão.",
            "Okay, the user manual says page 12.",
        ):
            message = parse_openai_chat_message(json.loads(_openai_body(content=text).body))
            self.assertEqual(message.visible_content, text)


# ======================================================================
# PARTE B — ROUTE / IDs INTERNOS (#21-30)
# ======================================================================


class DisplayNameTests(unittest.TestCase):
    def test_technical_ids_map_to_friendly_names(self) -> None:
        self.assertEqual(format_model_name("openai/gpt-oss-20b:free"), "GPT-OSS 20B")
        self.assertEqual(format_model_name("nvidia/nemotron-3-ultra-550b-a55b"), "Nemotron 3 Ultra")
        self.assertEqual(format_model_name("z-ai/glm-5.2"), "GLM 5.2")
        self.assertEqual(format_model_name("moonshotai/kimi-k2.6"), "Kimi K2.6")

    def test_provider_ids_map_to_friendly_names(self) -> None:
        self.assertEqual(format_provider_name("openrouter"), "OpenRouter")
        self.assertEqual(format_provider_name("nvidia"), "NVIDIA NIM")
        self.assertEqual(format_provider_name(ProviderId.GEMINI), "Gemini")

    def test_display_name_never_contains_namespace_or_billing_suffix(self) -> None:
        for model_id in (
            "openai/gpt-oss-20b:free",
            "nvidia/nemotron-3-ultra-550b-a55b",
            "z-ai/glm-5.2",
            "algum/modelo-desconhecido-7b:free",
        ):
            display = format_model_name(model_id)
            self.assertNotIn("/", display, model_id)
            self.assertNotIn(":free", display, model_id)
            self.assertNotIn("ROUTE", display)

    def test_unknown_model_still_gets_a_readable_name(self) -> None:
        self.assertEqual(format_model_name("algum/modelo-novo-7b:free"), "Modelo Novo 7B")
        self.assertTrue(format_model_name("qualquer-coisa"))

    def test_empty_input_stays_empty(self) -> None:
        self.assertEqual(format_model_name(""), "")
        self.assertEqual(format_model_name(None), "")
        self.assertEqual(format_provider_name(None), "")

    def test_formatting_never_feeds_back_into_routing(self) -> None:
        """O ID técnico é o que roteia; formatar é operação de apresentação.
        Se o nome amigável voltasse para o router, o roteamento quebraria."""
        technical = "openai/gpt-oss-20b:free"
        self.assertNotEqual(format_model_name(technical), technical)
        provider = OpenRouterProvider(api_key="k", transport=_Transport(_openai_body(content="x")))
        self.assertIn(":free", " ".join(provider.free_models()))

    def test_route_label_joins_both(self) -> None:
        self.assertEqual(
            format_route("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
            "NVIDIA NIM · Nemotron 3 Ultra",
        )


# ======================================================================
# PARTE C — TTS (#31-53)
# ======================================================================


class TtsSanitizerTests(unittest.TestCase):
    def test_markdown_emphasis_is_removed(self) -> None:
        for source, expected in (
            ("**Olá**", "Olá"),
            ("__Olá__", "Olá"),
            ("*Olá*", "Olá"),
            ("_Olá_", "Olá"),
            ("~~Olá~~", "Olá"),
            ("# Olá", "Olá"),
            ("## Olá", "Olá"),
            ("> aviso", "aviso"),
            ("- item", "item"),
            ("1. item", "item"),
        ):
            self.assertEqual(sanitize_text_for_tts(source), expected, source)

    def test_asterisks_are_never_spoken(self) -> None:
        self.assertNotIn("*", sanitize_text_for_tts("**Importante**: leia com **atenção**"))

    def test_link_becomes_its_label(self) -> None:
        self.assertEqual(sanitize_text_for_tts("[OpenAI](https://example.com/muito/longo)"), "OpenAI")

    def test_inline_code_loses_the_backticks(self) -> None:
        self.assertEqual(sanitize_text_for_tts("use `pip install` agora"), "use pip install agora")
        self.assertNotIn("`", sanitize_text_for_tts("`x`"))

    def test_code_fence_is_not_spoken(self) -> None:
        spoken = sanitize_text_for_tts("Veja:\n```python\nprint('oi')\n```\nPronto.")
        self.assertNotIn("`", spoken)
        self.assertNotIn("print", spoken)
        self.assertIn("Veja", spoken)
        self.assertIn("Pronto", spoken)

    def test_emoji_is_removed(self) -> None:
        for source, expected in (
            ("Pronto! ✅", "Pronto!"),
            ("🔥 Importante", "Importante"),
            ("⚠️ Atenção", "Atenção"),
            ("Olá 👋🙂", "Olá"),
        ):
            self.assertEqual(sanitize_text_for_tts(source), expected, source)

    def test_composed_emoji_leaves_no_residue(self) -> None:
        for source in ("família 👨‍👩‍👧", "bandeira 🇧🇷", "tom 👍🏽", "seletor ☀️"):
            spoken = sanitize_text_for_tts(source)
            for residue in ("‍", "️", "︎", "🏽"):
                self.assertNotIn(residue, spoken, source)

    def test_portuguese_accents_are_preserved(self) -> None:
        """Converter para ASCII destruiria o idioma padrão do JARVIS."""
        for word in ("ação", "informação", "você", "não", "coração", "pêssego", "índio", "ôdio"):
            self.assertIn(word, sanitize_text_for_tts(f"a {word} b"), word)

    def test_meaningful_symbols_are_preserved(self) -> None:
        text = "custa R$ 1.499,90 (+15% = €200)"
        self.assertEqual(sanitize_text_for_tts(text), text)

    def test_identifiers_with_underscores_are_not_mutilated(self) -> None:
        self.assertEqual(sanitize_text_for_tts("use nome_da_variavel aqui"), "use nome_da_variavel aqui")

    def test_whitespace_is_normalized(self) -> None:
        self.assertEqual(sanitize_text_for_tts("a  b\n\n\n\nc"), "a b\n\nc")

    def test_punctuation_survives_for_prosody(self) -> None:
        self.assertEqual(sanitize_text_for_tts("Pronto, chega. E agora?"), "Pronto, chega. E agora?")

    def test_only_code_yields_nothing_to_speak(self) -> None:
        self.assertEqual(sanitize_text_for_tts("```\nx = 1\n```"), "")

    def test_strip_emoji_is_available_on_its_own(self) -> None:
        self.assertEqual(strip_emoji("a🔥b"), "ab")


class SpeakPathTests(unittest.IsolatedAsyncioTestCase):
    """A sanitização mora em `VoiceService.speak`, ponto único por onde
    passam auto-speak e replay — nenhum caminho de fala escapa."""

    def _voice(self):
        from services.event_bus import EventBus
        from services.voice_service import VoiceService
        from tests.fakes import FakeTTSService

        from config.settings import Settings

        tts = FakeTTSService()
        service = VoiceService(Settings(), EventBus(), stt=None, tts=tts)
        return service, tts

    async def test_speak_receives_sanitized_text(self) -> None:
        service, tts = self._voice()
        await service.speak("**Importante** 🔥")
        self.assertEqual(tts.spoken[-1], "Importante")

    async def test_nothing_pronounceable_does_not_call_the_engine(self) -> None:
        service, tts = self._voice()
        await service.speak("```\nx=1\n```")
        self.assertEqual(tts.spoken, [])


# ======================================================================
# PARTE D — REFUSAL / ISOLAMENTO DE REQUEST (#54-68)
# ======================================================================


class RequestContextTests(unittest.TestCase):
    def test_a_fresh_context_starts_clean(self) -> None:
        context = RequestContext()
        self.assertFalse(context.visible_content_emitted)
        self.assertEqual(context.fallback_count, 0)
        self.assertIsNone(context.refusal)
        self.assertIsNone(context.safety_metadata)
        self.assertIsNone(context.provider_error)
        self.assertEqual(context.attempts, [])

    def test_contexts_do_not_share_mutable_state(self) -> None:
        """`attempts` como lista precisa de `default_factory`; sem isso, duas
        requests dividiriam a MESMA lista e uma contaminaria a outra."""
        first, second = RequestContext(), RequestContext()
        first.attempts.append("x")
        self.assertEqual(second.attempts, [])

    def test_router_has_no_mutable_generation_state(self) -> None:
        """Nenhum atributo de instância do router pode guardar recusa, erro ou
        contagem de fallback — é isso que impede uma request de contaminar a
        seguinte, inclusive concorrentes."""
        router = ProviderRouter(_registry())
        for attribute in vars(router):
            self.assertNotIn("refus", attribute.lower())
            self.assertNotIn("safety", attribute.lower())
            self.assertNotIn("fallback_count", attribute.lower())
            self.assertNotIn("blocked", attribute.lower())


class RefusalScopeTests(unittest.IsolatedAsyncioTestCase):
    def _service(self, *steps):
        transport = _Transport(*steps)
        provider = OpenRouterProvider(api_key="k", transport=transport)
        router = ProviderRouter(_registry(provider), provider_order=(ProviderId.OPENROUTER,))
        return ProviderRouterAIService(router, free_only=True), transport

    async def test_refusal_is_returned_to_the_user(self) -> None:
        service, _ = self._service(
            _openai_body(
                content="Não posso ajudar com isso.",
                model="openrouter/free",
                extra_message_fields={"refusal": "safety"},
            )
        )
        await service.start()
        self.assertEqual(await service.ask("algo"), "Não posso ajudar com isso.")

    async def test_refusal_never_enters_the_history_sent_to_the_provider(self) -> None:
        """Causa raiz do bug: a recusa virava turno de `assistant` e era
        reenviada, então o modelo lia a própria recusa e se ancorava nela."""
        service, transport = self._service(
            _openai_body(
                content="Desculpe, mas não posso continuar essa conversa.",
                model="openrouter/free",
                extra_message_fields={"refusal": "safety"},
            ),
            _openai_body(content="Claro, posso ajudar.", model="openrouter/free"),
        )
        await service.start()
        await service.ask("mensagem recusada")
        await service.ask("pq nao?")

        segunda_chamada = transport.calls[-1]["messages"]
        enviado = json.dumps(segunda_chamada, ensure_ascii=False)
        self.assertNotIn("não posso continuar", enviado)
        self.assertNotIn("mensagem recusada", enviado)

    async def test_next_request_answers_normally_after_a_refusal(self) -> None:
        service, _ = self._service(
            _openai_body(
                content="Não posso.", model="openrouter/free", extra_message_fields={"refusal": "safety"}
            ),
            _openai_body(content="Claro, posso ajudar.", model="openrouter/free"),
        )
        await service.start()
        await service.ask("recusada")
        self.assertEqual(await service.ask("mensagem normal"), "Claro, posso ajudar.")

    async def test_refusal_without_visible_content_stops_the_chain(self) -> None:
        """Item 40: percorrer a cadeia atrás de um provider mais permissivo
        seria usar o fallback para contornar safety."""
        refusing = OpenRouterProvider(
            api_key="k",
            transport=_Transport(
                _openai_body(
                    content=None, model="openrouter/free", extra_message_fields={"refusal": "safety"}
                )
            ),
        )
        permissive = _Transport(_openai_body(content="eu faria", model="nv"))
        second = NvidiaProvider(api_key="k", models=("nv",), transport=permissive)
        router = ProviderRouter(
            _registry(refusing, second), provider_order=(ProviderId.OPENROUTER, ProviderId.NVIDIA)
        )
        with self.assertRaises(ProviderRefusedError):
            await router.execute(_request())
        self.assertEqual(permissive.calls, [], "o segundo provider não pode ter sido consultado")

    def test_refusal_has_its_own_error_code(self) -> None:
        """Recusa não é indisponibilidade nem erro de configuração — o HUD não
        deve sugerir "cheque a credencial" para uma decisão sobre conteúdo."""
        self.assertIs(
            classify_provider_error(ProviderRefusedError()), AppErrorCode.PROVIDER_REFUSED
        )

    async def test_fallback_count_resets_between_requests(self) -> None:
        first = OpenRouterProvider(
            api_key="k",
            transport=_Transport(
                _openai_body(content=None, model="openrouter/free"),
                _openai_body(content="ok agora", model="openrouter/free"),
            ),
        )
        second = NvidiaProvider(
            api_key="k", models=("nv",), transport=_Transport(_openai_body(content="via nvidia", model="nv"))
        )
        router = ProviderRouter(
            _registry(first, second), provider_order=(ProviderId.OPENROUTER, ProviderId.NVIDIA)
        )
        primeira = await router.execute(_request())
        self.assertTrue(primeira.fallback_used)

        segunda = await router.execute(_request())
        self.assertFalse(segunda.fallback_used, "contagem de fallback vazou entre requests")
        self.assertEqual(segunda.fallback_count, 0)

    async def test_concurrent_requests_do_not_contaminate_each_other(self) -> None:
        import asyncio

        provider = OpenRouterProvider(
            api_key="k", transport=_Transport(_openai_body(content="ok", model="openrouter/free"))
        )
        router = ProviderRouter(_registry(provider), provider_order=(ProviderId.OPENROUTER,))
        results = await asyncio.gather(*(router.execute(_request()) for _ in range(5)))
        for result in results:
            self.assertEqual(result.fallback_count, 0)
            self.assertEqual(result.output, "ok")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
