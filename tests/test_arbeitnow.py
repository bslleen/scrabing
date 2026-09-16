import json
import os
import tempfile
from pathlib import Path

import pytest

import config
from discovery import db
from discovery import sources as sources_module
from discovery.arbeitnow import API_URL_PREFIX, fetch_job_items, is_arbeitnow_url
from discovery.normalizer import normalize_raw_job
from discovery.scraper import scrape_source

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
PAGE_1_URL = API_URL_PREFIX
PAGE_2_URL = f"{API_URL_PREFIX}?page=2"

FIXTURE_PAGES = {
    PAGE_1_URL: FIXTURES_DIR / "arbeitnow_page_1.json",
    PAGE_2_URL: FIXTURES_DIR / "arbeitnow_page_2.json",
}


def fake_fetch_json(url):
    path = FIXTURE_PAGES.get(url)
    if path is None:
        raise AssertionError(f"unexpected URL fetched: {url}")
    return json.loads(path.read_text())


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(db_path=path)
    yield path
    os.remove(path)


@pytest.fixture(autouse=True)
def no_real_sleeps(monkeypatch):
    monkeypatch.setattr(config, "CRAWL_DELAY_SECONDS", 0)


# --- is_arbeitnow_url / add_source auto-detection --------------------------

def test_is_arbeitnow_url_matches_the_api_prefix_only():
    assert is_arbeitnow_url("https://www.arbeitnow.com/api/job-board-api")
    assert is_arbeitnow_url("https://www.arbeitnow.com/api/job-board-api?page=2")
    assert not is_arbeitnow_url("https://www.arbeitnow.com/jobs/some-job")
    assert not is_arbeitnow_url("https://example-company.com/careers")
    assert not is_arbeitnow_url(None)


def test_add_source_auto_detects_arbeitnow_type(db_path):
    source_id = sources_module.add_source(url=PAGE_1_URL, db_path=db_path)
    row = db.get_source(source_id, db_path=db_path)
    assert row["type"] == "arbeitnow_api"
    assert row["scrape_config"] is None  # no CSS selectors for a JSON API


def test_add_source_still_defaults_html_sources_to_custom_html(db_path):
    source_id = sources_module.add_source(url="https://example-company.com/careers", db_path=db_path)
    row = db.get_source(source_id, db_path=db_path)
    assert row["type"] == "custom_html"
    assert row["scrape_config"] is not None


# --- fetch_job_items (pagination via links.next) ---------------------------

def test_fetch_job_items_follows_pagination_across_both_pages():
    items = fetch_job_items(PAGE_1_URL, max_pages=10, fetch_json_fn=fake_fetch_json)

    assert len(items) == 3
    assert {item["slug"] for item in items} == {
        "backend-engineer-berlin-12345",
        "remote-data-analyst-67890",
        "marketing-manager-hamburg-54321",
    }


def test_fetch_job_items_respects_max_pages():
    items = fetch_job_items(PAGE_1_URL, max_pages=1, fetch_json_fn=fake_fetch_json)

    assert len(items) == 2  # only page 1's items
    assert "marketing-manager-hamburg-54321" not in {item["slug"] for item in items}


# --- scrape_source (Arbeitnow branch) ---------------------------------------

def test_scrape_source_stores_slug_url_and_description_for_each_item(db_path):
    source_id = sources_module.add_source(url=PAGE_1_URL, db_path=db_path)

    inserted_ids = scrape_source(source_id, db_path=db_path, fetch_json_fn=fake_fetch_json)

    assert len(inserted_ids) == 3
    raw_jobs = {row["external_id"]: row for row in db.get_raw_jobs(db_path=db_path)}

    backend = raw_jobs["backend-engineer-berlin-12345"]
    assert backend["url"] == "https://www.arbeitnow.com/jobs/companies/acme-gmbh/backend-engineer-berlin-12345"
    assert backend["raw_html"] == (
        "<p>We are looking for a Backend Engineer to join our platform team.</p>"
        "<h2>Aufgaben</h2><ul><li>Build APIs</li></ul>"
    )
    assert "Backend Engineer" in backend["raw_text"]
    assert json.loads(backend["raw_json"])["company_name"] == "Acme GmbH"
    assert backend["status"] == "new"


