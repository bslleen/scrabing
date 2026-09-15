import os
import tempfile
from pathlib import Path

import pytest

from discovery import db, sources
from discovery.normalizer import normalize_raw_job
from discovery.scraper import scrape_source

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


@pytest.fixture
def scraped_db_path(db_path):
    """A temp db already populated with raw_jobs from the Phase 4 fixtures."""
    source_id = sources.add_source(url=BASE, db_path=db_path)
    scrape_source(source_id, db_path=db_path, fetch_fn=fake_fetch, delay_seconds=0)
    return db_path


def _raw_job_id_for(db_path, url):
    for row in db.get_raw_jobs(db_path=db_path):
        if row["url"] == url:
            return row["id"]
    raise AssertionError(f"no raw_jobs row for {url}")


# --- normalize_raw_job, one fixture per extraction path -------------------

def test_normalize_backend_engineer_uses_json_ld_throughout(scraped_db_path):
    raw_job_id = _raw_job_id_for(scraped_db_path, f"{BASE}/backend-engineer")

    job_id = normalize_raw_job(raw_job_id, db_path=scraped_db_path)
    assert job_id is not None

    job = db.get_job(job_id, db_path=scraped_db_path)
    assert job["title"] == "Backend Engineer"
    assert job["company"] == "Example Company"
    assert job["location"] == "Berlin, DE"
    assert job["employment_type"] == "full_time"
    assert job["salary_min"] is None  # no salary anywhere - must not be guessed
    assert job["salary_max"] is None
    assert "Backend Engineer" in job["description"]
    assert job["requirements"] is None  # no requirements heading in this fixture

    raw_job = db.get_raw_job(raw_job_id, db_path=scraped_db_path)
    assert raw_job["status"] == "normalized"


def test_normalize_data_analyst_extracts_salary_via_regex(scraped_db_path):
    raw_job_id = _raw_job_id_for(scraped_db_path, f"{BASE}/data-analyst")

    job_id = normalize_raw_job(raw_job_id, db_path=scraped_db_path)

    job = db.get_job(job_id, db_path=scraped_db_path)
    assert job["title"] == "Data Analyst"
    assert job["company"] == "Example Company"
    assert job["location"] is None  # not stated anywhere - must not be guessed
    assert job["salary_min"] == 50000
    assert job["salary_max"] == 65000


def test_normalize_frontend_engineer_uses_heuristics(scraped_db_path):
    raw_job_id = _raw_job_id_for(scraped_db_path, f"{BASE}/frontend-engineer")

    job_id = normalize_raw_job(raw_job_id, db_path=scraped_db_path)

    job = db.get_job(job_id, db_path=scraped_db_path)
    assert job["title"] == "Frontend Engineer"  # from <h1>, no JSON-LD on this page
    assert job["company"] == "Example Company"  # from a "Company:" labeled line
    assert job["location"] == "Hamburg, Germany"  # from a "Location:" labeled line
    assert job["employment_type"] == "part_time"  # keyword match, no JSON-LD
    assert "React" in job["requirements"]
    assert "TypeScript" in job["requirements"]
    assert "Location" not in job["requirements"]  # split lands before the heading


def test_normalize_marketing_manager_never_guesses_missing_fields(scraped_db_path):
    raw_job_id = _raw_job_id_for(scraped_db_path, f"{BASE}/marketing-manager")

    job_id = normalize_raw_job(raw_job_id, db_path=scraped_db_path)

    job = db.get_job(job_id, db_path=scraped_db_path)
    assert job["title"] == "Marketing Manager"  # from <h1>
    assert job["company"] is None  # "Example Company" appears but isn't labeled - no guess
    assert job["location"] is None
    assert job["salary_min"] is None
    assert job["employment_type"] is None


# --- failure handling: never silently drop a row --------------------------

def test_normalize_raw_job_marks_error_when_no_title_extractable(db_path):
    raw_job_id = db.insert_raw_job(
        url="https://example.com/empty",
        raw_html="<html><body></body></html>",
        raw_text="",
        db_path=db_path,
    )

    job_id = normalize_raw_job(raw_job_id, db_path=db_path)

    assert job_id is None
    assert db.get_jobs(db_path=db_path) == []

    raw_job = db.get_raw_job(raw_job_id, db_path=db_path)
    assert raw_job["status"] == "error"


def test_normalize_raw_job_returns_none_for_unknown_id(db_path):
    assert normalize_raw_job(999, db_path=db_path) is None
