"""Command line entrypoint."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

import httpx

from linkedin_jobs.config import ConfigError, load_settings
from linkedin_jobs.scrape import execute_run
from linkedin_jobs.store import build_store

logger = logging.getLogger("linkedin_jobs")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="linkedin-jobs",
        description="Scrape public LinkedIn guest job listings.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Scrape the configured search and store new jobs")
    run_parser.add_argument(
        "--csv-only",
        action="store_true",
        help="Write a local CSV and do not talk to Supabase",
    )
    run_parser.add_argument(
        "--force",
        action="store_true",
        help="Scrape even when the current time is outside the Asia/Kolkata window",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "run":
        return _run(csv_only=args.csv_only, force=args.force)
    return 2


def _run(*, csv_only: bool, force: bool) -> int:
    settings = load_settings()
    store = None
    if not csv_only:
        try:
            settings.require_supabase()
        except ConfigError as exc:
            logger.error("%s", exc)
            return 2
        assert settings.supabase_url is not None
        assert settings.supabase_service_role_key is not None
        store = build_store(settings.supabase_url, settings.supabase_service_role_key)

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        result = execute_run(
            settings=settings,
            client=client,
            store=store,
            sleeper=_sleep,
            now=datetime.now(settings.zone),
            force=force,
            csv_only=csv_only,
        )
    logger.info(result.message)
    return 0 if result.ok else 1


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


if __name__ == "__main__":
    raise SystemExit(main())
