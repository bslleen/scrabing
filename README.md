# Job Discovery

Given a careers page or job board URL, discovers individual job-posting URLs
on that site, handling pagination. Rule-based only: no AI, no API keys, no
external services. Everything runs locally against a single SQLite file.

This is Phase 1 of a larger pipeline (discover -> scrape -> normalize ->
filter -> browse in a local UI). Only discovery is built so far.

## Setup

```
./setup.sh
```

Creates a virtualenv, installs dependencies, and copies `.env.example` to
`.env` (all settings have defaults, so editing `.env` is optional).

## Usage

```
./start.sh
```

or directly:

```
source venv/bin/activate
python3 cli.py discover https://example-company.com/careers
```

Add `--max-pages N` to cap how many paginated pages are followed.

Crawling only prints results for now - it doesn't persist them yet. The
full pipeline's schema lives in `discovery/db.py` / `discovery/schema.sql`
(`sources`, `raw_jobs`, `jobs`, `criteria`, `job_matches` in `data/jobs.db`);
wiring crawler output into `sources`/`raw_jobs` lands in the scraping phase.

## Tests

```
source venv/bin/activate
pytest
```

Tests run against canned HTML fixtures - no real network calls.
