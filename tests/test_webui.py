import json
import os
import tempfile

import pytest

import config
from discovery import criteria as criteria_module
from discovery import db, sources as sources_module
from discovery.matcher import run_static_matching
from webui.app import create_app


@pytest.fixture(autouse=True)
def no_real_ai_calls(monkeypatch):
    """Guards every test in this file against ever reaching the real
    OpenAI API, regardless of whatever AI_API_KEY happens to be set in
    the developer's local .env - the checklist requires zero external
    network requests when loading any page.
    """
    monkeypatch.setattr(config, "AI_API_KEY", "")


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(db_path=path)
    yield path
    os.remove(path)


@pytest.fixture
def app(db_path):
    return create_app(db_path=db_path)


@pytest.fixture
def client(app):
    return app.test_client()


# --- every route returns 200 on a fresh, empty db --------------------------

@pytest.mark.parametrize("path", [
    "/", "/sources", "/sources/new", "/jobs", "/jobs/new", "/matches",
    "/criteria", "/criteria/new",
])
def test_get_routes_return_200_on_empty_db(client, path):
    assert client.get(path).status_code == 200


def test_all_five_list_pages_render_their_empty_state(client):
    assert b"No sources yet" in client.get("/sources").data
    assert b"No jobs yet" in client.get("/jobs").data
    assert b"No matches yet" in client.get("/matches").data
    assert b"No criteria yet" in client.get("/criteria").data
    # Dashboard's "recent matches" table has its own empty state too.
    assert b"No relevant matches yet" in client.get("/").data


# --- sources: create / validation / delete ---------------------------------

