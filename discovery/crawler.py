"""Discovers individual job-posting URLs on a careers page or job board.

Everything here is rule-based: a link is classified as a job posting (or
not) using URL-pattern and keyword heuristics, never AI. Every classified
link carries a confidence level and a machine-readable reason so later
phases (and a human skimming logs) can see why it was picked.

Two confidence levels:
  high   - the URL matches a known applicant-tracking-system (ATS) detail
           page pattern (Greenhouse, Lever, Personio, ...).
  medium - the URL path contains a job-related keyword segment followed by
           what looks like a posting slug/id, e.g. /careers/backend-engineer.

A bare listing/index page like "/jobs" or "/careers" (nothing after the
keyword) is deliberately excluded - it's a page to paginate through, not a
posting to scrape.
"""
import re
import time
from dataclasses import dataclass, replace
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import config

JOB_KEYWORDS = (
    "job",
    "jobs",
    "career",
    "careers",
    "karriere",
    "stelle",
    "stellen",
    "position",
    "positions",
    "vacancy",
    "vacancies",
    "opening",
    "openings",
    "vacature",
)

EXCLUDE_KEYWORDS = (
    "about",
    "contact",
    "imprint",
    "impressum",
    "datenschutz",
    "privacy",
    "agb",
    "terms",
    "cookie",
    "login",
    "sitemap",
    "faq",
    "blog",
    "news",
    "presse",
)

# (name, compiled pattern) - matched against the full lowercased URL.
ATS_PATTERNS = (
    ("ats_pattern:greenhouse", re.compile(r"greenhouse\.io/[^/]+/jobs/\d+")),
    ("ats_pattern:lever", re.compile(r"jobs\.lever\.co/[^/]+/[0-9a-f-]{36}")),
    ("ats_pattern:personio", re.compile(r"\.jobs\.personio\.(de|com)/job/\d+")),
    ("ats_pattern:smartrecruiters", re.compile(r"smartrecruiters\.com/[^/]+/\d+")),
    ("ats_pattern:join_com", re.compile(r"join\.com/companies/[^/]+/jobs/\d+")),
    ("ats_pattern:workday", re.compile(r"myworkdayjobs\.com/.+/job/")),
    ("ats_pattern:recruitee", re.compile(r"\.recruitee\.com/o/")),
    ("ats_pattern:bamboohr", re.compile(r"\.bamboohr\.com/careers/\d+")),
)

NEXT_PAGE_TEXT = {
    "next",
    "weiter",
    "nächste",
    "naechste",
    "»",
    "→",
    "vor",
}


@dataclass(frozen=True)
class DiscoveredLink:
    url: str
    confidence: str  # 'high' | 'medium'
    reason: str
    source_page: str = ""


def _contains_keyword(segment, keywords):
    return any(keyword in segment for keyword in keywords)


def classify_link(href, link_text, base_url):
    """Returns a DiscoveredLink (without source_page set) if href looks like
    an individual job posting, otherwise None.
    """
    if not href:
        return None

    full_url = urljoin(base_url, href)
    parsed = urlparse(full_url)
    if parsed.scheme not in ("http", "https"):
        return None

    full_lower = full_url.lower()
    for reason, pattern in ATS_PATTERNS:
        if pattern.search(full_lower):
            return DiscoveredLink(url=full_url, confidence="high", reason=reason)

    segments = [seg for seg in parsed.path.lower().split("/") if seg]
    text_lower = (link_text or "").strip().lower()

    if any(_contains_keyword(seg, EXCLUDE_KEYWORDS) for seg in segments):
        return None
    if text_lower in EXCLUDE_KEYWORDS:
        return None

    keyword_index = next(
        (i for i, seg in enumerate(segments) if _contains_keyword(seg, JOB_KEYWORDS)),
        None,
    )
    if keyword_index is None:
        return None

    has_detail_segment = keyword_index < len(segments) - 1
    if not has_detail_segment:
        return None  # looks like a listing/index page, not a posting

    return DiscoveredLink(
        url=full_url,
        confidence="medium",
        reason=f"keyword_path:{segments[keyword_index]}",
    )


