"""Markdown renderizado e sanitizado (v1.2).

O HUD passou a renderizar Markdown de verdade (`TextEdit.MarkdownText`).
O renderizador de Rich Text do Qt também interpreta HTML embutido, então
todo texto que chega lá passa antes por `sanitize_markdown()`.

Regra central: o texto RAW nunca muda (é o que o banco guarda e o que o
botão Copy entrega); a sanitização vive só na camada de exibição.
"""

import unittest

from app.models import Message, MessageRole
from frontend.message_model import MessageListModel, MessageRoles
from services.markdown_safety import contains_raw_html, sanitize_markdown


class MarkdownPreservationTests(unittest.TestCase):
    """Markdown legítimo não pode ser mutilado pela sanitização."""

    def test_headings_are_preserved(self) -> None:
        for text in ("# H1", "## H2", "### H3"):
            self.assertEqual(sanitize_markdown(text), text)

    def test_emphasis_is_preserved(self) -> None:
        for text in ("**negrito**", "*itálico*", "***ambos***", "_sublinhado_"):
            self.assertEqual(sanitize_markdown(text), text)

    def test_lists_are_preserved(self) -> None:
        text = "- um\n- dois\n\n1. primeiro\n2. segundo"
        self.assertEqual(sanitize_markdown(text), text)

    def test_blockquote_and_rule_are_preserved(self) -> None:
        text = "> citação\n\n---"
        self.assertEqual(sanitize_markdown(text), text)

    def test_table_is_preserved(self) -> None:
        text = "| a | b |\n|---|---|\n| 1 | 2 |"
        self.assertEqual(sanitize_markdown(text), text)

    def test_plain_link_is_preserved(self) -> None:
        text = "[JARVIS](https://example.com)"
        self.assertEqual(sanitize_markdown(text), text)

    def test_normal_prose_is_untouched(self) -> None:
        text = "O usuário se chama Davi e usa 5 < 10 como exemplo."
        self.assertEqual(sanitize_markdown(text), text)


class CodeBlockTests(unittest.TestCase):
    """Dentro de código, HTML é conteúdo que o usuário quer LER."""

    def test_fenced_block_content_is_left_intact(self) -> None:
        text = "```html\n<script>ok</script>\n```"
        self.assertEqual(sanitize_markdown(text), text)

    def test_inline_code_content_is_left_intact(self) -> None:
        text = "use `<b>tag</b>` aqui"
        self.assertEqual(sanitize_markdown(text), text)

    def test_tilde_fence_is_supported(self) -> None:
        text = "~~~\n<iframe></iframe>\n~~~"
        self.assertEqual(sanitize_markdown(text), text)

    def test_html_outside_the_block_is_still_neutralized(self) -> None:
        """Regressão de um bug real do próprio sanitizador: o padrão de
        código inline casava os dois primeiros backticks de uma cerca e
        fazia o bloco inteiro perder a proteção."""
        result = sanitize_markdown("<i>fora</i>\n```\n<script>dentro</script>\n```\n<b>fora2</b>")

        self.assertIn("<script>dentro</script>", result)  # dentro: intacto
        self.assertNotIn("<i>fora</i>", result)  # fora: escapado
        self.assertNotIn("<b>fora2</b>", result)
        self.assertIn("&lt;i&gt;fora&lt;/i&gt;", result)


class HtmlNeutralizationTests(unittest.TestCase):
    def test_script_tag_is_escaped_not_executed(self) -> None:
        result = sanitize_markdown("<script>alert(1)</script>")
        self.assertNotIn("<script>", result)
        self.assertIn("&lt;script&gt;", result)

    def test_iframe_is_escaped(self) -> None:
        result = sanitize_markdown('<iframe src="https://evil.example"></iframe>')
        self.assertNotIn("<iframe", result)

    def test_event_handler_attribute_cannot_survive_as_a_tag(self) -> None:
        result = sanitize_markdown('<img src=x onerror="alert(1)">')
        self.assertNotIn("<img", result)
        self.assertIn("&lt;img", result)

    def test_dangerous_url_schemes_are_broken(self) -> None:
        for scheme in ("javascript", "vbscript", "data", "file"):
            result = sanitize_markdown(f"[clique]({scheme}:algo)")
            self.assertNotIn(f"{scheme}:", result, f"{scheme} deveria ser neutralizado")
            self.assertIn("&#58;", result)

    def test_https_links_are_not_broken(self) -> None:
        text = "[ok](https://example.com) e [ok2](http://example.com)"
        self.assertEqual(sanitize_markdown(text), text)

    def test_nothing_is_silently_deleted(self) -> None:
        """Escapamos em vez de remover: o usuário continua vendo o que a IA
        escreveu, só que como texto literal."""
        result = sanitize_markdown("<script>alert(1)</script>")
        self.assertIn("alert(1)", result)

    def test_empty_and_none_like_input(self) -> None:
        self.assertEqual(sanitize_markdown(""), "")

    def test_contains_raw_html_helper(self) -> None:
        self.assertTrue(contains_raw_html("<b>x</b>"))
        self.assertFalse(contains_raw_html("**x**"))


class MessageModelMarkdownTests(unittest.TestCase):
    """O modelo expõe RAW e sanitizado em papéis diferentes."""

    def setUp(self) -> None:
        self.raw = "# Título\n<script>alert(1)</script>\n**fim**"
        self.model = MessageListModel()
        self.model.sync([Message(role=MessageRole.ASSISTANT, content=self.raw)])
        self.index = self.model.index(0, 0)

    def test_content_role_stays_raw_for_copy(self) -> None:
        self.assertEqual(self.model.data(self.index, MessageRoles.ContentRole), self.raw)

    def test_markdown_role_is_sanitized_for_display(self) -> None:
        shown = self.model.data(self.index, MessageRoles.MarkdownRole)
        self.assertNotIn("<script>", shown)
        self.assertIn("&lt;script&gt;", shown)
        self.assertIn("# Título", shown)

    def test_both_roles_are_exposed_to_qml(self) -> None:
        names = {value.decode() for value in self.model.roleNames().values()}
        self.assertIn("content", names)
        self.assertIn("markdown", names)


if __name__ == "__main__":
    unittest.main()
