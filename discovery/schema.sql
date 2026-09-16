-- Full pipeline schema: sites to scrape, raw scraped pages, normalized
-- jobs, saved filter criteria, and the resulting matches. Every CREATE is
-- IF NOT EXISTS so init_db() is safe to call on every run.

CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL,
  name TEXT,
  type TEXT DEFAULT 'custom_html',   -- 'custom_html', 'greenhouse', 'lever', etc. later
  scrape_config TEXT,                -- JSON string: selectors, pagination pattern
  last_scraped_at TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw_jobs (
  id INTEGER PRIMARY KEY,
  source_id INTEGER REFERENCES sources(id),
  external_id TEXT,
  url TEXT NOT NULL UNIQUE,
  raw_html TEXT,
  raw_text TEXT,
  raw_json TEXT,                     -- original item verbatim, for JSON-API sources (e.g. Arbeitnow); NULL for HTML-scraped jobs
  scraped_at TEXT,
  status TEXT DEFAULT 'new'          -- 'new', 'normalized', 'error'
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  raw_job_id INTEGER REFERENCES raw_jobs(id),
  title TEXT,
  company TEXT,
  location TEXT,
  description TEXT,
  requirements TEXT,
  salary_min INTEGER,
  salary_max INTEGER,
  employment_type TEXT,
  posted_at TEXT,
  normalized_at TEXT
);

CREATE TABLE IF NOT EXISTS criteria (
  id INTEGER PRIMARY KEY,
  label TEXT,                        -- e.g. "default" - supports multiple saved profiles
  keywords TEXT,                     -- JSON array
  exclude_keywords TEXT,             -- JSON array
  locations TEXT,                    -- JSON array
  min_salary INTEGER,
  employment_types TEXT,             -- JSON array
  ai_prompt TEXT,                    -- free-text prompt for Phase 7's AI-assisted matching; unused until then
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_matches (
  id INTEGER PRIMARY KEY,
  job_id INTEGER REFERENCES jobs(id),
  criteria_id INTEGER REFERENCES criteria(id),
  static_score REAL,
  match_reasons TEXT,                -- JSON: which rules hit, for transparency in the UI
  status TEXT DEFAULT 'pending',     -- 'pending', 'relevant', 'rejected'
  ai_score REAL,                     -- 0-100, from Phase 7's optional AI re-ranking; NULL until run
  ai_reasoning TEXT,                 -- short LLM explanation for ai_score, for UI transparency
  matched_at TEXT
);