def find_next_page_url(html, base_url):
    """Returns (url, reason) for the "next page" link, or (None, None)."""
    soup = BeautifulSoup(html, "html.parser")

    link_tag = soup.find("link", rel=lambda v: v and "next" in v)
    if link_tag and link_tag.get("href"):
        return urljoin(base_url, link_tag["href"]), "link_rel_next"

    a_tag = soup.find("a", rel=lambda v: v and "next" in v)
    if a_tag and a_tag.get("href"):
        return urljoin(base_url, a_tag["href"]), "a_rel_next"

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        aria_label = (a.get("aria-label") or "").strip().lower()
        if text in NEXT_PAGE_TEXT or aria_label in NEXT_PAGE_TEXT:
            return urljoin(base_url, a["href"]), "link_text_next"

    return None, None


def fetch_page(url):
    """Fetches a URL. Returns the response body, or None on failure.

    Failures are reported with a clear, specific message and never raised -
    a single misbehaving or blocking source must not crash the crawl.
    """
    headers = {"User-Agent": config.USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        print(f"[discovery] could not reach {url}: {exc}")
        return None

    if response.status_code == 403:
        print(f"[discovery] {url} returned HTTP 403 (blocked) - the site is refusing this crawler")
        return None
    if response.status_code == 429:
        print(f"[discovery] {url} returned HTTP 429 (rate limited) - back off and try again later")
        return None
    if response.status_code >= 400:
        print(f"[discovery] {url} returned HTTP {response.status_code} - skipping")
        return None

    return response.text


def _crawl(start_url, max_pages, delay_seconds, fetch):
    """Low-level pagination engine. Returns the full DiscoveredLink objects
    (confidence + reason included) so callers/tests can inspect why a link
    was picked, not just its URL.
    """
    seen_pages = set()
    found = {}  # url -> DiscoveredLink, first occurrence wins
    current_url = start_url
    pages_visited = 0

    while current_url and current_url not in seen_pages and pages_visited < max_pages:
        seen_pages.add(current_url)
        html = fetch(current_url)
        pages_visited += 1

        if html is None:
            print(f"[discovery] stopping pagination at {current_url} (fetch failed)")
            break

        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            link = classify_link(a["href"], a.get_text(strip=True), current_url)
            if link and link.url not in found:
                found[link.url] = replace(link, source_page=current_url)
                print(f"[discovery] +{link.confidence:6} {link.url}  ({link.reason})")

        next_url, next_reason = find_next_page_url(html, current_url)
        if next_url and next_url not in seen_pages:
            print(f"[discovery] pagination -> {next_url}  (via {next_reason})")
            current_url = next_url
            if delay_seconds and pages_visited < max_pages:
                time.sleep(delay_seconds)
        else:
            current_url = None

    if pages_visited >= max_pages and current_url:
        print(f"[discovery] stopped after reaching MAX_PAGES_PER_SITE={max_pages}")

    return list(found.values())


def discover_job_urls(source, max_pages=None, delay_seconds=None, fetch_fn=None):
    """Crawls a source's listing page(s), following pagination, and
    returns a deduplicated list of candidate job-posting URLs.

    `source` is any mapping with a "url" key - a sqlite3.Row from
    discovery.db.get_sources()/get_source() works directly, as does a
    plain dict (handy in tests and for one-off CLI use). The source's
    scrape_config isn't consumed yet; that lands with actual scraping.
    """
    max_pages = config.MAX_PAGES_PER_SITE if max_pages is None else max_pages
    delay_seconds = config.CRAWL_DELAY_SECONDS if delay_seconds is None else delay_seconds
    fetch = fetch_fn or fetch_page

    links = _crawl(source["url"], max_pages, delay_seconds, fetch)
    return [link.url for link in links]
