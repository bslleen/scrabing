"""Scrapes individual job-posting pages discovered by discovery.crawler and
stores them in raw_jobs.

Content extraction is JSON-LD-first: many job boards embed a schema.org
JobPosting as a <script type="application/ld+json"> block, which gives a
clean description with no nav/footer noise to strip. Only when no such
structured data is present (or it has no usable description) do we fall
back to heuristics: a handful of common description-container class
names, and finally the page's whole visible text as a last resort. Every
extraction records which path was used, for the same reason discovery.
crawler tags each link with a confidence/reason - so it's clear later why
a given raw_text looks the way it does.

Arbeitnow API sources (discovery.arbeitnow) bypass all of that: there's
no page to scrape, just JSON items already containing everything needed.

Standalone module: no imports from any other project.
"""
import json
import sqlite3
import time
from datetime import datetime, timezone

from bs4 import BeautifulSoup

import config
from discovery import arbeitnow, db
from discovery.crawler import discover_job_urls, fetch_page
from discovery.json_ld import find_job_posting

# Ordered most-specific-first: the first matching selector wins.
HEURISTIC_SELECTORS = (
    '[class*="job-description"]',
    '[class*="jobdescription"]',
    '[class*="job_description"]',
    '[class*="description"]',
    "article",
    "main",
)


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _clean_text(text):
    lines = (line.strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _extract_external_id(job_posting):
    identifier = job_posting.get("identifier")
    if isinstance(identifier, dict):
        value = identifier.get("value")
        return str(value) if value is not None else None
    if isinstance(identifier, str):
        return identifier
    return None


def _extract_from_json_ld(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue

        job_posting = find_job_posting(data)
        if not job_posting:
            continue

        description_html = job_posting.get("description")
        if not description_html:
            continue

        text = _clean_text(BeautifulSoup(description_html, "html.parser").get_text(separator="\n"))
        if text:
            return text, _extract_external_id(job_posting), "json_ld"

    return None, None, None


def _extract_heuristically(soup):
    for selector in HEURISTIC_SELECTORS:
        element = soup.select_one(selector)
        if element:
            text = _clean_text(element.get_text(separator="\n"))
            if text:
                return text, f"heuristic_selector:{selector}"

    return _clean_text(soup.get_text(separator="\n")), "heuristic_fallback:body_text"


def extract_job_content(html):
    """Returns (raw_text, external_id, reason). raw_text may be an empty
    string if the page had no extractable text at all; it is never None.
    """
    soup = BeautifulSoup(html, "html.parser")

    text, external_id, reason = _extract_from_json_ld(soup)
    if text:
        return text, external_id, reason

    text, reason = _extract_heuristically(soup)
    return text, None, reason


def scrape_source(source_id, db_path=None, fetch_fn=None, delay_seconds=None, fetch_json_fn=None):
    """Discovers and scrapes every not-yet-seen job for a source, storing
    raw_html/raw_text (and, for API sources, raw_json). Updates the
    source's last_scraped_at when done. Returns the list of newly
    inserted raw_jobs ids.

    Arbeitnow API sources (discovery.arbeitnow.is_arbeitnow_url) skip the
    HTML crawler entirely - there's no listing page to paginate through
    or links to classify, just JSON pages to follow via `links.next`.
    """
    source = db.get_source(source_id, db_path=db_path)
    if source is None:
        print(f"[scraper] no source with id={source_id}")
        return []

    if arbeitnow.is_arbeitnow_url(source["url"]):
        return _scrape_arbeitnow_source(source, db_path=db_path, fetch_json_fn=fetch_json_fn)

    return _scrape_html_source(source, db_path=db_path, fetch_fn=fetch_fn, delay_seconds=delay_seconds)


def _scrape_html_source(source, db_path=None, fetch_fn=None, delay_seconds=None):
    source_id = source["id"]
    fetch = fetch_fn or fetch_page
    delay_seconds = config.CRAWL_DELAY_SECONDS if delay_seconds is None else delay_seconds

    job_urls = discover_job_urls(source, fetch_fn=fetch, delay_seconds=delay_seconds)
    print(f"[scraper] {len(job_urls)} candidate job URL(s) discovered for source {source_id}")

    already_scraped = {row["url"] for row in db.get_raw_jobs(db_path=db_path)}

    inserted_ids = []
    for index, url in enumerate(job_urls):
        if url in already_scraped:
            print(f"[scraper] {url} already scraped, skipping")
            continue

        html = fetch(url)
        if html is None:
            print(f"[scraper] could not fetch {url}, skipping")
            continue

        raw_text, external_id, reason = extract_job_content(html)
        print(f"[scraper] {url}: extracted {len(raw_text)} char(s) via {reason}")

        try:
            raw_job_id = db.insert_raw_job(
                url=url,
                source_id=source_id,
                external_id=external_id,
                raw_html=html,
                raw_text=raw_text,
                scraped_at=_utc_now_iso(),
                status="new",
                db_path=db_path,
            )
        except sqlite3.IntegrityError:
            # Belt-and-suspenders: the url UNIQUE constraint is the real
            # dedupe guarantee, the already_scraped check above is just
            # there to avoid the wasted fetch in the common case.
            print(f"[scraper] {url} already scraped (unique constraint), skipping")
            continue

        inserted_ids.append(raw_job_id)
        already_scraped.add(url)

        if delay_seconds and index < len(job_urls) - 1:
            time.sleep(delay_seconds)

    db.update_source_last_scraped(source_id, _utc_now_iso(), db_path=db_path)
    print(f"[scraper] inserted {len(inserted_ids)} new raw_jobs row(s) for source {source_id}")
    return inserted_ids


def _scrape_arbeitnow_source(source, db_path=None, fetch_json_fn=None):
    source_id = source["id"]
    items = arbeitnow.fetch_job_items(source["url"], fetch_json_fn=fetch_json_fn)
    print(f"[scraper] {len(items)} job item(s) fetched from the Arbeitnow API for source {source_id}")

    already_scraped = {row["url"] for row in db.get_raw_jobs(db_path=db_path)}

    inserted_ids = []
    for item in items:
        url = item.get("url")
        if not url or url in already_scraped:
            continue

        try:
            raw_job_id = db.insert_raw_job(
                url=url,
                source_id=source_id,
                external_id=item.get("slug"),
                raw_html=item.get("description"),
                raw_text=arbeitnow.item_to_text(item.get("description")),
                raw_json=json.dumps(item),
                scraped_at=_utc_now_iso(),
                status="new",
                db_path=db_path,
            )
        except sqlite3.IntegrityError:
            print(f"[scraper] {url} already scraped (unique constraint), skipping")
            continue

        inserted_ids.append(raw_job_id)
        already_scraped.add(url)

    db.update_source_last_scraped(source_id, _utc_now_iso(), db_path=db_path)
    print(f"[scraper] inserted {len(inserted_ids)} new raw_jobs row(s) for source {source_id}")
    return inserted_ids
