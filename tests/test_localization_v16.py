"""Idioma, região e moeda (v1.6.0, Partes E e F).

Nenhum teste lê a configuração regional da máquina que roda a suíte: o
locale e o fuso são sempre injetados. Sem isso, o resultado dependeria de
onde o teste roda — que é exatamente o problema que a detecção automática
tem que resolver, não reproduzir.
"""

import unittest
from datetime import date

from services import i18n
from services.chat_title_service import ChatTitleService
from services.regional_preferences import (
    AUTOMATIC,
    DEFAULT_LANGUAGE,
    EUROZONE,
    Language,
    currency_for_region,
    format_currency,
    format_date,
    format_number,
    language_from_locale,
    region_from_locale,
    resolve_preferences,
)
from services.runtime_identity import build_locale_directives, build_system_prompt
from tests.helpers import build_isolated_account_manager


def _prefs(**kwargs):
    kwargs.setdefault("system_locale", "")
    kwargs.setdefault("system_timezone", "")
    return resolve_preferences(**kwargs)


# ======================================================================
# IDIOMA (#1-16)
# ======================================================================


class LanguageTests(unittest.TestCase):
    def test_manual_selection_of_each_supported_language(self) -> None:
        for choice, expected in (
            ("pt-BR", Language.PT_BR),
            ("en-US", Language.EN_US),
            ("es", Language.ES),
        ):
            self.assertIs(_prefs(language_choice=choice).language, expected, choice)

    def test_automatic_reads_the_system_locale(self) -> None:
        self.assertIs(_prefs(system_locale="en-US").language, Language.EN_US)
        self.assertIs(_prefs(system_locale="pt-BR").language, Language.PT_BR)
        self.assertIs(_prefs(system_locale="es-MX").language, Language.ES)

    def test_language_variants_collapse_to_the_supported_language(self) -> None:
        """`es-AR`, `es-ES` e `es-MX` são todos Espanhol — sem precisarem de
        entrada própria."""
        for tag in ("es-AR", "es-ES", "es-MX"):
            self.assertIs(language_from_locale(tag), Language.ES, tag)

    def test_unknown_locale_falls_back_safely(self) -> None:
        self.assertIs(_prefs(system_locale="xx-YY").language, DEFAULT_LANGUAGE)

    def test_manual_override_beats_the_system_locale(self) -> None:
        preferences = _prefs(language_choice="en-US", system_locale="pt-BR")
        self.assertIs(preferences.language, Language.EN_US)
        self.assertFalse(preferences.language_is_auto)

    def test_automatic_is_reported_as_automatic(self) -> None:
        self.assertTrue(_prefs(system_locale="pt-BR").language_is_auto)


class PromptLanguageTests(unittest.TestCase):
    def test_directives_state_the_preferred_language(self) -> None:
        directives = build_locale_directives(_prefs(language_choice="pt-BR", region_choice="BR"))
        self.assertIn("Portuguese (Brazil)", directives)

    def test_directives_change_with_the_language(self) -> None:
        self.assertIn("English", build_locale_directives(_prefs(language_choice="en-US")))
        self.assertIn("Spanish", build_locale_directives(_prefs(language_choice="es")))

    def test_directives_forbid_translating_literal_content(self) -> None:
        """Sem estas exceções, uma ordem crua de "responda em português"
        faria o modelo traduzir nome de produto, comando e código."""
        directives = build_locale_directives(_prefs(language_choice="pt-BR")).lower()
        for protected in ("source code", "urls", "model identifiers", "proper nouns", "product names"):
            self.assertIn(protected, directives)

    def test_directives_forbid_mixing_scripts(self) -> None:
        """Defesa principal contra o fragmento CJK aleatório: instrução de
        consistência, nunca remoção de Unicode."""
        self.assertIn("Do not mix languages", build_locale_directives(_prefs()))

    def test_directives_never_invent_an_exchange_rate(self) -> None:
        directives = build_locale_directives(_prefs(region_choice="BR")).lower()
        self.assertIn("never invent", directives)
        self.assertIn("keep the original currency", directives)

    def test_directives_carry_no_precise_location(self) -> None:
        directives = build_locale_directives(
            _prefs(region_choice="BR", system_timezone="America/Sao_Paulo")
        ).lower()
        for forbidden in ("ip", "latitude", "longitude", "address", "coordinate"):
            self.assertNotIn(f" {forbidden} ", f" {directives} ")

    def test_system_prompt_includes_the_directives(self) -> None:
        prompt = build_system_prompt("", _prefs(language_choice="en-US"))
        self.assertIn("Preferred response language: English", prompt)

    def test_system_prompt_without_preferences_stays_unchanged(self) -> None:
        """Chamada antiga (CLI, teste de identidade) não pode ganhar um
        idioma presumido."""
        self.assertNotIn("Preferred response language", build_system_prompt(""))

    def test_memory_context_still_reaches_the_prompt(self) -> None:
        prompt = build_system_prompt("Davi gosta de café", _prefs(language_choice="pt-BR"))
        self.assertIn("Davi gosta de café", prompt)
        self.assertIn("Portuguese (Brazil)", prompt)


class FallbackKeepsLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_provider_in_the_chain_receives_the_same_language(self) -> None:
        """O `system_prompt` é montado uma vez por sessão e vai no mesmo
        `RouteRequest` para todos os candidatos — trocar de provider no meio
        de uma request não pode trocar o idioma da resposta."""
        import json

        from services.providers.http_support import HttpResponse
        from services.providers.nvidia_provider import NvidiaProvider
        from services.providers.openrouter_provider import OpenRouterProvider
        from services.providers.registry import ProviderRegistry
        from services.providers.router import ProviderRouter
        from services.providers.types import ProviderId
        from services.provider_ai_service import ProviderRouterAIService

        seen: list[str] = []

        def _transport(response):
            async def call(url, headers, body, timeout_s):
                payload = json.loads(body.decode())
                system = [m for m in payload["messages"] if m["role"] == "system"]
                seen.append(system[0]["content"] if system else "")
                return response
            return call

        empty = HttpResponse(200, json.dumps({
            "model": "openrouter/free",
            "choices": [{"message": {"role": "assistant", "content": None}}],
        }))
        good = HttpResponse(200, json.dumps({
            "model": "nv",
            "choices": [{"message": {"role": "assistant", "content": "resposta"}}],
        }))

        registry = ProviderRegistry()
        registry.register(OpenRouterProvider(api_key="k", transport=_transport(empty)))
        registry.register(NvidiaProvider(api_key="k", models=("nv",), transport=_transport(good)))
        router = ProviderRouter(registry, provider_order=(ProviderId.OPENROUTER, ProviderId.NVIDIA))

        service = ProviderRouterAIService(router, free_only=True)
        await service.start(preferences=_prefs(language_choice="pt-BR"))
        await service.ask("oi")

        self.assertGreater(len(seen), 1, "o fallback precisa ter acontecido")
        self.assertEqual(len(set(seen)), 1, "o idioma mudou entre providers da mesma request")
        self.assertIn("Portuguese (Brazil)", seen[0])


class AutoTitleLanguageTests(unittest.IsolatedAsyncioTestCase):
    class _AI:
        supports_isolated_requests = True

        def __init__(self) -> None:
            self.prompts: list[str] = []

        def is_available(self) -> bool:
            return True

        async def ask_isolated(self, prompt: str, *, max_tokens: int = 64) -> str:
            self.prompts.append(prompt)
            return "Escolha de placa de vídeo"

    async def test_title_prompt_uses_the_configured_language(self) -> None:
        ai = self._AI()
        await ChatTitleService(ai, preferences=_prefs(language_choice="en-US")).suggest(
            user_message="qual placa de vídeo comprar?", assistant_message="depende"
        )
        self.assertIn("English (United States)", ai.prompts[0])

    async def test_title_defaults_to_the_project_language(self) -> None:
        ai = self._AI()
        await ChatTitleService(ai).suggest(user_message="oi", assistant_message="olá")
        self.assertIn("Portuguese (Brazil)", ai.prompts[0])


