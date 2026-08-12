"""Mascaramento de credenciais — usado em qualquer status/debug/log que
precise indicar "está configurado" sem nunca expor a chave real. Nenhum
outro módulo deste pacote deve formatar uma API key manualmente."""


def mask_secret(value: str | None) -> str:
    """`None`/vazio -> "not_configured". Caso contrário, só os 4 últimos
    caracteres (nunca o suficiente pra reconstruir a chave), ex.: "...ab12".
    Nunca devolve a credencial inteira, mesmo em debug."""
    if not value:
        return "not_configured"
    tail = value[-4:] if len(value) >= 4 else value
    return f"...{tail}"
