from pathlib import Path
from unittest.mock import patch

import requests

from discovery.crawler import classify_link, discover_job_urls, fetch_page, find_next_page_url

BASE = "https://example-company.com/careers"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# --- classify_link -----------------------------------------------------

def test_ats_pattern_is_high_confidence():
    link = classify_link(
        "https://boards.greenhouse.io/examplecompany/jobs/1234567",
        "Backend Engineer",
        BASE,
    )
    assert link is not None
    assert link.confidence == "high"
    assert link.reason == "ats_pattern:greenhouse"


def test_keyword_path_with_detail_segment_is_medium_confidence():
    link = classify_link("/careers/senior-backend-engineer", "Senior Backend Engineer", BASE)
    assert link is not None
    assert link.confidence == "medium"
    assert link.reason == "keyword_path:careers"


def test_bare_listing_page_is_rejected():
    assert classify_link("/careers", "All openings", BASE) is None
    assert classify_link("/jobs/", "Jobs", BASE) is None


def test_excluded_nav_links_are_rejected():
    assert classify_link("/about-us", "About us", BASE) is None
    assert classify_link("/impressum", "Impressum", BASE) is None
    assert classify_link("/careers/imprint", "Imprint", BASE) is None


def test_relative_href_is_resolved_against_base_url():
    # Standard RFC 3986 resolution: a relative href without a leading slash
    # resolves against BASE's parent path, not BASE itself.
    link = classify_link("stellen/werkstudent-marketing", "Werkstudent Marketing", BASE)
    assert link is not None
    assert link.url == "https://example-company.com/stellen/werkstudent-marketing"


def test_non_http_scheme_is_rejected():
    assert classify_link("mailto:jobs@example.com", "Email us", BASE) is None


# --- find_next_page_url --------------------------------------------------

def test_finds_next_via_link_rel():
    html = '<html><head><link rel="next" href="/careers?page=2"></head><body></body></html>'
    url, reason = find_next_page_url(html, BASE)
    assert url == "https://example-company.com/careers?page=2"
    assert reason == "link_rel_next"


def test_finds_next_via_anchor_text():
    html = '<html><body><a href="/careers?page=2">Weiter</a></body></html>'
    url, reason = find_next_page_url(html, BASE)
    assert url == "https://example-company.com/careers?page=2"
    assert reason == "link_text_next"


def test_no_next_page_returns_none():
    html = "<html><body><p>No more pages here.</p></body></html>"
    url, reason = find_next_page_url(html, BASE)
    assert url is None
    assert reason is None


# --- discover_job_urls (multi-page crawl, from static fixture files) -----

FIXTURE_PAGES = {
    BASE: FIXTURES_DIR / "listing_page_1.html",
    f"{BASE}?page=2": FIXTURES_DIR / "listing_page_2.html",
    f"{BASE}?page=3": FIXTURES_DIR / "listing_page_3.html",
}


def fake_fetch(url):
    path = FIXTURE_PAGES.get(url)
    return path.read_text() if path else None


def test_discover_job_urls_extracts_postings_across_paginated_fixtures():
    source = {"url": BASE}
    urls = discover_job_urls(source, max_pages=10, delay_seconds=0, fetch_fn=fake_fetch)

    assert isinstance(urls, list)
    assert all(isinstance(u, str) for u in urls)
    assert set(urls) == {
        f"{BASE}/backend-engineer",
        f"{BASE}/frontend-engineer",
        f"{BASE}/data-analyst",
        f"{BASE}/marketing-manager",
    }
    assert len(urls) == 4  # the page-2 duplicate must not be counted twice


def test_discover_job_urls_respects_max_pages():
    source = {"url": BASE}
    urls = discover_job_urls(source, max_pages=2, delay_seconds=0, fetch_fn=fake_fetch)

    # Only pages 1-2 were visited, so page 3's posting must be absent.
    assert f"{BASE}/marketing-manager" not in urls
    assert set(urls) == {f"{BASE}/backend-engineer", f"{BASE}/frontend-engineer", f"{BASE}/data-analyst"}


def test_discover_job_urls_stops_gracefully_on_fetch_failure():
    def failing_fetch(url):
        return None

    source = {"url": BASE}
    urls = discover_job_urls(source, max_pages=10, delay_seconds=0, fetch_fn=failing_fetch)
    assert urls == []


# --- fetch_page (HTTP failure reporting) ----------------------------------

class _FakeResponse:
    def __init__(self, status_code, text="<html></html>"):
        self.status_code = status_code
        self.text = text


def test_fetch_page_returns_body_on_success():
    with patch("discovery.crawler.requests.get", return_value=_FakeResponse(200, "<html>ok</html>")):
        assert fetch_page(BASE) == "<html>ok</html>"


def test_fetch_page_reports_403_clearly_and_returns_none(capsys):
    with patch("discovery.crawler.requests.get", return_value=_FakeResponse(403)):
        result = fetch_page(BASE)

    assert result is None
    assert "403" in capsys.readouterr().out


def test_fetch_page_reports_429_clearly_and_returns_none(capsys):
    with patch("discovery.crawler.requests.get", return_value=_FakeResponse(429)):
        result = fetch_page(BASE)

    assert result is None
    assert "429" in capsys.readouterr().out


def test_fetch_page_handles_connection_errors_without_raising(capsys):
    with patch("discovery.crawler.requests.get", side_effect=requests.ConnectionError("boom")):
        result = fetch_page(BASE)

    assert result is None
    assert "could not reach" in capsys.readouterr().out