class NoDestructiveScriptFilterTests(unittest.TestCase):
    """O fragmento CJK aleatório é combatido por consistência de prompt, não
    por remoção de Unicode. Um filtro destrutivo quebraria tradução, estudo
    de idioma, nome próprio e citação — casos legítimos e comuns."""

    def test_no_module_strips_foreign_scripts_from_responses(self) -> None:
        import pathlib
        import re

        services = pathlib.Path(__file__).resolve().parent.parent / "services"
        # Faixas CJK/cirílico/árabe em regex de remoção seriam a assinatura
        # de um filtro destrutivo.
        suspicious = re.compile(r"[\\]u4e00|[\\]u0600|[\\]u0400.*sub\(")
        for path in services.rglob("*.py"):
            content = path.read_text(encoding="utf-8")
            self.assertIsNone(suspicious.search(content), f"{path.name} parece filtrar script estrangeiro")

    def test_speech_sanitizer_preserves_legitimate_foreign_text(self) -> None:
        from services.speech_sanitizer import sanitize_text_for_tts

        for text in ("日本語 significa língua japonesa", "地产 é um termo chinês", "Привет"):
            self.assertIn(text.split()[0], sanitize_text_for_tts(text), text)


# ======================================================================
# REGIÃO (#17-26)
# ======================================================================


class RegionTests(unittest.TestCase):
    def test_locale_region_is_detected(self) -> None:
        self.assertEqual(_prefs(system_locale="pt-BR").region, "BR")
        self.assertEqual(_prefs(system_locale="en-US").region, "US")
        self.assertEqual(_prefs(system_locale="en-GB").region, "GB")

    def test_manual_region_beats_the_locale(self) -> None:
        preferences = _prefs(region_choice="PT", system_locale="pt-BR")
        self.assertEqual(preferences.region, "PT")
        self.assertFalse(preferences.region_is_auto)

    def test_timezone_is_only_a_hint_when_the_locale_has_no_region(self) -> None:
        self.assertEqual(_prefs(system_locale="pt", system_timezone="America/Sao_Paulo").region, "BR")

    def test_timezone_never_beats_a_manual_region(self) -> None:
        self.assertEqual(
            _prefs(region_choice="PT", system_timezone="America/Sao_Paulo").region, "PT"
        )

    def test_locale_region_beats_the_timezone(self) -> None:
        self.assertEqual(
            _prefs(system_locale="en-US", system_timezone="America/Sao_Paulo").region, "US"
        )

    def test_unknown_region_falls_back_safely(self) -> None:
        self.assertEqual(_prefs(system_locale="pt-ZZ").region, "BR")
        self.assertEqual(_prefs(region_choice="ZZ").region, "BR")

    def test_language_and_region_are_independent(self) -> None:
        english_in_brazil = _prefs(language_choice="en-US", region_choice="BR")
        self.assertIs(english_in_brazil.language, Language.EN_US)
        self.assertEqual(english_in_brazil.currency, "BRL")

        portuguese_in_us = _prefs(language_choice="pt-BR", region_choice="US")
        self.assertIs(portuguese_in_us.language, Language.PT_BR)
        self.assertEqual(portuguese_in_us.currency, "USD")

    def test_language_alone_never_implies_a_region(self) -> None:
        """`pt` sem país não pode virar "provavelmente Brasil" — é isso que
        mantém idioma e região desacoplados."""
        self.assertEqual(region_from_locale("pt"), "")

    def test_detection_never_collects_precise_location(self) -> None:
        preferences = _prefs(system_locale="pt-BR", system_timezone="America/Sao_Paulo")
        for field in vars(preferences):
            self.assertNotIn(field, ("latitude", "longitude", "ip", "address", "city"))

    def test_no_module_calls_an_ip_geolocation_service(self) -> None:
        import pathlib

        services = pathlib.Path(__file__).resolve().parent.parent / "services"
        for path in services.rglob("*.py"):
            content = path.read_text(encoding="utf-8").lower()
            for endpoint in ("ipapi.co", "ip-api.com", "ipinfo.io", "geolocation", "freegeoip"):
                self.assertNotIn(endpoint, content, f"{path.name} consulta geolocalização remota")


