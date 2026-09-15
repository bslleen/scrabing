# Job Discovery

Given a careers page or job board URL, discovers individual job-posting URLs
on that site, scrapes each one, and stores the results. Rule-based by
default: no AI, no API keys, no external services required. Everything
runs locally against a single SQLite file. The one optional exception is
an AI re-ranking stage (`discovery/ai_filter.py`) that only activates if
you configure an API key - the pipeline is fully usable without it.

Pipeline: discover -> scrape -> normalize -> filter (static, optionally
AI-refined) -> browse in a local UI. Discovery, source registration,
scraping, normalization, static filtering/scoring, and optional AI
re-ranking are built so far.

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

# Define what "relevant" means, then score every normalized job against it:
python3 cli.py add-criteria backend-berlin \
    --keywords "python,django" --exclude-keywords "senior" \
    --locations "Berlin" --min-salary 55000 \
    --ai-prompt "Mid-level backend role using Python, based in Berlin or remote."
python3 cli.py criteria
python3 cli.py match 1

# Optional: re-score the static-relevant jobs with AI (no-op without AI_API_KEY set):
python3 cli.py ai-match 1
```

`discover` accepts `--max-pages N` to cap how many paginated listing pages
are followed. The delay between page fetches is `CRAWL_DELAY_SECONDS` in
`.env`. If a site blocks the crawler (HTTP 403) or rate-limits it (HTTP
429), that's reported clearly and the crawl stops for that site rather than
crashing.

### Data model (`discovery/db.py`, `discovery/schema.sql`)

`sources` (sites to scrape) -> `raw_jobs` (one row per scraped posting) ->
`jobs` (normalized) -> `job_matches` (scored against a saved `criteria`
profile). All in `data/jobs.db`.

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

### Criteria profiles (`discovery/criteria.py`)

`add_criteria`, `list_criteria`, `get_criteria`, `update_criteria`,
`remove_criteria` - named profiles of keywords, exclude-keywords,
locations, minimum salary, and employment types (multiple profiles can be
saved, e.g. one per job search). Also stores a free-text `ai_prompt`
field, persisted here for the AI-assisted matching phase but not read by
anything yet.

### Static matching (`discovery/matcher.py`)

`score_job(job, criteria)` scores one job against one criteria profile
with simple, transparent weighted rules - no AI - and returns `(score,
reasons)` where `reasons` is a list of short human-readable strings (e.g.
`"matched keyword: python"`, `"location match: Berlin"`) for every rule
that fired, positive or negative. Keyword and location matches add
points; a salary below the criteria's minimum subtracts (only when salary
data actually exists - a job with no salary listed is never penalized for
it). An exclude-keyword hit is a hard reject: it forces the score below
any threshold no matter what else matched, since an exclude list means
"never show me this."

`run_static_matching(criteria_id)` scores every job in `jobs` and
inserts/updates one `job_matches` row per job: `'relevant'` at or above
`STATIC_MATCH_THRESHOLD`, `'rejected'` below it (`'pending'` is only the
pre-scoring default). Re-running for the same criteria updates existing
rows instead of duplicating them. This is the only filtering stage when
no AI is configured, so its output has to stand alone as a complete,
usable result - see `STATIC_MATCH_THRESHOLD` and the `*_MATCH_WEIGHT`
settings in `.env` to tune it.

### Optional AI re-ranking (`discovery/ai_filter.py`)

The one deliberate exception to "nothing but page fetches leaves this
machine." Off by default - it only runs if `AI_API_KEY` is set in `.env`,
and even then only on jobs the static filter already marked `'relevant'`
(never the full raw pool), which bounds both cost and token usage.

`run_ai_filtering(criteria_id)` sends each static-relevant job's title/
company/location/description/requirements, together with the criteria
profile's free-text `ai_prompt`, to an LLM (OpenAI Chat Completions by
default, model set by `AI_MODEL`) asking for a 0-100 relevance score and a
one-to-two-sentence reasoning string. Both are written to
`job_matches.ai_score`/`ai_reasoning`. A score below `AI_RELEVANCE_THRESHOLD`
flips that job's status from `'relevant'` to `'rejected'`, with the
reasoning kept so the UI can show why the AI disagreed with the static
filter; this stage never promotes a static-rejected job, only narrows what
the static filter already accepted. Requests run through a small thread
pool (`AI_MAX_CONCURRENT_REQUESTS`) rather than one-by-one, since scoring
is I/O-bound.

If `AI_API_KEY` is empty (the default) or a criteria profile has no
`ai_prompt` set, this logs a message and returns immediately without
error - `python3 cli.py match` alone is always enough to get a usable,
complete result.

## Tests

```
source venv/bin/activate
pytest
```

Tests run against canned HTML fixtures - no real network calls.
