"""Seleção e persistência de microfone (v1.3, itens 13-16, 64).

Nenhum microfone real é aberto: a enumeração do `sounddevice` é substituída
por listas fixas, e a persistência usa banco temporário.
"""

import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from services.audio_devices import (
    SYSTEM_DEFAULT_KEY,
    AudioDevice,
    default_input_device,
    list_input_devices,
    resolve_input_device,
)
from services.local_database import connect
from services.user_repository import UserRepository
from services.user_settings_repository import KEY_MICROPHONE, UserSettingsRepository


def _device(index, name, *, host="WASAPI", default=False, rate=48000):
    return AudioDevice(
        index=index,
        name=name,
        host_api=host,
        max_input_channels=1,
        default_samplerate=rate,
        is_system_default=default,
    )


HYPERX = _device(1, "HyperX QuadCast")
WEBCAM = _device(2, "Microfone (Webcam)", default=True)


class DeviceEnumerationTests(unittest.TestCase):
    def test_lists_all_inputs_and_skips_output_only(self) -> None:
        raw = [
            {"name": "Alto-falantes", "max_input_channels": 0, "hostapi": 0, "default_samplerate": 48000},
            {"name": "HyperX QuadCast", "max_input_channels": 2, "hostapi": 0, "default_samplerate": 48000},
            {"name": "Webcam", "max_input_channels": 1, "hostapi": 0, "default_samplerate": 44100},
        ]
        fake_sd = unittest.mock.Mock()
        fake_sd.query_devices.return_value = raw
        fake_sd.default.device = [2, 5]
        fake_sd.query_hostapis.return_value = [{"name": "MME"}]

        with unittest.mock.patch.dict("sys.modules", {"sounddevice": fake_sd}):
            devices = list_input_devices()

        self.assertEqual([d.name for d in devices], ["HyperX QuadCast", "Webcam"])
        self.assertTrue(devices[1].is_system_default)

    def test_missing_portaudio_returns_empty_never_raises(self) -> None:
        with unittest.mock.patch.dict("sys.modules", {"sounddevice": None}):
            self.assertEqual(list_input_devices(), [])

    def test_query_failure_returns_empty(self) -> None:
        fake_sd = unittest.mock.Mock()
        fake_sd.query_devices.side_effect = RuntimeError("host de áudio caiu")
        with unittest.mock.patch.dict("sys.modules", {"sounddevice": fake_sd}):
            self.assertEqual(list_input_devices(), [])

    def test_key_is_stable_and_distinguishes_host_apis(self) -> None:
        """Item 14: a chave persistida não pode ser o índice."""
        mme = _device(0, "HyperX QuadCast", host="MME")
        wasapi = _device(7, "HyperX QuadCast", host="WASAPI")
        self.assertNotEqual(mme.key, wasapi.key)
        # Mesmo dispositivo, índice diferente -> mesma chave.
        self.assertEqual(mme.key, _device(3, "HyperX QuadCast", host="MME").key)


class DeviceResolutionTests(unittest.TestCase):
    def _with_devices(self, devices):
        return unittest.mock.patch(
            "services.audio_devices.list_input_devices", return_value=devices
        )

    def test_saved_device_is_used_when_present(self) -> None:
        with self._with_devices([HYPERX, WEBCAM]):
            resolution = resolve_input_device(HYPERX.key)
        self.assertEqual(resolution.device, HYPERX)
        self.assertFalse(resolution.fell_back)

    def test_no_preference_uses_system_default(self) -> None:
        with self._with_devices([HYPERX, WEBCAM]):
            resolution = resolve_input_device(SYSTEM_DEFAULT_KEY)
        self.assertEqual(resolution.device, WEBCAM)
        self.assertFalse(resolution.fell_back)

    def test_vanished_device_falls_back_and_flags_it(self) -> None:
        """Item 14: nunca crashar, e avisar em vez de trocar em silêncio."""
        with self._with_devices([WEBCAM]):
            resolution = resolve_input_device(HYPERX.key)
        self.assertEqual(resolution.device, WEBCAM)
        self.assertTrue(resolution.fell_back)
        self.assertEqual(resolution.requested_key, HYPERX.key)

    def test_no_devices_at_all_returns_none_without_raising(self) -> None:
        with self._with_devices([]):
            resolution = resolve_input_device(HYPERX.key)
        self.assertIsNone(resolution.device)
        self.assertFalse(resolution.fell_back)

    def test_default_falls_back_to_first_when_system_marks_none(self) -> None:
        with self._with_devices([_device(0, "Único")]):
            self.assertEqual(default_input_device().name, "Único")


class MicrophonePersistenceTests(unittest.TestCase):
    """Persistência por conta, em banco temporário — nunca o banco real."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.connection = connect(Path(self._tmp.name) / "test.db")
        self.addCleanup(self.connection.close)
        self.users = UserRepository(self.connection)
        self.settings = UserSettingsRepository(self.connection)
        self.alice = self.users.create_user(
            username="alice", display_name="Alice", password="senha-forte-123", email="a@example.com"
        )
        self.bob = self.users.create_user(
            username="bob", display_name="Bob", password="senha-forte-123", email="b@example.com"
        )

    def test_preference_round_trips(self) -> None:
        self.settings.set(self.alice.id, KEY_MICROPHONE, HYPERX.key)
        self.assertEqual(self.settings.get(self.alice.id, KEY_MICROPHONE), HYPERX.key)

    def test_preference_survives_reconnection(self) -> None:
        self.settings.set(self.alice.id, KEY_MICROPHONE, HYPERX.key)
        self.connection.commit()
        again = UserSettingsRepository(self.connection)
        self.assertEqual(again.get(self.alice.id, KEY_MICROPHONE), HYPERX.key)

    def test_preference_is_per_user(self) -> None:
        self.settings.set(self.alice.id, KEY_MICROPHONE, HYPERX.key)
        self.assertIsNone(self.settings.get(self.bob.id, KEY_MICROPHONE))

    def test_set_twice_replaces_instead_of_duplicating(self) -> None:
        self.settings.set(self.alice.id, KEY_MICROPHONE, HYPERX.key)
        self.settings.set(self.alice.id, KEY_MICROPHONE, WEBCAM.key)
        self.assertEqual(self.settings.get(self.alice.id, KEY_MICROPHONE), WEBCAM.key)
        rows = self.connection.execute(
            "SELECT COUNT(*) FROM user_settings WHERE user_id = ?", (self.alice.id,)
        ).fetchone()[0]
        self.assertEqual(rows, 1)

    def test_clear_removes_preference(self) -> None:
        self.settings.set(self.alice.id, KEY_MICROPHONE, HYPERX.key)
        self.settings.clear(self.alice.id, KEY_MICROPHONE)
        self.assertIsNone(self.settings.get(self.alice.id, KEY_MICROPHONE))

    def test_deleting_user_removes_their_settings(self) -> None:
        self.settings.set(self.alice.id, KEY_MICROPHONE, HYPERX.key)
        self.users.delete_user(self.alice.id)
        rows = self.connection.execute(
            "SELECT COUNT(*) FROM user_settings WHERE user_id = ?", (self.alice.id,)
        ).fetchone()[0]
        self.assertEqual(rows, 0)
        self.assertEqual(self.settings.get(self.bob.id, KEY_MICROPHONE), None)


if __name__ == "__main__":
    unittest.main()
