"""Imagens web: relevância, SSRF e injeção por Markdown (v1.3, itens 24-29, 70).

Nenhum acesso real à internet: `validate_image_url()` decide ANTES de
qualquer requisição, que é exatamente o ponto — a barreira é de parsing, não
de rede.
"""

import unittest

from services.markdown_safety import sanitize_markdown
from services.web_images import (
    IMAGE_FETCH_LIMITS,
    UnavailableImageSearchService,
    UnsafeImageUrlError,
    WebImageResult,
    is_safe_image_url,
    is_visual_subject,
    sanitize_results,
    strip_markdown_images,
    validate_image_url,
)


class UrlSafetyTests(unittest.TestCase):
    def test_https_is_accepted(self) -> None:
        self.assertTrue(is_safe_image_url("https://upload.wikimedia.org/burj.jpg"))

    def test_dangerous_schemes_are_blocked(self) -> None:
        for url in (
            "file:///C:/Users/davic/secrets.txt",
            "file://server/share/x.png",
            "data:image/png;base64,AAAA",
            "javascript:alert(1)",
            "ftp://exemplo.com/x.png",
            "http://exemplo.com/x.png",  # sem TLS
        ):
            with self.subTest(url=url):
                self.assertFalse(is_safe_image_url(url))

    def test_localhost_is_blocked(self) -> None:
        for host in ("localhost", "127.0.0.1", "[::1]", "0.0.0.0", "algo.local", "svc.internal"):
            with self.subTest(host=host):
                self.assertFalse(is_safe_image_url(f"https://{host}/x.png"))

    def test_private_networks_are_blocked(self) -> None:
        """SSRF clássico: rede interna e metadata de nuvem."""
        for host in (
            "10.0.0.5",
            "192.168.1.1",
            "172.16.0.1",
            "169.254.169.254",  # metadata de instância
            "100.64.0.1",  # CGNAT
            "[fd00::1]",  # ULA IPv6
            "[fe80::1]",  # link-local IPv6
        ):
            with self.subTest(host=host):
                self.assertFalse(is_safe_image_url(f"https://{host}/x.png"))

    def test_public_ip_literal_is_allowed(self) -> None:
        self.assertTrue(is_safe_image_url("https://93.184.216.34/x.png"))

    def test_embedded_credentials_are_blocked(self) -> None:
        self.assertFalse(is_safe_image_url("https://user:senha@exemplo.com/x.png"))

    def test_non_standard_port_is_blocked(self) -> None:
        self.assertFalse(is_safe_image_url("https://exemplo.com:8080/x.png"))
        self.assertTrue(is_safe_image_url("https://exemplo.com:443/x.png"))

    def test_control_characters_are_blocked(self) -> None:
        for url in ("https://exemplo.com/\nx.png", "https://exemplo.com/ x.png", "https://exem\tplo.com/x"):
            with self.subTest(url=url):
                self.assertFalse(is_safe_image_url(url))

    def test_empty_url_is_blocked(self) -> None:
        self.assertFalse(is_safe_image_url(""))
        self.assertFalse(is_safe_image_url(None))

    def test_error_message_never_echoes_the_url(self) -> None:
        """Devolver a URL hostil para a UI é um vetor por si só."""
        evil = "javascript:alert(document.cookie)"
        with self.assertRaises(UnsafeImageUrlError) as ctx:
            validate_image_url(evil)
        self.assertNotIn("alert", str(ctx.exception))


class FetchLimitTests(unittest.TestCase):
    def test_limits_are_declared_and_conservative(self) -> None:
        """Item 28: os tetos existem como dado auditável, não como números
        mágicos escondidos num cliente HTTP."""
        self.assertLessEqual(IMAGE_FETCH_LIMITS.timeout_seconds, 15)
        self.assertLessEqual(IMAGE_FETCH_LIMITS.max_redirects, 5)
        self.assertLessEqual(IMAGE_FETCH_LIMITS.max_bytes, 10 * 1024 * 1024)
        self.assertLessEqual(IMAGE_FETCH_LIMITS.max_width, 8192)

    def test_only_image_mime_types_are_allowed(self) -> None:
        allowed = IMAGE_FETCH_LIMITS.allowed_mime_types
        self.assertIn("image/png", allowed)
        for bad in ("text/html", "application/javascript", "image/svg+xml", "application/pdf"):
            with self.subTest(bad=bad):
                self.assertNotIn(bad, allowed)


