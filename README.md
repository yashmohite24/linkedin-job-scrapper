# LinkedIn jobs scraper

Hourly collector for public LinkedIn guest job listings. The default search is Product Manager roles in India posted in the last 24 hours. New job links are inserted into Supabase. Jobs already stored are left unchanged, including their `first_seen_at`.

Each run also writes a CSV of only the rows inserted on that run.

## What it collects

From LinkedIn’s logged-out guest pages:

- company name
- job link (`https://www.linkedin.com/jobs/view/{id}`)
- designation
- location
- job description
- hiring team, when the public page includes one

`first_seen_at` is set when the row is inserted. `linkedin_listed_at` is the date LinkedIn prints on the card, when that attribute is present.

The scraper does not log in, does not use cookies, and does not try to get around a block. LinkedIn often answers datacenter IPs (including GitHub-hosted runners) with HTTP 429, 999, or a sign-in wall. When that happens the run saves rows already parsed and exits non-zero. The next hour tries again.

## Schedule

GitHub Actions runs at 09:00, 10:00, …, 23:00, and 00:00 Asia/Kolkata. The cron expression is `30 3-18 * * *` UTC. The script checks the clock again and does nothing from 01:00 through 08:59 Asia/Kolkata, so a delayed run cannot scrape in the middle of the night. A run that slips within the midnight hour (00:00–00:59) still runs.

Scheduled workflows pause after about 60 days without a repository update. Actions can also start a few minutes late.

## Setup

Install [uv](https://docs.astral.sh/uv/), then:

```bash
uv sync
cp .env.example .env
```

In the Supabase SQL editor, run [supabase/migrations/001_job_openings.sql](supabase/migrations/001_job_openings.sql). That creates `public.job_openings` and a private Storage bucket named `job-exports`. Put the project URL and service-role key in `.env`. The service-role key bypasses row level security. Keep it in `.env` or in GitHub Actions secrets, not in the repository.

GitHub secrets:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

## Run

```bash
uv run python -m linkedin_jobs run
```

Outside 09:00–00:00 Asia/Kolkata this exits without scraping. Add `--force` to run anyway.

Without Supabase credentials, write a local CSV of every job found in that run. This mode does not check the database, so it cannot skip links you have already stored:

```bash
uv run python -m linkedin_jobs run --csv-only --force
```

CSVs land in `exports/YYYY/MM/DD/HH.csv` (Asia/Kolkata). A configured Supabase run uploads that same object key to the `job-exports` bucket.

Search defaults match `keywords=Product Manager`, `location=India`, `geoId=102713980`, and `f_TPR=r86400`. Override them with `KEYWORDS`, `LOCATION`, `GEO_ID`, and `TIME_FILTER`.

## Tests

```bash
uv run pytest
```

Tests use saved HTML fixtures and a fake HTTP client. They do not call LinkedIn.
