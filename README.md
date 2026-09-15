# Job Discovery

Given a careers page or job board URL, discovers individual job-posting URLs
on that site, scrapes each one, normalizes it into structured fields, scores
it against what you're looking for, and lets you browse the results in a
local web UI. Rule-based by default: no AI, no API keys, no accounts, no
external services required. Everything runs locally against a single SQLite
file. The one optional exception is an AI re-ranking stage that only
activates if you configure your own API key - the pipeline is fully usable,
and gives a complete result, without it.

Pipeline: discover -> scrape -> normalize -> filter (static, optionally
AI-refined) -> browse in a local web UI.

## Setup

```
./setup.sh          # macOS/Linux
setup.bat            # Windows
```

One-click: creates a virtualenv, installs dependencies, and copies
`.env.example` to `.env`. Every setting in `.env` has a built-in default, so
you don't need to edit anything to get started - the only thing you'd add
is an `AI_API_KEY` if you want the optional AI stage.

## Everyday usage

The fastest path - one command runs the whole pipeline against a site:

```
python3 discover.py "https://example-company.com/careers" --criteria default
```

This discovers job postings on that page (following pagination), scrapes
each one, normalizes it into structured fields, scores it against a saved
criteria profile called "default" (skip `--criteria` to discover/scrape/
normalize without scoring anything), and - unless a criteria profile has no
`ai_prompt` set, or `--no-ai` is passed, or no `AI_API_KEY` is configured -
re-scores the static-relevant results with AI. It prints a progress line
after each stage:

```
==> https://example-company.com/careers
    Registered new source (id 1) for https://example-company.com/careers
    Scraped 6 new job posting(s)
    Normalized 6 job(s), 0 error(s)
    Static match: 2 relevant, 4 rejected
    AI filter: 2 scored, 1 flipped to rejected, 0 error(s)

Summary:
  scraped:    6 new
  normalized: 6 (0 error(s))
  static:     2 relevant, 4 rejected
  AI filter:  2 scored, 1 flipped to rejected, 0 error(s)

Browse the results: python3 -m webui.app
```

You'll need a criteria profile before that's useful:

```
python3 cli.py add-criteria default \
    --keywords "python,django" --exclude-keywords "senior" \
    --locations "Berlin" --min-salary 55000 \
    --ai-prompt "Mid-level backend role using Python, based in Berlin or remote."
```

Then browse everything - and add, edit, or delete rows by hand - in the web
UI:

```
python3 -m webui.app
```

Or skip the CLI entirely: `./start.sh` (macOS/Linux), `start.bat` (Windows),
or double-click `start.command` (macOS) opens a numbered menu covering all
of the above, plus the more granular per-stage commands below.

### Per-stage commands

`discover.py` runs everything at once; `cli.py` exposes each stage on its
own, for when you want to inspect or re-run just one step:

```
python3 cli.py discover <url>              # crawl + print candidate URLs, no saving
python3 cli.py add-source <url> [--name]   # register a site
python3 cli.py sources                     # list registered sources
python3 cli.py scrape <source_id>          # discover + scrape a registered source
python3 cli.py normalize [raw_job_id]      # normalize one row, or every pending one
python3 cli.py add-criteria <label> ...    # create a criteria profile
python3 cli.py criteria                    # list criteria profiles
python3 cli.py match <criteria_id>         # static-score every job against one profile
python3 cli.py ai-match <criteria_id>      # AI re-score the static-relevant results
```

`discover` and `scrape` accept `--max-pages`/politeness delays via `.env`
(`MAX_PAGES_PER_SITE`, `CRAWL_DELAY_SECONDS`). If a site blocks the crawler
(HTTP 403) or rate-limits it (HTTP 429), that's reported clearly and the
crawl stops for that site rather than crashing.

## How it fits together

### Data model (`discovery/db.py`, `discovery/schema.sql`)

`sources` (sites to scrape) -> `raw_jobs` (one row per scraped posting) ->
`jobs` (normalized) -> `job_matches` (scored against a saved `criteria`
profile). All in `data/jobs.db`, gitignored, created on first use.

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
saved, e.g. one per job search - `discover.py --criteria <label>` picks one
by its label). Also stores a free-text `ai_prompt` field, used by
`discovery/ai_filter.py`'s optional AI re-ranking.

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