class ResultSanitizationTests(unittest.TestCase):
    def _result(self, **overrides):
        base = dict(
            image_url="https://cdn.example.com/a.jpg",
            thumbnail_url="https://cdn.example.com/a-thumb.jpg",
            source_url="https://example.com/pagina",
            source_name="Example",
            width=800,
            height=600,
        )
        base.update(overrides)
        return WebImageResult(**base)

    def test_safe_result_passes(self) -> None:
        self.assertEqual(len(sanitize_results([self._result()])), 1)

    def test_unsafe_image_url_is_dropped(self) -> None:
        self.assertEqual(sanitize_results([self._result(image_url="file:///etc/passwd")]), [])

    def test_unsafe_source_page_is_dropped(self) -> None:
        self.assertEqual(sanitize_results([self._result(source_url="http://127.0.0.1/")]), [])

    def test_oversized_dimensions_are_dropped(self) -> None:
        self.assertEqual(sanitize_results([self._result(width=99999)]), [])

    def test_never_raises_on_bad_input(self) -> None:
        """Item 70: falha de imagem não pode quebrar a mensagem."""
        self.assertEqual(sanitize_results(None), [])
        self.assertEqual(sanitize_results([]), [])


class RelevanceTests(unittest.TestCase):
    """Itens 24-25: imagem em assunto visual, não em tudo."""

    def test_visual_subjects(self) -> None:
        for query in (
            "Como é o Burj Khalifa?",
            "Como é um axolote?",
            "que planta é essa de folha grande",
            "me mostra a arquitetura do museu do amanhã",
            "qual o formato dessa peça",
        ):
            with self.subTest(query=query):
                self.assertTrue(is_visual_subject(query))

    def test_non_visual_subjects(self) -> None:
        for query in (
            "quanto é 15% de 300",
            "bom dia, tudo bem?",
            "corrige esse bug de python no meu código",
            "explique o conceito de recursão",
            "resuma esse texto para mim",
            "qual a sintaxe de um regex",
        ):
            with self.subTest(query=query):
                self.assertFalse(is_visual_subject(query))

    def test_non_visual_hint_wins_over_visual_hint(self) -> None:
        """"como é a sintaxe" tem "como é" mas não pede foto."""
        self.assertFalse(is_visual_subject("como é a sintaxe de uma função em python"))

    def test_too_short_is_not_visual(self) -> None:
        self.assertFalse(is_visual_subject("oi"))
        self.assertFalse(is_visual_subject(""))


class MarkdownInjectionTests(unittest.TestCase):
    """Item 29: a IA não exibe imagem remota escrevendo Markdown."""

    def test_markdown_image_is_stripped_keeping_alt(self) -> None:
        self.assertEqual(strip_markdown_images("veja ![um gato](https://x/y.png) ali"), "veja um gato ali")

    def test_local_file_image_cannot_be_injected(self) -> None:
        rendered = sanitize_markdown("![](file:///C:/Users/davic/.env)")
        self.assertNotIn("file:", rendered)
        self.assertNotIn("![", rendered)

    def test_localhost_image_cannot_be_injected(self) -> None:
        rendered = sanitize_markdown("![](http://127.0.0.1:8080/admin)")
        self.assertNotIn("127.0.0.1", rendered)

    def test_javascript_image_cannot_be_injected(self) -> None:
        rendered = sanitize_markdown("![](javascript:alert(1))")
        self.assertNotIn("javascript:", rendered)

    def test_remote_https_image_is_also_stripped(self) -> None:
        """Mesmo uma URL "inocente" vaza o IP do usuário como pixel de
        rastreio — toda imagem tem que vir pelo pipeline validado."""
        rendered = sanitize_markdown("![gato](https://tracker.example.com/pixel.png)")
        self.assertNotIn("tracker.example.com", rendered)
        self.assertIn("gato", rendered)

    def test_code_blocks_still_show_the_example_literally(self) -> None:
        """Pedir "me mostre a sintaxe de imagem em Markdown" continua
        funcionando: dentro de código nada é interpretado."""
        rendered = sanitize_markdown("```\n![alt](https://x/y.png)\n```")
        self.assertIn("![alt](https://x/y.png)", rendered)

    def test_regular_links_are_not_touched(self) -> None:
        rendered = sanitize_markdown("veja [a página](https://example.com/pagina)")
        self.assertIn("https://example.com/pagina", rendered)


class ProviderBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """Item 26: não fingir que a pesquisa funciona."""

    async def test_placeholder_is_honest_and_never_raises(self) -> None:
        service = UnavailableImageSearchService()
        self.assertFalse(service.is_available())
        self.assertEqual(await service.search("burj khalifa"), [])

    async def test_factory_returns_placeholder_in_this_version(self) -> None:
        from services.web_images import create_image_search_service

        self.assertFalse(create_image_search_service(object()).is_available())


if __name__ == "__main__":
    unittest.main()
