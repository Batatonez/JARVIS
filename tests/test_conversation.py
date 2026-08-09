import unittest

from app.conversation import Conversation
from app.models import MessageRole


class ConversationTests(unittest.TestCase):
    def test_add_returns_message_and_appends_to_history(self) -> None:
        conversation = Conversation()

        message = conversation.add(MessageRole.USER, "olá")

        self.assertEqual(message.role, MessageRole.USER)
        self.assertEqual(message.content, "olá")
        self.assertEqual(conversation.messages, [message])

    def test_messages_preserves_order(self) -> None:
        conversation = Conversation()
        conversation.add(MessageRole.USER, "primeira")
        conversation.add(MessageRole.ASSISTANT, "segunda")
        conversation.add(MessageRole.USER, "terceira")

        contents = [m.content for m in conversation.messages]

        self.assertEqual(contents, ["primeira", "segunda", "terceira"])

    def test_messages_returns_a_copy(self) -> None:
        conversation = Conversation()
        conversation.add(MessageRole.USER, "olá")

        snapshot = conversation.messages
        snapshot.append("mutação externa não deveria afetar o histórico real")

        self.assertEqual(len(conversation.messages), 1)

    def test_clear_empties_history(self) -> None:
        conversation = Conversation()
        conversation.add(MessageRole.USER, "olá")

        conversation.clear()

        self.assertEqual(conversation.messages, [])

    def test_history_is_capped_at_max_messages(self) -> None:
        conversation = Conversation(max_messages=3)
        for i in range(5):
            conversation.add(MessageRole.USER, f"mensagem {i}")

        contents = [m.content for m in conversation.messages]

        self.assertEqual(contents, ["mensagem 2", "mensagem 3", "mensagem 4"])


if __name__ == "__main__":
    unittest.main()
