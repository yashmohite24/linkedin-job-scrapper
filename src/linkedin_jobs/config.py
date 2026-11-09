"""Environment settings and the Asia/Kolkata run window."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_KEYWORDS = "Product Manager"
DEFAULT_LOCATION = "India"
DEFAULT_GEO_ID = "102713980"
DEFAULT_TIME_FILTER = "r86400"
DEFAULT_TIMEZONE = "Asia/Kolkata"


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE pairs into the environment without overriding existing vars."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _parse_time(value: str) -> time:
    hour, minute = value.strip().split(":")
    return time(int(hour), int(minute))


@dataclass(frozen=True)
class Settings:
    keywords: str = DEFAULT_KEYWORDS
    location: str = DEFAULT_LOCATION
    geo_id: str = DEFAULT_GEO_ID
    time_filter: str = DEFAULT_TIME_FILTER
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    timezone: str = DEFAULT_TIMEZONE
    window_start: time = time(9, 0)
    window_end: time = time(0, 0)
    request_delay_seconds: float = 2.5
    max_jobs: int = 1000
    max_backoffs: int = 2
    export_dir: Path = Path("exports")

    def list_params(self, start: int) -> dict[str, str | int]:
        return {
            "keywords": self.keywords,
            "location": self.location,
            "geoId": self.geo_id,
            "f_TPR": self.time_filter,
            "start": start,
        }

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def require_supabase(self) -> None:
        if self.supabase_url and self.supabase_service_role_key:
            return
        raise ConfigError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required. "
            "Use --csv-only to write a local CSV without Supabase."
        )


class ConfigError(Exception):
    pass


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        keywords=os.environ.get("KEYWORDS", DEFAULT_KEYWORDS),
        location=os.environ.get("LOCATION", DEFAULT_LOCATION),
        geo_id=os.environ.get("GEO_ID", DEFAULT_GEO_ID),
        time_filter=os.environ.get("TIME_FILTER", DEFAULT_TIME_FILTER),
        supabase_url=os.environ.get("SUPABASE_URL") or None,
        supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or None,
        timezone=os.environ.get("TIMEZONE", DEFAULT_TIMEZONE),
        window_start=_parse_time(os.environ.get("WINDOW_START", "09:00")),
        window_end=_parse_time(os.environ.get("WINDOW_END", "00:00")),
        request_delay_seconds=float(os.environ.get("REQUEST_DELAY_SECONDS", "2.5")),
        max_jobs=int(os.environ.get("MAX_JOBS", "1000")),
        max_backoffs=int(os.environ.get("MAX_BACKOFFS", "2")),
        export_dir=Path(os.environ.get("EXPORT_DIR", "exports")),
    )


def is_within_window(now: datetime, settings: Settings) -> bool:
    """Return whether `now` falls in the configured local window.

    The default window is 09:00 through 00:00 Asia/Kolkata, both ends included.
    Because the midnight run is scheduled at 00:00 and GitHub Actions can slip,
    the entire 00:00 hour stays inside when the window ends at 00:00. Hours
    01:00 through 08:59 stay outside.
    """
    local = now.replace(tzinfo=settings.zone) if now.tzinfo is None else now.astimezone(settings.zone)
    current = time(local.hour, local.minute, local.second, local.microsecond)
    start = settings.window_start
    end = settings.window_end
    if start == end:
        return True
    if start < end:
        return start <= current <= end
    if end == time(0, 0):
        return current >= start or local.hour == 0
    return current >= start or current <= end
