"""Source registration: a "source" is one site the tool has been pointed at.

Only 'custom_html' sources are supported for now - generic scraping via
CSS selectors stored in scrape_config. ATS-specific handling (Greenhouse,
Lever, ...) is a later, optional phase.
"""
import json

from discovery import db

# Heuristic default scrape_config for a site the user hasn't configured by
# hand: treat any <a> tag whose href or visible text contains "job" or
# "career" (case-insensitive) as a candidate job-posting link. This is
# intentionally crude - it will pick up index/listing pages as well as
# postings, and it won't find postings embedded via JavaScript or an ATS
# widget. It's meant as a starting point to override with a real CSS
# selector once the user (or a later phase) inspects the actual site.
DEFAULT_SCRAPE_CONFIG = {
    "job_link_selector": "a",
    "job_link_keywords": ["job", "career"],
}


def _normalize_scrape_config(scrape_config):
    """scrape_config may be a dict (serialized here) or an already-JSON
    string (stored as-is); None falls back to DEFAULT_SCRAPE_CONFIG.
    """
    if scrape_config is None:
        return json.dumps(DEFAULT_SCRAPE_CONFIG)
    if isinstance(scrape_config, str):
        return scrape_config
    return json.dumps(scrape_config)


def add_source(url, name=None, scrape_config=None, db_path=None):
    """Registers a new source and returns its id."""
    return db.insert_source(
        url=url,
        name=name,
        type="custom_html",
        scrape_config=_normalize_scrape_config(scrape_config),
        db_path=db_path,
    )


def list_sources(db_path=None):
    return db.get_sources(db_path=db_path)


def remove_source(source_id, db_path=None):
    return db.delete_source(source_id, db_path=db_path)
