from discovery.crawler import classify_link, discover_job_urls, find_next_page_url

BASE = "https://example-company.com/careers"


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


# --- discover_job_urls (multi-page crawl) --------------------------------

PAGE_1 = """
<html><body>
  <a href="/careers/backend-engineer">Backend Engineer</a>
  <a href="/careers/frontend-engineer">Frontend Engineer</a>
  <a href="/about-us">About us</a>
  <a rel="next" href="/careers?page=2">Next</a>
</body></html>
"""

PAGE_2 = """
<html><body>
  <a href="/careers/backend-engineer">Backend Engineer (dup)</a>
  <a href="/careers/data-analyst">Data Analyst</a>
</body></html>
"""

PAGES = {
    "https://example-company.com/careers": PAGE_1,
    "https://example-company.com/careers?page=2": PAGE_2,
}


def fake_fetch(url):
    return PAGES.get(url)


def test_discover_job_urls_follows_pagination_and_dedupes():
    links = discover_job_urls(
        "https://example-company.com/careers",
        max_pages=10,
        delay_seconds=0,
        fetch_fn=fake_fetch,
    )
    urls = {link.url for link in links}

    assert urls == {
        "https://example-company.com/careers/backend-engineer",
        "https://example-company.com/careers/frontend-engineer",
        "https://example-company.com/careers/data-analyst",
    }
    assert len(links) == 3  # the page-2 duplicate must not be counted twice


def test_discover_job_urls_respects_max_pages():
    links = discover_job_urls(
        "https://example-company.com/careers",
        max_pages=1,
        delay_seconds=0,
        fetch_fn=fake_fetch,
    )
    urls = {link.url for link in links}

    # Only page 1 was visited, so the page-2-only listing must be absent.
    assert "https://example-company.com/careers/data-analyst" not in urls
    assert len(urls) == 2


def test_discover_job_urls_stops_gracefully_on_fetch_failure():
    def failing_fetch(url):
        return None

    links = discover_job_urls(
        "https://example-company.com/careers",
        max_pages=10,
        delay_seconds=0,
        fetch_fn=failing_fetch,
    )
    assert links == []