def test_create_source_appears_in_list(client, db_path):
    resp = client.post("/sources/new", data={
        "name": "Example Co", "url": "https://example-company.com/careers", "scrape_config": "",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Example Co" in resp.data
    assert len(db.get_sources(db_path=db_path)) == 1


def test_create_source_invalid_url_shows_error_and_keeps_values(client):
    resp = client.post("/sources/new", data={"name": "Bad", "url": "not-a-url", "scrape_config": ""})
    assert resp.status_code == 200
    assert b"URL must start with http" in resp.data
    assert b'value="Bad"' in resp.data


def test_create_source_invalid_scrape_config_json_shows_error(client):
    resp = client.post("/sources/new", data={
        "name": "", "url": "https://example.com", "scrape_config": "{not json",
    })
    assert resp.status_code == 200
    assert b"valid JSON" in resp.data


def test_delete_source_removes_it(client, db_path):
    source_id = sources_module.add_source(url="https://example.com/careers", db_path=db_path)
    resp = client.post(f"/sources/{source_id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert db.get_source(source_id, db_path=db_path) is None


def test_delete_source_with_raw_jobs_is_blocked_cleanly_not_500(client, db_path):
    source_id = sources_module.add_source(url="https://example.com/careers", db_path=db_path)
    db.insert_raw_job(url="https://example.com/careers/1", source_id=source_id, db_path=db_path)

    resp = client.post(f"/sources/{source_id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Cannot delete" in resp.data
    assert db.get_source(source_id, db_path=db_path) is not None


# --- jobs: create / validation / detail / delete --------------------------

def _job_form(**overrides):
    form = {
        "title": "Backend Engineer", "company": "Acme", "location": "Berlin",
        "description": "Line one.\nLine two.", "requirements": "Python.",
        "salary_min": "60000", "salary_max": "80000", "employment_type": "full-time",
        "posted_at": "", "source_url": "",
    }
    form.update(overrides)
    return form


def test_create_job_appears_in_list_and_detail(client, db_path):
    resp = client.post("/jobs/new", data=_job_form(), follow_redirects=True)
    assert resp.status_code == 200
    assert b"Backend Engineer" in resp.data  # redirected to the new job's detail page
    assert len(db.get_jobs(db_path=db_path)) == 1

    assert b"Backend Engineer" in client.get("/jobs").data


def test_create_job_empty_title_reruns_with_inline_error_and_keeps_values(client):
    resp = client.post("/jobs/new", data=_job_form(title=""))
    assert resp.status_code == 200
    assert b"Title is required" in resp.data
    assert b'value="Acme"' in resp.data
    assert b'value="Berlin"' in resp.data


def test_job_detail_renders_reasons_as_bullets_and_ai_reasoning_as_quote(client, db_path):
    job_id = db.insert_job(title="Backend Engineer", description="Python role", db_path=db_path)
    criteria_id = criteria_module.add_criteria(label="c1", keywords=["python"], db_path=db_path)
    run_static_matching(criteria_id, db_path=db_path)
    match_id = next(m["id"] for m in db.get_job_matches(db_path=db_path) if m["job_id"] == job_id)
    db.update_job_match(match_id, db_path=db_path, ai_score=85, ai_reasoning="Strong fit for the role.")

    resp = client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert b'<ul class="reasons-list">' in resp.data
    assert b"matched keyword: python" in resp.data
    assert b'class="ai-quote"' in resp.data
    assert b"Strong fit for the role." in resp.data


def test_job_detail_never_shows_raw_json_or_none(client, db_path):
    job_id = db.insert_job(title="Backend Engineer", description="Python role", db_path=db_path)
    criteria_id = criteria_module.add_criteria(label="c1", keywords=["python"], db_path=db_path)
    run_static_matching(criteria_id, db_path=db_path)

    body = client.get(f"/jobs/{job_id}").data.decode()
    assert '["matched keyword' not in body
    assert ">None<" not in body


def test_delete_job_removes_job_and_cascades_match_rows(client, db_path):
    job_id = db.insert_job(title="Backend Engineer", description="Python role", db_path=db_path)
    criteria_id = criteria_module.add_criteria(label="c1", keywords=["python"], db_path=db_path)
    run_static_matching(criteria_id, db_path=db_path)
    assert len(db.get_job_matches(db_path=db_path)) == 1

    resp = client.post(f"/jobs/{job_id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert db.get_job(job_id, db_path=db_path) is None
    assert db.get_job_matches(db_path=db_path) == []


def test_job_detail_404_for_missing_job(client):
    assert client.get("/jobs/9999").status_code == 404


def test_jobs_list_filters_by_status(client, db_path):
    db.insert_job(title="Backend Engineer", description="Python backend role", db_path=db_path)
    db.insert_job(title="Marketing Manager", description="No match here", db_path=db_path)
    criteria_id = criteria_module.add_criteria(label="c1", keywords=["python", "backend"], db_path=db_path)
    run_static_matching(criteria_id, db_path=db_path)

    resp = client.get("/jobs?status=relevant")
    assert b"Backend Engineer" in resp.data
    assert b"Marketing Manager" not in resp.data

    resp = client.get("/jobs?status=rejected")
    assert b"Marketing Manager" in resp.data
    assert b"Backend Engineer" not in resp.data


def test_jobs_list_search_filters_by_title_or_company(client, db_path):
    db.insert_job(title="Backend Engineer", company="Acme", db_path=db_path)
    db.insert_job(title="Data Analyst", company="Widgets Inc", db_path=db_path)

    resp = client.get("/jobs?q=Backend")
    assert b"Backend Engineer" in resp.data
    assert b"Data Analyst" not in resp.data


# --- matches: filters, reject, delete --------------------------------------

def test_matches_reject_moves_status_to_rejected(client, db_path):
    job_id = db.insert_job(title="Backend Engineer", description="Python backend role", db_path=db_path)
    criteria_id = criteria_module.add_criteria(label="c1", keywords=["python", "backend"], db_path=db_path)
    run_static_matching(criteria_id, db_path=db_path)
    match_id = next(m["id"] for m in db.get_job_matches(db_path=db_path) if m["job_id"] == job_id)
    assert db.get_job_match(match_id, db_path=db_path)["status"] == "relevant"

    resp = client.post(f"/matches/{match_id}/reject", follow_redirects=True)
    assert resp.status_code == 200
    assert db.get_job_match(match_id, db_path=db_path)["status"] == "rejected"


def test_matches_list_ai_confirmed_vs_static_only_filter(client, db_path):
    job_id = db.insert_job(title="Backend Engineer", description="Python backend role", db_path=db_path)
    criteria_id = criteria_module.add_criteria(label="c1", keywords=["python", "backend"], db_path=db_path)
    run_static_matching(criteria_id, db_path=db_path)
    match_id = next(m["id"] for m in db.get_job_matches(db_path=db_path) if m["job_id"] == job_id)
    db.update_job_match(match_id, db_path=db_path, ai_score=90, ai_reasoning="Great fit.")

    assert b"Backend Engineer" in client.get("/matches?filter=ai_confirmed").data
    assert b"Backend Engineer" not in client.get("/matches?filter=static_only").data


def test_matches_sorted_by_ai_score_then_static_score(client, db_path):
    lower_ai_job = db.insert_job(title="Job A", description="python backend", db_path=db_path)
    higher_ai_job = db.insert_job(title="Job B", description="python backend", db_path=db_path)
    criteria_id = criteria_module.add_criteria(label="c1", keywords=["python", "backend"], db_path=db_path)
    run_static_matching(criteria_id, db_path=db_path)

    matches = {m["job_id"]: m["id"] for m in db.get_job_matches(db_path=db_path)}
    db.update_job_match(matches[lower_ai_job], db_path=db_path, ai_score=40)
    db.update_job_match(matches[higher_ai_job], db_path=db_path, ai_score=90)

    body = client.get("/matches").data.decode()
    assert body.index("Job B") < body.index("Job A")


def test_matches_delete_removes_job_and_cascades(client, db_path):
    job_id = db.insert_job(title="Backend Engineer", description="python backend", db_path=db_path)
    criteria_id = criteria_module.add_criteria(label="c1", keywords=["python", "backend"], db_path=db_path)
    run_static_matching(criteria_id, db_path=db_path)

    resp = client.post(f"/jobs/{job_id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert db.get_job(job_id, db_path=db_path) is None
    assert db.get_job_matches(db_path=db_path) == []


# --- criteria: create / validation / edit / delete / run -------------------

def test_create_criteria_appears_in_list(client, db_path):
    resp = client.post("/criteria/new", data={
        "label": "backend-berlin", "keywords": "python\nbackend", "exclude_keywords": "senior",
        "locations": "Berlin", "min_salary": "50000", "employment_types": ["full-time"],
        "ai_prompt": "",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"backend-berlin" in resp.data
    assert len(criteria_module.list_criteria(db_path=db_path)) == 1


def test_create_criteria_empty_label_shows_inline_error(client):
    resp = client.post("/criteria/new", data={"label": "", "keywords": "python"})
    assert resp.status_code == 200
    assert b"Label is required" in resp.data


def test_edit_criteria_updates_fields_and_keeps_others(client, db_path):
    criteria_id = criteria_module.add_criteria(
        label="old-label", keywords=["python"], min_salary=40000, db_path=db_path,
    )

    resp = client.post(f"/criteria/{criteria_id}/edit", data={
        "label": "new-label", "keywords": "python\ndjango", "exclude_keywords": "",
        "locations": "", "min_salary": "40000", "employment_types": [], "ai_prompt": "",
    }, follow_redirects=True)
    assert resp.status_code == 200

    row = criteria_module.get_criteria(criteria_id, db_path=db_path)
    assert row["label"] == "new-label"
    assert json.loads(row["keywords"]) == ["python", "django"]
    assert row["min_salary"] == 40000


def test_delete_criteria_cascades_match_rows(client, db_path):
    db.insert_job(title="Backend Engineer", description="python backend", db_path=db_path)
    criteria_id = criteria_module.add_criteria(label="c1", keywords=["python", "backend"], db_path=db_path)
    run_static_matching(criteria_id, db_path=db_path)
    assert len(db.get_job_matches(db_path=db_path)) == 1

    resp = client.post(f"/criteria/{criteria_id}/delete", follow_redirects=True)
    assert resp.status_code == 200
    assert criteria_module.get_criteria(criteria_id, db_path=db_path) is None
    assert db.get_job_matches(db_path=db_path) == []


def test_run_matching_flashes_summary_and_skips_ai_with_no_prompt(client, db_path):
    db.insert_job(title="Backend Engineer", description="python backend", db_path=db_path)
    criteria_id = criteria_module.add_criteria(label="c1", keywords=["python", "backend"], db_path=db_path)

    resp = client.post(f"/criteria/{criteria_id}/run", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Matched 1 relevant of 1 jobs" in resp.data
    assert b"skipped" in resp.data