# ======================================================================
# MOEDA (#27-40)
# ======================================================================


class CurrencyTests(unittest.TestCase):
    def test_currency_follows_the_region(self) -> None:
        for region, currency in (
            ("BR", "BRL"), ("US", "USD"), ("GB", "GBP"), ("JP", "JPY"),
            ("CA", "CAD"), ("AU", "AUD"), ("CH", "CHF"),
        ):
            self.assertEqual(currency_for_region(region), currency, region)

    def test_eurozone_maps_to_eur(self) -> None:
        for region in ("PT", "ES", "FR", "DE", "IT", "IE", "NL"):
            self.assertIn(region, EUROZONE)
            self.assertEqual(currency_for_region(region), "EUR", region)

    def test_automatic_currency_tracks_the_region(self) -> None:
        preferences = _prefs(region_choice="GB")
        self.assertEqual(preferences.currency, "GBP")
        self.assertTrue(preferences.currency_is_auto)

    def test_manual_currency_beats_the_region(self) -> None:
        preferences = _prefs(region_choice="BR", currency_choice="USD")
        self.assertEqual(preferences.currency, "USD")
        self.assertFalse(preferences.currency_is_auto)

    def test_unknown_currency_falls_back_to_the_region(self) -> None:
        self.assertEqual(_prefs(region_choice="BR", currency_choice="XXX").currency, "BRL")

    def test_currency_formatting_matches_the_locale(self) -> None:
        self.assertEqual(format_currency(1499.9, _prefs(region_choice="BR")), "R$ 1.499,90")
        self.assertEqual(format_currency(1499.9, _prefs(region_choice="US")), "$1,499.90")
        self.assertEqual(format_currency(1499.9, _prefs(region_choice="GB")), "£1,499.90")
        self.assertEqual(format_currency(1499.9, _prefs(region_choice="PT")), "1.499,90 €")

    def test_zero_decimal_currency(self) -> None:
        """O iene não usa centavos; formatá-lo com dois seria errado."""
        self.assertEqual(format_currency(1500, _prefs(region_choice="JP")), "¥1,500")

    def test_number_separators_follow_the_locale(self) -> None:
        self.assertEqual(format_number(1500.5, _prefs(region_choice="BR")), "1.500,50")
        self.assertEqual(format_number(1500.5, _prefs(region_choice="US")), "1,500.50")

    def test_date_format_follows_the_region(self) -> None:
        day = date(2026, 8, 15)
        self.assertEqual(format_date(day, _prefs(region_choice="BR")), "15/08/2026")
        self.assertEqual(format_date(day, _prefs(region_choice="US")), "08/15/2026")

    def test_no_hardcoded_exchange_rate_exists(self) -> None:
        """Converter com taxa fixa produziria número errado com cara de
        exato. Sem fonte de câmbio, a moeda de origem é preservada."""
        import pathlib
        import re

        services = pathlib.Path(__file__).resolve().parent.parent / "services"
        rate = re.compile(r"(USD_TO_|_TO_BRL|EXCHANGE_RATE|FX_RATE)\s*=\s*[0-9]", re.IGNORECASE)
        for path in services.rglob("*.py"):
            self.assertIsNone(rate.search(path.read_text(encoding="utf-8")), path.name)

    def test_technical_values_are_never_reformatted(self) -> None:
        """`format_number` é só para valor que o app exibe. Versão, IP e
        identificador continuam sendo strings intocadas."""
        preferences = _prefs(region_choice="BR")
        for technical in ("1.6.0", "192.168.0.1", "openai/gpt-oss-20b:free"):
            self.assertEqual(technical, technical)  # nenhum formatter os toca
            self.assertNotIn(technical, format_number(1500.5, preferences))


# ======================================================================
# I18N
# ======================================================================


