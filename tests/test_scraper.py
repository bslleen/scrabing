import os
import tempfile
from pathlib import Path

import pytest

from discovery import db, sources
from discovery.scraper import extract_job_content, scrape_source

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
BASE = "https://example-company.com/careers"

FIXTURE_PAGES = {
    BASE: FIXTURES_DIR / "listing_page_1.html",
    f"{BASE}?page=2": FIXTURES_DIR / "listing_page_2.html",
    f"{BASE}?page=3": FIXTURES_DIR / "listing_page_3.html",
    f"{BASE}/backend-engineer": FIXTURES_DIR / "job_backend_engineer.html",
    f"{BASE}/frontend-engineer": FIXTURES_DIR / "job_frontend_engineer.html",
    f"{BASE}/data-analyst": FIXTURES_DIR / "job_data_analyst.html",
    f"{BASE}/marketing-manager": FIXTURES_DIR / "job_marketing_manager.html",
}


def fake_fetch(url):
    path = FIXTURE_PAGES.get(url)
    return path.read_text() if path else None


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(db_path=path)
    yield path
    os.remove(path)


# --- scrape_source (full pipeline against fixtures) -----------------------

def test_scrape_source_inserts_one_raw_job_per_discovered_url(db_path):
    source_id = sources.add_source(url=BASE, db_path=db_path)

    inserted_ids = scrape_source(source_id, db_path=db_path, fetch_fn=fake_fetch, delay_seconds=0)

    assert len(inserted_ids) == 4
    raw_jobs = db.get_raw_jobs(db_path=db_path)
    assert len(raw_jobs) == 4
    for row in raw_jobs:
        assert row["raw_text"]  # non-empty
        assert row["raw_html"]
        assert row["source_id"] == source_id
        assert row["status"] == "new"
        assert row["scraped_at"]


def test_scrape_source_updates_last_scraped_at(db_path):
    source_id = sources.add_source(url=BASE, db_path=db_path)
    assert db.get_source(source_id, db_path=db_path)["last_scraped_at"] is None

    scrape_source(source_id, db_path=db_path, fetch_fn=fake_fetch, delay_seconds=0)

    assert db.get_source(source_id, db_path=db_path)["last_scraped_at"] is not None


def test_scrape_source_does_not_duplicate_on_rerun(db_path):
    source_id = sources.add_source(url=BASE, db_path=db_path)

    first_ids = scrape_source(source_id, db_path=db_path, fetch_fn=fake_fetch, delay_seconds=0)
    assert len(first_ids) == 4

    second_ids = scrape_source(source_id, db_path=db_path, fetch_fn=fake_fetch, delay_seconds=0)
    assert second_ids == []

    raw_jobs = db.get_raw_jobs(db_path=db_path)
    assert len(raw_jobs) == 4  # still just 4 - no duplicates


def test_scrape_source_returns_empty_for_unknown_source(db_path):
    assert scrape_source(999, db_path=db_path, fetch_fn=fake_fetch, delay_seconds=0) == []


# --- extract_job_content (JSON-LD first, heuristic fallback) --------------

def test_extract_job_content_prefers_json_ld():
    html = FIXTURE_PAGES[f"{BASE}/backend-engineer"].read_text()
    text, external_id, reason = extract_job_content(html)

    assert reason == "json_ld"
    assert external_id == "BE-042"
    assert "Backend Engineer" in text
    assert "Python" in text


def test_extract_job_content_falls_back_to_heuristic_selector():
    html = FIXTURE_PAGES[f"{BASE}/frontend-engineer"].read_text()
    text, external_id, reason = extract_job_content(html)

    assert reason.startswith("heuristic_selector:")
    assert external_id is None
    assert "Frontend Engineer" in text
    assert "React" in text
    assert "Contact" not in text  # nav noise sits outside the selected container


def test_extract_job_content_falls_back_to_body_text():
    html = FIXTURE_PAGES[f"{BASE}/marketing-manager"].read_text()
    text, external_id, reason = extract_job_content(html)

    assert reason == "heuristic_fallback:body_text"
    assert external_id is None
    assert "Marketing Manager" in text


def test_extract_job_content_ignores_malformed_json_ld():
    html = """
    <html><body>
      <script type="application/ld+json">{ not valid json </script>
      <main>Actual job content lives here.</main>
    </body></html>
    """
    text, external_id, reason = extract_job_content(html)

    assert reason == "heuristic_selector:main"
    assert external_id is None
    assert "Actual job content lives here." in text