def test_scrape_source_does_not_duplicate_on_rerun(db_path):
    source_id = sources_module.add_source(url=PAGE_1_URL, db_path=db_path)

    first = scrape_source(source_id, db_path=db_path, fetch_json_fn=fake_fetch_json)
    second = scrape_source(source_id, db_path=db_path, fetch_json_fn=fake_fetch_json)

    assert len(first) == 3
    assert second == []
    assert len(db.get_raw_jobs(db_path=db_path)) == 3


# --- normalize_raw_job (direct field mapping, no heuristics) ---------------

def _raw_job_id_for_slug(db_path, slug):
    return next(row["id"] for row in db.get_raw_jobs(db_path=db_path) if row["external_id"] == slug)


def test_normalize_maps_fields_directly_for_a_non_remote_job(db_path):
    source_id = sources_module.add_source(url=PAGE_1_URL, db_path=db_path)
    scrape_source(source_id, db_path=db_path, fetch_json_fn=fake_fetch_json)
    raw_job_id = _raw_job_id_for_slug(db_path, "backend-engineer-berlin-12345")

    job_id = normalize_raw_job(raw_job_id, db_path=db_path)

    job = db.get_job(job_id, db_path=db_path)
    assert job["title"] == "Backend Engineer"
    assert job["company"] == "Acme GmbH"
    assert job["location"] == "Berlin"
    assert job["employment_type"] == "Full-time"
    assert job["posted_at"] == "2024-06-01T00:00:00+00:00"
    assert job["salary_min"] is None and job["salary_max"] is None

    # The description is used as-is (cleaned text) - no heading-based
    # splitting, so the German "Aufgaben" heading stays inside it rather
    # than being pulled into requirements.
    assert "Aufgaben" in job["description"]
    assert job["requirements"] is None

    assert db.get_raw_job(raw_job_id, db_path=db_path)["status"] == "normalized"


def test_normalize_folds_remote_flag_into_location_when_location_is_blank(db_path):
    source_id = sources_module.add_source(url=PAGE_1_URL, db_path=db_path)
    scrape_source(source_id, db_path=db_path, fetch_json_fn=fake_fetch_json)
    raw_job_id = _raw_job_id_for_slug(db_path, "remote-data-analyst-67890")

    job_id = normalize_raw_job(raw_job_id, db_path=db_path)

    job = db.get_job(job_id, db_path=db_path)
    assert job["title"] == "Data Analyst"
    assert job["location"] == "Remote"  # blank location + remote=true
    assert job["employment_type"] is None  # empty job_types list


def test_normalize_joins_multiple_job_types_for_a_page_2_job(db_path):
    source_id = sources_module.add_source(url=PAGE_1_URL, db_path=db_path)
    # A single scrape_source call already walks both pages via links.next.
    scrape_source(source_id, db_path=db_path, fetch_json_fn=fake_fetch_json)

    raw_job_id = _raw_job_id_for_slug(db_path, "marketing-manager-hamburg-54321")
    job_id = normalize_raw_job(raw_job_id, db_path=db_path)

    job = db.get_job(job_id, db_path=db_path)
    assert job["title"] == "Marketing Manager"
    assert job["location"] == "Hamburg"  # remote=false, left as-is
    assert job["employment_type"] == "Full-time, Contract"


def test_normalize_marks_error_when_arbeitnow_item_has_no_title(db_path):
    source_id = sources_module.add_source(url=PAGE_1_URL, db_path=db_path)
    item = {"slug": "no-title-job", "url": "https://www.arbeitnow.com/jobs/x/no-title-job",
             "company_name": "Acme", "description": "<p>Body</p>", "job_types": [], "location": ""}
    raw_job_id = db.insert_raw_job(
        url=item["url"], source_id=source_id, external_id=item["slug"],
        raw_html=item["description"], raw_text="Body", raw_json=json.dumps(item),
        status="new", db_path=db_path,
    )

    job_id = normalize_raw_job(raw_job_id, db_path=db_path)

    assert job_id is None
    assert db.get_raw_job(raw_job_id, db_path=db_path)["status"] == "error"
    assert db.get_jobs(db_path=db_path) == []
