import csv
from datetime import datetime, timezone
from pathlib import Path

import httpx

from linkedin_jobs.config import Settings
from linkedin_jobs.scrape import CSV_COLUMNS, GuestBlocked, execute_run, fetch_text

IST_NOON = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)  # 17:30 IST
IST_NIGHT = datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc)  # 01:30 IST


class MemoryStore:
    def __init__(self, existing: set[str] | None = None, fail_upload: bool = False):
        self.existing = set(existing or set())
        self.inserted: list[dict[str, str | None]] = []
        self.uploads: list[tuple[str, bytes]] = []
        self.lookups: list[list[str]] = []
        self.fail_upload = fail_upload

    def existing_ids(self, job_ids: list[str]) -> set[str]:
        self.lookups.append(job_ids)
        return {job_id for job_id in job_ids if job_id in self.existing}

    def insert_new(self, rows: list[dict[str, str | None]]) -> list[dict[str, str | None]]:
        fresh = [row for row in rows if row["linkedin_job_id"] not in self.existing]
        self.existing.update(row["linkedin_job_id"] for row in fresh)
        self.inserted.extend(fresh)
        return fresh

    def upload_csv(self, path: Path, object_key: str) -> None:
        if self.fail_upload:
            raise RuntimeError("bucket missing")
        self.uploads.append((object_key, path.read_bytes()))


def _card(job_id: str, title: str = "Product Manager") -> str:
    return f"""
    <div class="base-card base-search-card" data-entity-urn="urn:li:jobPosting:{job_id}">
      <a class="base-card__full-link" href="https://in.linkedin.com/jobs/view/role-{job_id}?trk=1"></a>
      <h3 class="base-search-card__title">{title}</h3>
      <h4 class="base-search-card__subtitle"><a href="https://www.linkedin.com/company/acme">Acme</a></h4>
      <span class="job-search-card__location">Pune, Maharashtra, India</span>
      <time class="job-search-card__listdate--new" datetime="2026-11-09"></time>
    </div>
    """


def _detail(job_id: str) -> str:
    return f"""
    <div class="show-more-less-html__markup"><p>Own the roadmap for {job_id}.</p></div>
    <section class="job-details-people-who-can-help">
      <h2>Meet the hiring team</h2>
      <a href="https://www.linkedin.com/in/ada">Ada Lovelace</a>
    </section>
    """


def _client(pages: dict[int, str], details: dict[str, str] | None = None, statuses: dict | None = None):
    details = details or {}
    statuses = statuses or {}
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "seeMoreJobPostings" in str(request.url):
            start = int(request.url.params["start"])
            assert request.url.params["keywords"] == "Product Manager"
            assert request.url.params["geoId"] == "102713980"
            assert request.url.params["f_TPR"] == "r86400"
            status, body = statuses.get(("list", start), (200, pages.get(start, "")))
            return httpx.Response(status, text=body)
        job_id = request.url.path.rstrip("/").split("/")[-1]
        status, body = statuses.get(("detail", job_id), (200, details.get(job_id, _detail(job_id))))
        return httpx.Response(status, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "export_dir": tmp_path / "exports",
        "request_delay_seconds": 0,
        "max_backoffs": 2,
        "max_jobs": 1000,
    }
    values.update(overrides)
    return Settings(**values)


def test_paginates_until_empty_and_writes_only_new_rows(tmp_path):
    pages = {0: _card("1") + _card("2"), 2: _card("3"), 3: "<html></html>"}
    client, calls = _client(pages)
    store = MemoryStore(existing={"2"})
    delays: list[float] = []

    result = execute_run(
        settings=_settings(tmp_path),
        client=client,
        store=store,
        sleeper=delays.append,
        now=IST_NOON,
    )

    assert result.ok
    assert result.listings == 3
    assert result.inserted == 2
    assert [row["linkedin_job_id"] for row in store.inserted] == ["1", "3"]
    detail_calls = [url for url in calls if "/jobPosting/" in url]
    assert detail_calls == [
        "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/1",
        "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/3",
    ]
    assert not any("start=5" in url or "start=4" in url for url in calls)
    with open(result.csv_path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["linkedin_job_id"] for row in rows] == ["1", "3"]
    assert rows[0]["company_name"] == "Acme"
    assert rows[0]["job_url"] == "https://www.linkedin.com/jobs/view/1"
    assert rows[0]["designation"] == "Product Manager"
    assert rows[0]["location"] == "Pune, Maharashtra, India"
    assert rows[0]["description"] == "Own the roadmap for 1."
    assert rows[0]["hiring_team"] == "Ada Lovelace"
    assert rows[0]["first_seen_at"]
    assert rows[0]["search_keywords"] == "Product Manager"
    assert list(rows[0]) == CSV_COLUMNS
    assert store.uploads[0][0] == "exports/2026/11/09/17.csv"
    assert delays.count(0) >= 2