**The one part of this pipeline that makes a network call to an LLM
provider, and the only reason you'd ever need your own API key.** Off by
default - it only runs if `AI_API_KEY` is set in `.env`, and even then only
on jobs the static filter already marked `'relevant'` (never the full raw
pool), which bounds both cost and token usage.

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
error - static matching alone is always enough to get a usable, complete
result.

### One-command pipeline (`discover.py`, `discovery/pipeline.py`)

`discover.py` is a thin CLI wrapper around `discovery/pipeline.py`'s
`run_pipeline()`, which chains everything above for one URL: register (or
reuse) a source, scrape, normalize, and - if `--criteria <label>` names an
existing profile - static-match and (unless `--no-ai`) AI-filter. Re-running
against a URL you've already registered reuses that `sources` row rather
than creating a duplicate.

### Web UI (`webui/`)

```
python3 -m webui.app
```

or option 2 in `./start.sh` / `start.bat`. Opens on `http://127.0.0.1:5000`
(set `WEBUI_HOST`/`WEBUI_PORT` in `.env` to change that - on macOS, port
5000 is often already taken by the AirPlay Receiver system service, so you
may need to pick a different port). A Flask app, server-rendered with
Jinja - no build step, no JS framework, and it never loads anything from a
CDN (the one local script handles flash-message dismissal, the delete
confirmation dialog, and disabling "Run" buttons while they work).

Covers every table: browse sources/jobs/matches/criteria, add a source or
a job by hand, edit or delete a criteria profile, and trigger "run
discovery" / "run matching" / "AI re-score" from the browser instead of
the CLI. Deleting a job or a criteria profile also removes its
`job_matches` rows in the same transaction (both have a foreign key
pointing at them with no `ON DELETE CASCADE`, so this is done explicitly
in `discovery/db.py` rather than left to fail). Every destructive action
goes through a confirmation dialog and a POST, and every action ends in a
flash message plus a redirect (so refreshing the result page never
re-submits it).

## Dependencies, and why

Every third-party package this project uses, and what it's for - nothing
here is incidental:

| Package | Used for | Why this one |
|---|---|---|
| `requests` | Every HTTP fetch (crawling, scraping, and the AI stage's API call) | Simple, synchronous, no event loop to reason about - right fit for a tool that fetches a page, waits, fetches the next one |
| `beautifulsoup4` | Parsing HTML (finding links, JSON-LD blocks, description containers) | Standard, no compiled dependencies, tolerant of the malformed HTML real sites produce |
| `python-dotenv` | Loading `.env` into `config.py` | The whole project's settings live in one place, read once at startup |
| `flask` | The local web UI | A thin server-rendered app is all this needs - no API layer, no client-side framework, no build step |
| `pytest` | The test suite | - |

That's the whole list. No browser-automation library (Playwright, Selenium)
is used for scraping - `requests` + `beautifulsoup4` handle every site this
tool targets, and adding a headless browser would be a heavy, slower
dependency for sites that don't need one. No AI SDK is installed either -
`discovery/ai_filter.py` calls OpenAI's REST API directly via `requests`,
which is enough for what it does (one JSON request per job) without pulling
in a client library.

**Only two things ever leave your machine:** page fetches (discovery,
scraping) to whatever site you point this at, and - only if you've set
`AI_API_KEY` - one request per static-relevant job to your configured AI
provider. Nothing else: no telemetry, no analytics, no accounts, no data
sent anywhere else.

## Tests

```
source venv/bin/activate
pytest
```

Tests run against canned HTML fixtures and a temp SQLite file per test -
no real network calls. The web UI is tested with Flask's test client
(`tests/test_webui.py`), not a browser. `tests/test_discover_cli.py` runs
the full pipeline through `discover.py`'s actual entry point against the
same fixtures, with the page-fetching and AI-client seams replaced by
fakes. Every AI-filter test monkeypatches `AI_API_KEY` empty regardless of
what's in your local `.env`, so a configured key never causes a real API
call during the test suite.
