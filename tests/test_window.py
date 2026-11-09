from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from linkedin_jobs.config import ConfigError, Settings, is_within_window, load_settings
from linkedin_jobs.store import export_key

IST = ZoneInfo("Asia/Kolkata")


def _settings() -> Settings:
    return Settings()


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 11, 9, 9, 0, tzinfo=IST), True),
        (datetime(2026, 11, 9, 8, 59, tzinfo=IST), False),
        (datetime(2026, 11, 9, 15, 0, tzinfo=IST), True),
        (datetime(2026, 11, 9, 23, 59, tzinfo=IST), True),
        (datetime(2026, 11, 10, 0, 0, tzinfo=IST), True),
        (datetime(2026, 11, 10, 0, 40, tzinfo=IST), True),
        (datetime(2026, 11, 10, 1, 0, tzinfo=IST), False),
        (datetime(2026, 11, 9, 3, 30, tzinfo=timezone.utc), True),
        (datetime(2026, 11, 9, 18, 30, tzinfo=timezone.utc), True),
        (datetime(2026, 11, 9, 19, 30, tzinfo=timezone.utc), False),
        (datetime(2026, 11, 9, 2, 29, tzinfo=timezone.utc), False),
    ],
)
def test_default_window(moment, expected):
    assert is_within_window(moment, _settings()) is expected


def test_export_key_uses_kolkata_hour():
    morning = datetime(2026, 11, 9, 3, 30, tzinfo=timezone.utc)
    midnight = datetime(2026, 11, 9, 18, 30, tzinfo=timezone.utc)

    assert export_key(morning, "Asia/Kolkata") == "exports/2026/11/09/09.csv"
    assert export_key(midnight, "Asia/Kolkata") == "exports/2026/11/10/00.csv"


def test_load_settings_reads_environment(monkeypatch):
    monkeypatch.setenv("KEYWORDS", "Designer")
    monkeypatch.setenv("LOCATION", "Bengaluru")
    monkeypatch.setenv("GEO_ID", "123")
    monkeypatch.setenv("TIME_FILTER", "r3600")
    monkeypatch.setenv("WINDOW_START", "10:30")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")

    settings = load_settings()

    assert settings.keywords == "Designer"
    assert settings.location == "Bengaluru"
    assert settings.geo_id == "123"
    assert settings.time_filter == "r3600"
    assert settings.window_start.hour == 10
    assert settings.window_start.minute == 30
    assert settings.supabase_url == "https://example.supabase.co"
    settings.require_supabase()


def test_require_supabase_without_credentials():
    with pytest.raises(ConfigError):
        Settings().require_supabase()