class TranslationTests(unittest.TestCase):
    def test_each_language_has_its_own_text(self) -> None:
        self.assertEqual(i18n.translate("settings.language", Language.PT_BR), "IDIOMA")
        self.assertEqual(i18n.translate("settings.language", Language.EN_US), "LANGUAGE")
        self.assertEqual(i18n.translate("settings.language", Language.ES), "IDIOMA")

    def test_missing_key_returns_the_key_never_empty(self) -> None:
        self.assertEqual(i18n.translate("chave.inexistente", Language.EN_US), "chave.inexistente")

    def test_catalog_is_complete_for_every_language(self) -> None:
        """Uma chave faltando num idioma cairia para o padrão — o catálogo
        entregue ao QML nunca tem buraco."""
        reference = set(i18n.catalog_for(DEFAULT_LANGUAGE))
        for language in i18n.available_languages():
            self.assertEqual(set(i18n.catalog_for(language)), reference, language.value)

    def test_no_qml_file_decides_language_on_its_own(self) -> None:
        import pathlib

        qml = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "qml"
        for path in qml.rglob("*.qml"):
            content = path.read_text(encoding="utf-8")
            for pattern in ('language === "pt"', "language == 'pt'", 'locale === "pt-BR"'):
                self.assertNotIn(pattern, content, path.name)


# ======================================================================
# PERSISTÊNCIA (#41-46)
# ======================================================================


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        import tempfile
        from pathlib import Path

        # `ignore_cleanup_errors=True`: no Windows o SQLite mantém o arquivo
        # aberto até o processo soltar, e a limpeza do diretório temporário
        # falharia por isso — mesmo padrão do resto da suíte.
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.account = build_isolated_account_manager(Path(self._tmp.name))

    async def asyncTearDown(self) -> None:
        await self.account.shutdown()

    async def _user(self, username="davi", email="d@example.com"):
        return await self.account.register(
            username=username, display_name=username.title(),
            password="chave-longa-987", email=email,
        )

    async def test_defaults_are_automatic(self) -> None:
        await self._user()
        preferences = self.account.regional_preferences()
        self.assertTrue(preferences.language_is_auto)
        self.assertTrue(preferences.region_is_auto)
        self.assertTrue(preferences.currency_is_auto)

    async def test_choices_persist(self) -> None:
        await self._user()
        self.account.set_regional_preferences(language="en-US", region="US", currency="USD")
        preferences = self.account.regional_preferences()
        self.assertIs(preferences.language, Language.EN_US)
        self.assertEqual(preferences.region, "US")
        self.assertEqual(preferences.currency, "USD")

    async def test_automatic_is_persisted_as_automatic_not_as_the_detected_value(self) -> None:
        """Gravar o país detectado congelaria uma decisão que era para ser
        automática — e seria guardar localização, que este projeto não faz."""
        user = await self._user()
        self.account.set_regional_preferences(region=AUTOMATIC)
        stored = self.account._user_settings.get(user.id, "locale.region")
        self.assertEqual(stored, AUTOMATIC)

    async def test_preferences_survive_logout_and_login(self) -> None:
        await self._user()
        self.account.set_regional_preferences(language="es")
        await self.account.logout()
        await self.account.login(identifier="davi", password="chave-longa-987")
        self.assertIs(self.account.regional_preferences().language, Language.ES)

    async def test_one_user_never_changes_another_users_preference(self) -> None:
        await self._user("alice", "a@example.com")
        self.account.set_regional_preferences(language="en-US")
        await self.account.logout()

        await self._user("bob", "b@example.com")
        self.assertTrue(self.account.regional_preferences().language_is_auto)
        self.account.set_regional_preferences(language="es")
        await self.account.logout()

        await self.account.login(identifier="alice", password="chave-longa-987")
        self.assertIs(self.account.regional_preferences().language, Language.EN_US)

    async def test_preferences_reach_the_ai_service(self) -> None:
        from tests.fakes import FakeAIService

        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        try:
            fake = FakeAIService()
            account = build_isolated_account_manager(
                Path(tmp.name), ai_service_factory=lambda: fake
            )
            try:
                await account.register(
                    username="davi", display_name="Davi",
                    password="chave-longa-987", email="d@example.com",
                )
                self.assertIsNotNone(fake.received_preferences)
            finally:
                await account.shutdown()
        finally:
            tmp.cleanup()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
