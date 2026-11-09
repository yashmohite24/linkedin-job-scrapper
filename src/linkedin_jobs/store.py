"""Insert new job rows into Supabase and write the run CSV."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

_INSERT_CHUNK = 200
_LOOKUP_CHUNK = 100


class JobStore(Protocol):
    def existing_ids(self, job_ids: list[str]) -> set[str]:
        """Return job ids that are already stored."""

    def insert_new(self, rows: list[dict[str, str | None]]) -> list[dict[str, str | None]]:
        """Insert rows whose job id is new. Leave existing rows unchanged."""

    def upload_csv(self, path: Path, object_key: str) -> None:
        """Upload the run CSV to the private job-exports bucket."""


class SupabaseJobStore:
    def __init__(self, client: object) -> None:
        self._client = client

    def existing_ids(self, job_ids: list[str]) -> set[str]:
        found: set[str] = set()
        for chunk in _chunks(job_ids, _LOOKUP_CHUNK):
            response = (
                self._client.table("job_openings")
                .select("linkedin_job_id")
                .in_("linkedin_job_id", chunk)
                .execute()
            )
            for row in response.data or []:
                job_id = row.get("linkedin_job_id")
                if job_id:
                    found.add(job_id)
        return found

    def insert_new(self, rows: list[dict[str, str | None]]) -> list[dict[str, str | None]]:
        inserted: list[dict[str, str | None]] = []
        for chunk in _chunks(rows, _INSERT_CHUNK):
            response = (
                self._client.table("job_openings")
                .upsert(
                    chunk,
                    on_conflict="linkedin_job_id",
                    ignore_duplicates=True,
                )
                .execute()
            )
            inserted.extend(_rows_from_response(response.data))
        return inserted

    def upload_csv(self, path: Path, object_key: str) -> None:
        self._client.storage.from_("job-exports").upload(
            object_key,
            path.read_bytes(),
            file_options={"content-type": "text/csv", "upsert": "true"},
        )


def build_store(url: str, service_role_key: str) -> SupabaseJobStore:
    from supabase import create_client

    return SupabaseJobStore(create_client(url, service_role_key))


def export_key(now: datetime, timezone_name: str) -> str:
    local = now.astimezone(ZoneInfo(timezone_name))
    return local.strftime("exports/%Y/%m/%d/%H.csv")


def local_export_path(export_dir: Path, key: str) -> Path:
    parts = Path(key).parts
    if parts and parts[0] == "exports":
        return export_dir.joinpath(*parts[1:])
    return export_dir / key


def write_csv(path: Path, rows: list[dict[str, str | None]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: "" if row.get(column) is None else row.get(column) for column in columns})


def _rows_from_response(data: object) -> list[dict[str, str | None]]:
    """Normalize PostgREST's representation body to a list of inserted rows.

    `ON CONFLICT DO NOTHING ... RETURNING *` returns only rows that were inserted.
    An empty body means nothing new was stored, so those rows stay out of the CSV.
    """
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    return []


def _chunks[T](items: list[T], size: int) -> list[list[T]]:
    return [items[index : index + size] for index in range(0, len(items), size)]
