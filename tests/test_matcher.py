import json
import os
import tempfile

import pytest

from discovery import criteria as criteria_module
from discovery import db
from discovery.matcher import run_static_matching, score_job


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(db_path=path)
    yield path
    os.remove(path)


# --- score_job (pure scoring logic, no DB) --------------------------------

BASE_CRITERIA = {
    "keywords": ["python", "django"],
    "exclude_keywords": ["senior"],
    "locations": ["Berlin"],
    "min_salary": 55000,
    "employment_types": [],
}


def test_score_job_accumulates_keyword_location_and_salary_matches():
    job = {
        "title": "Backend Engineer",
        "description": "We are looking for a Python developer with Django experience.",
        "requirements": None,
        "location": "Berlin, Germany",
        "salary_min": 60000,
        "salary_max": 80000,
    }

    score, reasons = score_job(job, BASE_CRITERIA)

    assert score == 9  # 2 keywords (+2 each) + location (+3) + salary (+2)
    assert "matched keyword: python" in reasons
    assert "matched keyword: django" in reasons
    assert "location match: Berlin" in reasons
    assert "salary meets minimum: 80000 >= 55000" in reasons


def test_score_job_exclude_keyword_is_a_hard_reject_despite_other_matches():
    job = {
        "title": "Senior Python Architect",
        "description": "Python architecture role requiring Django knowledge.",
        "requirements": None,
        "location": "Munich, Germany",
        "salary_min": 70000,
        "salary_max": 90000,
    }

    score, reasons = score_job(job, BASE_CRITERIA)

    assert score < 0
    assert "excluded keyword: senior" in reasons
    assert "matched keyword: python" in reasons  # still recorded, just outweighed


def test_score_job_missing_salary_is_neutral_not_a_rejection():
    job = {
        "title": "Marketing Coordinator",
        "description": "No relevant keywords here.",
        "requirements": None,
        "location": "Paris, France",
        "salary_min": None,
        "salary_max": None,
    }

    score, reasons = score_job(job, BASE_CRITERIA)

    assert score == 0
    assert reasons == []


def test_score_job_remote_location_matches_regardless_of_criteria_locations():
    job = {
        "title": "Python Developer",
        "description": "Django backend work.",
        "requirements": None,
        "location": "Remote (Europe)",
        "salary_min": None,
        "salary_max": None,
    }

    _, reasons = score_job(job, BASE_CRITERIA)

    assert "location match: remote" in reasons


def test_score_job_salary_below_minimum_is_penalized_not_neutral():
    job = {
        "title": "Python Developer",
        "description": "Django backend work.",
        "requirements": None,
        "location": None,
        "salary_min": 30000,
        "salary_max": 40000,
    }

    score, reasons = score_job(job, BASE_CRITERIA)

    assert "salary below minimum: 40000 < 55000" in reasons
    assert score == (2 + 2) - 2  # two keyword matches minus the salary penalty


# --- run_static_matching (full DB-backed run) ------------------------------

def test_run_static_matching_produces_expected_relevant_rejected_split(db_path):
    criteria_id = criteria_module.add_criteria(
        label="backend-berlin",
        keywords=["python", "django"],
        exclude_keywords=["senior"],
        locations=["Berlin"],
        min_salary=55000,
        db_path=db_path,
    )

    relevant_job_id = db.insert_job(
        title="Backend Engineer",
        description="We are looking for a Python developer with Django experience.",
        location="Berlin, Germany",
        salary_min=60000,
        salary_max=80000,
        db_path=db_path,
    )
    excluded_job_id = db.insert_job(
        title="Senior Python Architect",
        description="Python architecture role requiring Django knowledge.",
        location="Munich, Germany",
        salary_min=70000,
        salary_max=90000,
        db_path=db_path,
    )
    unrelated_job_id = db.insert_job(
        title="Marketing Coordinator",
        description="Manage social media campaigns and marketing materials.",
        location="Paris, France",
        db_path=db_path,
    )

    counts = run_static_matching(criteria_id, db_path=db_path)

    assert counts == {"relevant": 1, "rejected": 2}

    matches = {row["job_id"]: row for row in db.get_job_matches(db_path=db_path)}

    relevant_match = matches[relevant_job_id]
    assert relevant_match["status"] == "relevant"
    relevant_reasons = json.loads(relevant_match["match_reasons"])
    assert "matched keyword: python" in relevant_reasons
    assert "location match: Berlin" in relevant_reasons

    excluded_match = matches[excluded_job_id]
    assert excluded_match["status"] == "rejected"
    assert "excluded keyword: senior" in json.loads(excluded_match["match_reasons"])

    unrelated_match = matches[unrelated_job_id]
    assert unrelated_match["status"] == "rejected"
    assert json.loads(unrelated_match["match_reasons"]) == []


def test_run_static_matching_rerun_updates_rather_than_duplicates(db_path):
    criteria_id = criteria_module.add_criteria(label="backend-berlin", keywords=["python"], db_path=db_path)
    db.insert_job(title="Python Developer", description="Python role.", db_path=db_path)

    run_static_matching(criteria_id, db_path=db_path)
    run_static_matching(criteria_id, db_path=db_path)

    assert len(db.get_job_matches(db_path=db_path)) == 1


def test_run_static_matching_returns_zero_counts_for_unknown_criteria(db_path):
    assert run_static_matching(999, db_path=db_path) == {"relevant": 0, "rejected": 0}


def test_run_static_matching_never_overwrites_an_applied_match(db_path):
    criteria_id = criteria_module.add_criteria(label="backend-berlin", keywords=["python"], db_path=db_path)
    job_id = db.insert_job(title="Python Developer", description="Python role.", db_path=db_path)

    run_static_matching(criteria_id, db_path=db_path)
    match = next(m for m in db.get_job_matches(db_path=db_path) if m["job_id"] == job_id)
    db.update_job_match(match["id"], db_path=db_path, status="applied", applied_at="2026-01-01T00:00:00+00:00")

    # A later rerun (e.g. after re-scraping) must not recompute and
    # silently un-apply a job a downstream tool already acted on.
    counts = run_static_matching(criteria_id, db_path=db_path)

    assert counts == {"relevant": 0, "rejected": 0}  # the applied job isn't recounted either way
    updated = db.get_job_match(match["id"], db_path=db_path)
    assert updated["status"] == "applied"
    assert updated["applied_at"] == "2026-01-01T00:00:00+00:00"
    assert len(db.get_job_matches(db_path=db_path)) == 1  # no duplicate row created either
