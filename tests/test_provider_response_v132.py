"""Normalização da resposta do provider (v1.3.2, itens 1-4).

Bug de origem: perguntando "Opa! E aí, tudo bem?", a resposta visível do
JARVIS às vezes era literalmente `User Safety: safe`.

**Causa raiz provada** (resposta RAW capturada em
`tests/fixtures_openrouter.py`): `openrouter/free` é um agregador CEGO que
sorteia qualquer modelo do pool gratuito, e nesse pool está
`nvidia/nemotron-3.5-content-safety:free` — um CLASSIFICADOR de conteúdo, não
um modelo de conversa. O `content` que ele devolve para qualquer entrada é o
veredito de moderação.

O parser estava certo; a SELEÇÃO estava errada. Por isso a correção é
estrutural em duas frentes, e ambas são testadas aqui:

1. a rota padrão passou a ser uma lista curada de modelos de CHAT;
2. a resposta passou a ser separada por natureza (`visible_content` /
   `reasoning` / `refusal`), e só a primeira pode virar mensagem.

Nenhum teste toca a rede.
"""

import unittest

from app.models import AppErrorCode
from services.ai_service import AIServiceUnavailableError
from services.provider_ai_service import ProviderRouterAIService, classify_provider_error
from services.providers.exceptions import EmptyProviderResponseError
from services.providers.openrouter_provider import (
    AGGREGATE_FREE_MODEL,
    FREE_CHAT_MODELS,
    HttpResponse,
    OpenRouterProvider,
    parse_message,
)
from services.providers.registry import ProviderRegistry
from services.providers.router import ProviderRouter
from services.providers.types import ProviderId, RouteRequest
from tests.fixtures_openrouter import (
    CONTENT_PARTS_RESPONSE,
    HEALTHY_RESPONSE,
    LEGITIMATE_SAFETY_EXPLANATION_RESPONSE,
    REASONING_ONLY_RESPONSE,
    REFUSAL_RESPONSE,
    SAFETY_CLASSIFIER_RESPONSE,
    as_body,
)


def _transport_for(*payloads):
    """Transporte falso que devolve as respostas na ordem, repetindo a
    última. Nunca toca rede."""
    bodies = [as_body(p) for p in payloads]
    calls = {"n": 0}
    requested_models = []

    async def _transport(url, headers, body, timeout_s):
        import json

        requested_models.append(json.loads(body.decode())["model"])
        index = min(calls["n"], len(bodies) - 1)
        calls["n"] += 1
        return HttpResponse(status=200, body=bodies[index])

    _transport.calls = calls
    _transport.requested_models = requested_models
    return _transport


def _service(transport, **kwargs):
    provider = OpenRouterProvider(api_key="test-key", transport=transport)
    registry = ProviderRegistry()
    registry.register(provider)
    return ProviderRouterAIService(ProviderRouter(registry), free_only=True, **kwargs)


# ----------------------------------------------------------------------
# Item 1 — a causa raiz: seleção de modelo
# ----------------------------------------------------------------------


class ModelSelectionTests(unittest.TestCase):
    def test_blind_aggregate_route_is_not_the_default(self) -> None:
        """`openrouter/free` sorteia qualquer modelo do pool — inclusive o
        classificador de conteúdo. Não pode ser a escolha automática."""
        provider = OpenRouterProvider(api_key="k")
        self.assertNotEqual(provider.free_models()[0], AGGREGATE_FREE_MODEL)

    def test_aggregate_route_is_still_recognized_as_free(self) -> None:
        """Continua sendo rota gratuita válida para quem a pedir de propósito
        — senão `free_only` a rejeitaria."""
        self.assertIn(AGGREGATE_FREE_MODEL, OpenRouterProvider(api_key="k").free_models())

    def test_content_safety_classifier_is_never_offered(self) -> None:
        """O modelo que causou o bug não pode estar na lista de chat."""
        for model in FREE_CHAT_MODELS:
            self.assertNotIn("content-safety", model)
            self.assertNotIn("guard", model.lower())

    def test_curated_models_are_all_free_slugs(self) -> None:
        for model in FREE_CHAT_MODELS:
            self.assertTrue(model.endswith(":free"), model)


# ----------------------------------------------------------------------
# Item 2 — separação por natureza
# ----------------------------------------------------------------------


