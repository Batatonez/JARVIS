"""Preferências por conta — chave/valor simples sobre a tabela
`user_settings` (v1.3).

Chave/valor de propósito: a alternativa seria uma coluna nova em `users` (e
uma migração) a cada preferência que o HUD passar a lembrar. Como isto guarda
só escolha de interface — nada de credencial, nada que dependa de integridade
referencial — o custo de flexibilidade é o certo aqui.

Tudo é escopado por `user_id`, como todo repositório do projeto: uma conta
nunca lê nem sobrescreve a preferência de outra. E como a tabela tem
`ON DELETE CASCADE`, apagar a conta leva as preferências junto (item 58).

Chaves conhecidas ficam declaradas aqui como constantes, para não virarem
strings soltas espalhadas pelo código.
"""

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Identificador estável do microfone escolhido (ver services/audio_devices.py).
# Guardamos a CHAVE (host API + nome), nunca o índice numérico — o índice muda
# quando um USB é conectado ou depois de um reboot (item 14).
KEY_MICROPHONE = "voice.microphone_key"

# v1.6.0 — preferência regional por conta. Guardamos a ESCOLHA, não o
# resultado: `automatic` fica gravado como `automatic` e a detecção roda de
# novo a cada sessão, então trocar a configuração do Windows (ou viajar) se
# reflete sozinho. Gravar o país detectado congelaria uma decisão que era
# para ser automática — e seria guardar localização, que este projeto não faz.
KEY_LANGUAGE = "locale.language"
KEY_REGION = "locale.region"
KEY_CURRENCY = "locale.currency"

_MAX_VALUE_LENGTH = 512


class UserSettingsRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def get(self, user_id: str, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key)
        ).fetchone()
        return row["value"] if row is not None else default

    def set(self, user_id: str, key: str, value: str) -> None:
        """Grava (ou substitui) uma preferência. Valor é truncado em vez de
        rejeitado: preferência de UI não deve conseguir quebrar um fluxo."""
        cleaned = (value or "")[:_MAX_VALUE_LENGTH]
        self._conn.execute(
            "INSERT INTO user_settings (user_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value, "
            "                                        updated_at = excluded.updated_at",
            (user_id, key, cleaned, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def clear(self, user_id: str, key: str) -> None:
        self._conn.execute(
            "DELETE FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key)
        )
        self._conn.commit()

    def all_for(self, user_id: str) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT key, value FROM user_settings WHERE user_id = ?", (user_id,)
        ).fetchall()
        return {row["key"]: row["value"] for row in rows}