def test_stops_when_a_page_repeats_known_ids(tmp_path):
    pages = {0: _card("1") + _card("2"), 2: _card("1") + _card("2")}
    client, calls = _client(pages)

    result = execute_run(
        settings=_settings(tmp_path),
        client=client,
        store=MemoryStore(),
        sleeper=lambda _seconds: None,
        now=IST_NOON,
    )

    assert result.inserted == 2
    assert not any("start=4" in url for url in calls)


def test_stops_at_the_safety_cap(tmp_path):
    pages = {0: _card("1") + _card("2"), 2: _card("3")}
    client, calls = _client(pages)

    result = execute_run(
        settings=_settings(tmp_path, max_jobs=1),
        client=client,
        store=MemoryStore(),
        sleeper=lambda _seconds: None,
        now=IST_NOON,
    )

    assert result.inserted == 1
    assert result.listings == 1
    assert not any("start=2" in url for url in calls)


def test_blocked_listing_saves_rows_already_parsed(tmp_path):
    pages = {0: _card("10")}
    client, calls = _client(pages, statuses={("list", 1): (429, "slow down")})
    store = MemoryStore()

    result = execute_run(
        settings=_settings(tmp_path),
        client=client,
        store=store,
        sleeper=lambda _seconds: None,
        now=IST_NOON,
    )

    assert result.status == "partial"
    assert [row["linkedin_job_id"] for row in store.inserted] == ["10"]
    list_calls = [url for url in calls if "seeMoreJobPostings" in url and "start=1" in url]
    assert len(list_calls) == 3


def test_csv_only_skips_supabase(tmp_path):
    client, calls = _client({0: _card("7"), 1: ""})
    store = MemoryStore(existing={"7"})

    result = execute_run(
        settings=_settings(tmp_path),
        client=client,
        store=store,
        sleeper=lambda _seconds: None,
        now=IST_NOON,
        csv_only=True,
    )

    assert result.ok
    assert result.inserted == 1
    assert store.lookups == []
    assert store.uploads == []
    assert any("/jobPosting/7" in url for url in calls)


def test_outside_window_does_not_scrape(tmp_path):
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request {request.url}")

    result = execute_run(
        settings=_settings(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(explode)),
        store=MemoryStore(),
        sleeper=lambda _seconds: None,
        now=IST_NIGHT,
    )

    assert result.status == "skipped"
    assert result.csv_path is None


def test_force_runs_outside_the_window(tmp_path):
    client, _calls = _client({0: _card("8"), 1: ""})

    result = execute_run(
        settings=_settings(tmp_path),
        client=client,
        store=MemoryStore(),
        sleeper=lambda _seconds: None,
        now=IST_NIGHT,
        force=True,
    )

    assert result.ok
    assert result.inserted == 1


def test_fetch_backs_off_twice_on_authwall():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(200, text="<html>authwall</html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    delays: list[float] = []
    try:
        fetch_text(
            client,
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
            params={"start": 0},
            sleeper=delays.append,
            max_backoffs=2,
            request_delay_seconds=2.5,
        )
    except GuestBlocked as exc:
        assert "HTTP 200" in str(exc)
    else:
        raise AssertionError("expected GuestBlocked")
    assert attempts["count"] == 3
    assert delays == [2.5, 5.0]


def test_upload_failure_keeps_the_local_csv(tmp_path):
    client, _calls = _client({0: _card("4"), 1: ""})
    store = MemoryStore(fail_upload=True)

    result = execute_run(
        settings=_settings(tmp_path),
        client=client,
        store=store,
        sleeper=lambda _seconds: None,
        now=IST_NOON,
    )

    assert result.status == "partial"
    assert result.inserted == 1
    assert result.csv_path is not None
    assert Path(result.csv_path).is_file()
