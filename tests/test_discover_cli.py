"""End-to-end test for the discover.py CLI entry point: runs the full
discover -> scrape -> normalize -> static match -> AI filter pipeline
against the same local fixtures used by the earlier per-phase tests, with
both network seams (page fetching, the AI client) replaced by fakes -
never a real HTTP or API call - then asserts on job_matches through the
db helpers directly, not the UI.
"""
from pathlib import Path

import pytest

import config
import discover
from discovery import criteria as criteria_module
from discovery import db

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


def fake_ai_client(job, ai_prompt):
    """Canned, deterministic AI stub - as in Phase 7's tests, never a
    real network call.
    """
    if job["title"] == "Backend Engineer":
        return 90, "Great fit for the backend role."
    return 20, "Not a strong match for this role."


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Points every discovery.* call (all default to config.DB_PATH) at
    a fresh temp file and initializes its schema - discover.py's own
    db.init_db() call would do this too, but a test that adds a criteria
    profile *before* calling discover.main() needs the tables to already
    exist.
    """
    db_path = str(tmp_path / "pipeline_test.db")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db(db_path=db_path)
    return db_path


@pytest.fixture(autouse=True)
def patched_network_seams(monkeypatch):
    # scraper.py and crawler.py each import fetch_page into their own
    # module namespace (`from discovery.crawler import fetch_page`), so
    # both bindings need patching to fully remove real network access.
    monkeypatch.setattr("discovery.crawler.fetch_page", fake_fetch)
    monkeypatch.setattr("discovery.scraper.fetch_page", fake_fetch)
    monkeypatch.setattr("discovery.ai_filter._default_client", fake_ai_client)
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")  # so ai_filter doesn't skip
    monkeypatch.setattr(config, "CRAWL_DELAY_SECONDS", 0)  # no real sleeps in tests


def test_discover_cli_runs_full_pipeline_end_to_end(temp_db):
    criteria_module.add_criteria(
        label="default",
        keywords=["python", "backend", "engineer"],
        exclude_keywords=[],
        locations=["Berlin"],
        min_salary=None,
        ai_prompt="Looking for a backend engineering role.",
        db_path=temp_db,
    )

    discover.main([BASE, "--criteria", "default"])

    raw_jobs = db.get_raw_jobs(db_path=temp_db)
    assert len(raw_jobs) == 4  # backend/frontend/data-analyst/marketing, across 3 paginated pages

    jobs = db.get_jobs(db_path=temp_db)
    assert len(jobs) == 4  # each fixture has an extractable title, so none error out

    matches = db.get_job_matches(db_path=temp_db)
    assert len(matches) == 4

    by_title = {j["title"]: j for j in jobs}
    matches_by_job_id = {m["job_id"]: m for m in matches}

    backend_match = matches_by_job_id[by_title["Backend Engineer"]["id"]]
    assert backend_match["status"] == "relevant"
    assert backend_match["ai_score"] == 90
    assert "Great fit" in backend_match["ai_reasoning"]

    # The other three never clear the static bar, so they're never sent
    # to the (fake) AI client at all - ai_score stays unset.
    for title in ("Frontend Engineer", "Data Analyst", "Marketing Manager"):
        other_match = matches_by_job_id[by_title[title]["id"]]
        assert other_match["status"] == "rejected"
        assert other_match["ai_score"] is None


def test_discover_cli_no_ai_flag_skips_ai_filtering_entirely(temp_db, monkeypatch):
    calls = []

    def fail_if_called(job, ai_prompt):
        calls.append(job["id"])
        return 100, "should never run"

    monkeypatch.setattr("discovery.ai_filter._default_client", fail_if_called)

    criteria_module.add_criteria(
        label="default", keywords=["python", "backend", "engineer"], db_path=temp_db,
    )

    discover.main([BASE, "--criteria", "default", "--no-ai"])

    assert calls == []
    matches = db.get_job_matches(db_path=temp_db)
    assert len(matches) == 4
    assert all(m["ai_score"] is None for m in matches)


def test_discover_cli_reuses_existing_source_on_rerun(temp_db):
    discover.main([BASE])
    discover.main([BASE])

    sources = [row for row in db.get_sources(db_path=temp_db) if row["url"] == BASE]
    assert len(sources) == 1

    # The second run found nothing new to scrape (already-seen URLs).
    assert len(db.get_raw_jobs(db_path=temp_db)) == 4


def test_discover_cli_skips_matching_with_no_criteria_flag(temp_db):
    discover.main([BASE])

    assert len(db.get_jobs(db_path=temp_db)) == 4
    assert db.get_job_matches(db_path=temp_db) == []


def test_discover_cli_reports_unknown_criteria_label_and_skips_matching(temp_db):
    discover.main([BASE, "--criteria", "does-not-exist"])

    assert len(db.get_jobs(db_path=temp_db)) == 4
    assert db.get_job_matches(db_path=temp_db) == []
