"""Support for Arbeitnow's public job-board API
(https://www.arbeitnow.com/api/job-board-api) as an alternative to HTML
crawling for sources whose URL points at it.

Arbeitnow already returns clean, structured JSON - title, company,
location, a remote flag, job types, and a creation timestamp - so
there's nothing to discover or guess: discovery.crawler's link-
classification heuristics are skipped entirely for this source type
(see discovery.scraper.scrape_source), and discovery.normalizer maps
these fields directly instead of running its usual JSON-LD/heuristic
pipeline.
"""
import time

import requests
from bs4 import BeautifulSoup

import config

API_URL_PREFIX = "https://www.arbeitnow.com/api/job-board-api"


def is_arbeitnow_url(url):
    return bool(url) and url.startswith(API_URL_PREFIX)


def item_to_text(description_html):
    """Cleans an Arbeitnow item's description HTML into plain text, the
    same way discovery.scraper cleans a scraped page's description -
    used as the job's raw_text and, later, jobs.description.
    """
    if not description_html:
        return ""
    text = BeautifulSoup(description_html, "html.parser").get_text(separator="\n")
    lines = (line.strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _default_fetch_json(url):
    headers = {"User-Agent": config.USER_AGENT}
    response = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def fetch_job_items(start_url, max_pages=None, fetch_json_fn=None):
    """Follows links.next across pages of the Arbeitnow API (bounded by
    MAX_PAGES_PER_SITE), returning the combined list of job item dicts
    found. A page fetch failure stops pagination there rather than
    raising - same politeness/robustness contract as discovery.crawler.
    """
    max_pages = config.MAX_PAGES_PER_SITE if max_pages is None else max_pages
    fetch_json = fetch_json_fn or _default_fetch_json

    items = []
    seen_pages = set()
    current_url = start_url
    pages_visited = 0

    while current_url and current_url not in seen_pages and pages_visited < max_pages:
        seen_pages.add(current_url)
        try:
            payload = fetch_json(current_url)
        except requests.RequestException as exc:
            print(f"[arbeitnow] failed to fetch {current_url}: {exc}")
            break

        page_items = payload.get("data") or []
        items.extend(page_items)
        pages_visited += 1
        print(f"[arbeitnow] page {pages_visited}: {len(page_items)} job(s)")

        next_url = (payload.get("links") or {}).get("next")
        if next_url and next_url not in seen_pages:
            current_url = next_url
            if config.CRAWL_DELAY_SECONDS and pages_visited < max_pages:
                time.sleep(config.CRAWL_DELAY_SECONDS)
        else:
            current_url = None

    if pages_visited >= max_pages and current_url:
        print(f"[arbeitnow] stopped after reaching MAX_PAGES_PER_SITE={max_pages}")

    return items
