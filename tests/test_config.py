"""Stage A duman testi: ayarlar yükleniyor ve makul varsayılanlar geliyor mu."""

from __future__ import annotations

from yatirimradar import __version__
from yatirimradar.config import Settings, get_settings


def test_version_string() -> None:
    assert __version__ == "0.0.1"


def test_settings_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.pilot_il == "Ankara"
    assert s.pilot_ilce == "Polatlı"
    assert 0.0 <= s.geocode_confidence_threshold <= 1.0
    assert s.database_url.startswith("postgresql+psycopg://")


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
