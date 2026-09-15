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

Add `--max-pages N` to cap how many paginated pages are followed. The delay
between page fetches is set by `CRAWL_DELAY_SECONDS` in `.env`. If a site
blocks the crawler (HTTP 403) or rate-limits it (HTTP 429), that's reported
clearly and the crawl stops for that site rather than crashing.

`discovery.crawler.discover_job_urls(source)` takes any mapping with a
"url" key (a `sources` row, or a plain `{"url": ...}` dict) and returns a
deduplicated `list[str]` of candidate job-posting URLs. Crawling only
prints results for now - it doesn't persist them yet. The full pipeline's
schema lives in `discovery/db.py` / `discovery/schema.sql` (`sources`,
`raw_jobs`, `jobs`, `criteria`, `job_matches` in `data/jobs.db`).

`discovery/sources.py` registers sites to scrape (`add_source`,
`list_sources`, `remove_source`). Only `type='custom_html'` is supported so
far: a site scraped via CSS selectors in `scrape_config`. If you don't
supply one, a crude default heuristic is stored instead (any `<a>` tag
whose href/text contains "job" or "career") - meant to be overridden once
a site has been inspected by hand. Wiring the crawler and a source's
`scrape_config` together lands in the scraping phase.

## Tests

```
source venv/bin/activate
pytest
```

Tests run against canned HTML fixtures - no real network calls.
