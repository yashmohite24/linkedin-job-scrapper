"""Fetch every guest search page, then each new job's public detail page."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import httpx

from linkedin_jobs.config import Settings, is_within_window
from linkedin_jobs.parse import JobDetail, ListingCard, parse_detail, parse_listing
from linkedin_jobs.store import JobStore, export_key, local_export_path, write_csv

logger = logging.getLogger("linkedin_jobs")

LIST_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

GUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

Sleeper = Callable[[float], None]
CSV_COLUMNS = [
    "company_name",
    "job_url",
    "designation",
    "location",
    "description",
    "hiring_team",
    "first_seen_at",
    "linkedin_job_id",
    "linkedin_listed_at",
    "search_keywords",
    "search_location",
]


class GuestBlocked(Exception):
    """LinkedIn refused the guest request. The run should stop and keep parsed rows."""


@dataclass
class RunResult:
    status: str
    listings: int = 0
    new_jobs: int = 0
    inserted: int = 0
    csv_path: str | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"skipped", "ok"}


def fetch_text(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, str | int] | None,
    sleeper: Sleeper,
    max_backoffs: int,
    request_delay_seconds: float,
) -> str:
    backoffs = 0
    while True:
        response = client.get(url, params=params, headers=GUEST_HEADERS)
        status = response.status_code
        text = response.text
        if status == 200 and not _is_authwall(text):
            return text
        retryable = status in {429, 999, 401, 403} or (status == 200 and _is_authwall(text))
        if retryable and backoffs < max_backoffs:
            backoffs += 1
            sleeper(request_delay_seconds * backoffs)
            continue
        raise GuestBlocked(f"LinkedIn guest endpoint refused the request (HTTP {status})")


def collect_listings(
    client: httpx.Client,
    settings: Settings,
    sleeper: Sleeper,
) -> tuple[list[ListingCard], GuestBlocked | None]:
    collected: list[ListingCard] = []
    seen: set[str] = set()
    start = 0
    first_request = True
    while len(collected) < settings.max_jobs:
        if not first_request:
            sleeper(settings.request_delay_seconds)
        first_request = False
        try:
            html = fetch_text(
                client,
                LIST_URL,
                params=settings.list_params(start),
                sleeper=sleeper,
                max_backoffs=settings.max_backoffs,
                request_delay_seconds=settings.request_delay_seconds,
            )
        except GuestBlocked as exc:
            logger.warning("Listing fetch stopped at start=%s: %s", start, exc)
            return collected, exc
        cards = parse_listing(html)
        if not cards:
            logger.info("No more listing cards at start=%s", start)
            return collected, None
        new_cards = [card for card in cards if card.job_id not in seen]
        if not new_cards:
            logger.info("Listing page at start=%s repeated known ids; stopping", start)
            return collected, None
        for card in new_cards:
            if len(collected) >= settings.max_jobs:
                break
            seen.add(card.job_id)
            collected.append(card)
        logger.info("Listing start=%s returned %s cards (%s total)", start, len(cards), len(collected))
        if len(collected) >= settings.max_jobs:
            logger.info("Reached the %s job safety cap", settings.max_jobs)
            return collected, None
        start += len(cards)
    return collected, None


def collect_details(
    client: httpx.Client,
    cards: list[ListingCard],
    settings: Settings,
    sleeper: Sleeper,
) -> tuple[list[dict[str, str | None]], GuestBlocked | None]:
    rows: list[dict[str, str | None]] = []
    seen_at = datetime.now(timezone.utc).isoformat()
    blocked: GuestBlocked | None = None
    for index, card in enumerate(cards):
        if index:
            sleeper(settings.request_delay_seconds)
        try:
            html = fetch_text(
                client,
                DETAIL_URL.format(job_id=card.job_id),
                params=None,
                sleeper=sleeper,
                max_backoffs=settings.max_backoffs,
                request_delay_seconds=settings.request_delay_seconds,
            )
        except GuestBlocked as exc:
            logger.warning("Detail fetch stopped on job %s: %s", card.job_id, exc)
            blocked = exc
            break
        detail = parse_detail(html)
        rows.append(_row(card, detail, settings, seen_at))
    return rows, blocked


def execute_run(
    *,
    settings: Settings,
    client: httpx.Client,
    store: JobStore | None,
    sleeper: Sleeper,
    now: datetime,
    force: bool = False,
    csv_only: bool = False,
) -> RunResult:
    if not force and not is_within_window(now, settings):
        message = (
            f"Outside {settings.window_start.strftime('%H:%M')}–"
            f"{settings.window_end.strftime('%H:%M')} {settings.timezone}; not scraping."
        )
        logger.info(message)
        return RunResult(status="skipped", message=message)

    cards, list_blocked = collect_listings(client, settings, sleeper)
    if csv_only or store is None:
        existing: set[str] = set()
    else:
        existing = store.existing_ids([card.job_id for card in cards])
    new_cards = [card for card in cards if card.job_id not in existing]
    logger.info("%s listings, %s not yet stored", len(cards), len(new_cards))

    rows, detail_blocked = collect_details(client, new_cards, settings, sleeper)
    blocked = list_blocked or detail_blocked

    if csv_only or store is None:
        inserted = rows
    else:
        inserted = store.insert_new(rows)

    csv_path = None
    upload_error = None
    if rows or not blocked:
        key = export_key(now, settings.timezone)
        path = local_export_path(settings.export_dir, key)
        write_csv(path, inserted, CSV_COLUMNS)
        csv_path = str(path)
        logger.info("Wrote %s new rows to %s", len(inserted), path)
        if not csv_only and store is not None:
            try:
                store.upload_csv(path, key)
            except Exception as exc:
                upload_error = exc
                logger.error("CSV upload failed: %s", exc)

    if blocked is not None or upload_error is not None:
        message = "; ".join(str(item) for item in (blocked, upload_error) if item)
        return RunResult(
            status="partial",
            listings=len(cards),
            new_jobs=len(new_cards),
            inserted=len(inserted),
            csv_path=csv_path,
            message=message,
        )
    return RunResult(
        status="ok",
        listings=len(cards),
        new_jobs=len(new_cards),
        inserted=len(inserted),
        csv_path=csv_path,
        message="Scrape finished",
    )


def _row(card: ListingCard, detail: JobDetail, settings: Settings, seen_at: str) -> dict[str, str | None]:
    designation = card.designation or detail.designation
    listed = card.linkedin_listed_at.isoformat() if card.linkedin_listed_at else None
    return {
        "linkedin_job_id": card.job_id,
        "company_name": card.company_name,
        "job_url": card.job_url,
        "designation": designation,
        "location": card.location,
        "description": detail.description,
        "hiring_team": detail.hiring_team,
        "linkedin_listed_at": listed,
        "first_seen_at": seen_at,
        "search_keywords": settings.keywords,
        "search_location": settings.location,
    }


def _is_authwall(text: str) -> bool:
    lowered = text.lower()
    if "authwall" in lowered or "/checkpoint/challenge" in lowered:
        return True
    has_jobs = "base-search-card" in lowered or "description__text" in lowered or "show-more-less-html__markup" in lowered
    return "captcha" in lowered and not has_jobs
