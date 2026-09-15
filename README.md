# Job Discovery

Given a careers page or job board URL, discovers individual job-posting URLs
on that site, scrapes each one, and stores the results. Rule-based only: no
AI, no API keys, no external services. Everything runs locally against a
single SQLite file.

Pipeline: discover -> scrape -> normalize -> filter -> browse in a local UI.
Discovery, source registration, scraping, and normalization are built so
far.

## Setup

```
./setup.sh
```

Creates a virtualenv, installs dependencies, and copies `.env.example` to
`.env` (all settings have defaults, so editing `.env` is optional).

## Usage

Interactively: `./start.sh` (a simple menu covering the commands below).

Or directly:

```
source venv/bin/activate

# Try discovery without saving anything:
python3 cli.py discover https://example-company.com/careers

# Register a site, scrape it, then normalize the results:
python3 cli.py add-source https://example-company.com/careers --name "Example Company"
python3 cli.py sources
python3 cli.py scrape 1
python3 cli.py normalize
```

`discover` accepts `--max-pages N` to cap how many paginated listing pages
are followed. The delay between page fetches is `CRAWL_DELAY_SECONDS` in
`.env`. If a site blocks the crawler (HTTP 403) or rate-limits it (HTTP
429), that's reported clearly and the crawl stops for that site rather than
crashing.

### Data model (`discovery/db.py`, `discovery/schema.sql`)

`sources` (sites to scrape) -> `raw_jobs` (one row per scraped posting) ->
`jobs` (normalized) -> `job_matches` (filtered against saved `criteria`,
not built yet). All in `data/jobs.db`.

### Sources (`discovery/sources.py`)

`add_source`, `list_sources`, `remove_source`. Only `type='custom_html'` is
supported so far. If you don't supply a `scrape_config`, a crude default
heuristic is stored instead (any `<a>` tag whose href/text contains "job"
or "career") - meant to be overridden once a site has been inspected by
hand. It isn't consumed by the crawler yet.

### Crawling (`discovery/crawler.py`)

`discover_job_urls(source)` takes any mapping with a "url" key (a `sources`
row, or a plain `{"url": ...}` dict) and returns a deduplicated `list[str]`
of candidate job-posting URLs, following "next page" links.

### Scraping (`discovery/scraper.py`)

`scrape_source(source_id)` discovers a source's job URLs, fetches each one
not already in `raw_jobs` (deduped on `raw_jobs.url`), and stores both the
raw HTML and an extracted `raw_text`. Extraction is JSON-LD-first: if the
page embeds a schema.org `JobPosting` (common on many job boards), its
`description` is used directly; otherwise a handful of common
description-container class names are tried, falling back to the page's
whole visible text as a last resort. Updates `sources.last_scraped_at`
when done.

### Normalization (`discovery/normalizer.py`)

`normalize_raw_job(raw_job_id)` turns a `raw_jobs` row into a structured
`jobs` row: title, company, location, description, requirements, salary
range, and employment type. Same JSON-LD-first approach as scraping -
schema.org `JobPosting` fields are used directly wherever present. Where
they're absent, it falls back to bilingual (English/German) heuristics:
section headings (`Requirements`/`Anforderungen`, `Your Profile`/`Ihr
Profil`, ...) to split description from requirements, keyword matching for
employment type (`Vollzeit`, `Werkstudent`, ...), and a currency-anchored
regex for salary ranges. Every heuristic leaves a field `NULL` rather than
guessing when confidence is low - a missing company name is left missing,
never fabricated. Sets `raw_jobs.status` to `'normalized'`, or `'error'` if
even a title can't be extracted; a raw_jobs row is never silently dropped.

## Tests

```
source venv/bin/activate
pytest
```

Tests run against canned HTML fixtures - no real network calls.