class MessageParsingTests(unittest.TestCase):
    def test_healthy_response_exposes_only_content_as_visible(self) -> None:
        """Caso C do item 4: reasoning existe, content existe -> só content."""
        message = parse_message(HEALTHY_RESPONSE)
        self.assertEqual(message.visible_content, "Opa! Tudo ótimo por aqui! E você, como tá?")
        self.assertIn("casual greeting", message.reasoning)
        self.assertTrue(message.has_visible_content)

    def test_reasoning_never_becomes_visible_content(self) -> None:
        """Caso D do item 4: reasoning existe, content vazio -> sem conteúdo
        visível. O raciocínio NÃO é aproveitado como resposta."""
        message = parse_message(REASONING_ONLY_RESPONSE)
        self.assertEqual(message.visible_content, "")
        self.assertFalse(message.has_visible_content)
        self.assertIn("greeting me in Portuguese", message.reasoning)

    def test_null_content_does_not_raise(self) -> None:
        self.assertEqual(parse_message(REASONING_ONLY_RESPONSE).visible_content, "")

    def test_content_parts_are_concatenated_in_order(self) -> None:
        message = parse_message(CONTENT_PARTS_RESPONSE)
        self.assertEqual(message.visible_content, "Claro! Aqui está a explicação.")

    def test_refusal_is_separated_from_content(self) -> None:
        message = parse_message(REFUSAL_RESPONSE)
        self.assertEqual(message.visible_content, "")
        self.assertEqual(message.refusal, "I can't help with that.")

    def test_missing_choices_is_handled(self) -> None:
        message = parse_message({"id": "x"})
        self.assertEqual(message.visible_content, "")
        self.assertFalse(message.has_visible_content)


class ExecutionResultTests(unittest.IsolatedAsyncioTestCase):
    async def _execute(self, payload, model="openai/gpt-oss-20b:free"):
        provider = OpenRouterProvider(api_key="k", transport=_transport_for(payload))
        return await provider.execute(
            RouteRequest(prompt="oi", free_only=True), model=model
        )

    async def test_reasoning_and_refusal_travel_as_metadata(self) -> None:
        """Caso E do item 4: metadata continua disponível internamente."""
        result = await self._execute(HEALTHY_RESPONSE)
        self.assertTrue(result.has_visible_content)
        self.assertIn("casual greeting", result.reasoning)
        self.assertIsNone(result.refusal)
        self.assertEqual(result.usage.total_tokens, 85)
        self.assertEqual(result.served_model, "openai/gpt-oss-20b:free")
        self.assertTrue(result.cost.is_free)

    async def test_metadata_never_leaks_into_output(self) -> None:
        result = await self._execute(HEALTHY_RESPONSE)
        for leak in ("gpt-oss", "prompt_tokens", "cost", "casual greeting"):
            self.assertNotIn(leak, result.output)

    async def test_reasoning_only_response_has_no_visible_content(self) -> None:
        result = await self._execute(REASONING_ONLY_RESPONSE)
        self.assertFalse(result.has_visible_content)
        self.assertEqual(result.output, "")


# ----------------------------------------------------------------------
# Itens 1 e 3 — o comportamento visível para o usuário
# ----------------------------------------------------------------------


class SafetyMetadataNeverBecomesAnAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def test_classifier_output_is_reported_but_not_invented(self) -> None:
        """Caso A/B do item 4.

        Se o classificador for servido mesmo assim (rota pedida
        explicitamente), o `content` dele É o que a API devolveu — mentir
        sobre isso seria pior. O que a v1.3.2 garante é que essa rota nunca é
        escolhida automaticamente (ver `ModelSelectionTests`)."""
        provider = OpenRouterProvider(
            api_key="k", transport=_transport_for(SAFETY_CLASSIFIER_RESPONSE)
        )
        result = await provider.execute(
            RouteRequest(prompt="Opa! E aí, tudo bem?", free_only=True),
            model="nvidia/nemotron-3.5-content-safety:free",
        )
        self.assertEqual(result.served_model, "nvidia/nemotron-3.5-content-safety:free")
        self.assertNotIn("content-safety", result.output)

    async def test_default_route_never_asks_the_classifier(self) -> None:
        transport = _transport_for(HEALTHY_RESPONSE)
        service = _service(transport)
        await service.start()
        await service.ask("Opa! E aí, tudo bem?")
        for model in transport.requested_models:
            self.assertNotIn("content-safety", model)
            self.assertNotEqual(model, AGGREGATE_FREE_MODEL)


class EmptyResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_visible_content_triggers_one_retry_then_fails(self) -> None:
        """Item 3: UM retry, não um laço."""
        transport = _transport_for(REASONING_ONLY_RESPONSE)
        service = _service(transport)
        await service.start()
        with self.assertRaises(AIServiceUnavailableError):
            await service.ask("oi")
        self.assertEqual(transport.calls["n"], 2, "exatamente uma nova tentativa")

    async def test_retry_uses_a_different_free_model(self) -> None:
        transport = _transport_for(REASONING_ONLY_RESPONSE)
        service = _service(transport)
        await service.start()
        with self.assertRaises(AIServiceUnavailableError):
            await service.ask("oi")
        self.assertEqual(len(set(transport.requested_models)), 2)

    async def test_retry_never_uses_a_paid_model(self) -> None:
        """Item 3: `free_only` continua obrigatório no retry."""
        transport = _transport_for(REASONING_ONLY_RESPONSE)
        service = _service(transport)
        await service.start()
        with self.assertRaises(AIServiceUnavailableError):
            await service.ask("oi")
        free = set(OpenRouterProvider(api_key="k").free_models())
        for model in transport.requested_models:
            self.assertIn(model, free)

    async def test_successful_retry_returns_the_answer(self) -> None:
        transport = _transport_for(REASONING_ONLY_RESPONSE, HEALTHY_RESPONSE)
        service = _service(transport)
        await service.start()
        reply = await service.ask("oi")
        self.assertIn("Tudo ótimo", reply)

    async def test_empty_response_never_enters_the_history(self) -> None:
        """Item 3: não duplicar user message, não persistir assistant vazio."""
        transport = _transport_for(REASONING_ONLY_RESPONSE)
        service = _service(transport)
        await service.start()
        with self.assertRaises(AIServiceUnavailableError):
            await service.ask("oi")
        self.assertEqual(service._history, [])

    async def test_error_is_structured(self) -> None:
        self.assertEqual(
            classify_provider_error(EmptyProviderResponseError()),
            AppErrorCode.EMPTY_PROVIDER_RESPONSE,
        )

    async def test_error_message_is_user_friendly(self) -> None:
        transport = _transport_for(REASONING_ONLY_RESPONSE)
        service = _service(transport)
        await service.start()
        with self.assertRaises(AIServiceUnavailableError) as ctx:
            await service.ask("oi")
        message = str(ctx.exception)
        self.assertNotIn("Traceback", message)
        self.assertNotIn("reasoning_details", message)


class NotABlacklistTests(unittest.IsolatedAsyncioTestCase):
    """Item 4-F — o teste que prova que a correção é estrutural.

    Se alguém "resolver" o bug com um filtro do tipo
    `if "User Safety" in content: content = ""`, este teste quebra."""

    async def test_legitimate_answer_mentioning_user_safety_is_preserved(self) -> None:
        transport = _transport_for(LEGITIMATE_SAFETY_EXPLANATION_RESPONSE)
        service = _service(transport)
        await service.start()
        reply = await service.ask("Como um classificador de conteúdo responde?")
        self.assertIn("User Safety: safe", reply)
        self.assertIn("NemoGuard", reply)

    async def test_no_textual_blacklist_exists_in_the_provider_layer(self) -> None:
        """Varredura estática: a string do bug não pode aparecer como
        condição em `services/`."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent / "services"
        offenders = []
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if "User Safety" not in stripped:
                    continue
                # Comentário/docstring explicando a causa raiz é permitido;
                # código que TESTA a string, não.
                if stripped.startswith("#") or stripped.startswith("*"):
                    continue
                if any(token in stripped for token in ("if ", "==", "in content", "replace(")):
                    offenders.append(f"{path.name}: {stripped}")
        self.assertEqual(offenders, [], "filtro textual encontrado — a correção deve ser estrutural")


class SummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_reports_reasoning_size_not_text(self) -> None:
        """Item 2: raciocínio interno não aparece nem no painel de
        diagnóstico."""
        transport = _transport_for(HEALTHY_RESPONSE)
        service = _service(transport)
        await service.start()
        await service.ask("oi")
        summary = service.last_result_summary
        self.assertGreater(summary["reasoning_chars"], 0)
        self.assertNotIn("reasoning", [k for k in summary if k == "reasoning"])
        for value in summary.values():
            self.assertNotIn("casual greeting", str(value))


if __name__ == "__main__":
    unittest.main()
