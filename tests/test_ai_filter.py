import os
import tempfile

import pytest

import config
from discovery import criteria as criteria_module
from discovery import db
from discovery.ai_filter import run_ai_filtering
from discovery.matcher import run_static_matching


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(db_path=path)
    yield path
    os.remove(path)


@pytest.fixture
def scored(db_path):
    """One criteria profile with static matching already run across a
    static-relevant job and two static-rejected jobs (one hard-excluded,
    one with no keyword matches at all).
    """
    criteria_id = criteria_module.add_criteria(
        label="backend-berlin",
        keywords=["python", "backend"],
        exclude_keywords=["senior"],
        ai_prompt="Looking for a mid-level backend role using Python, based in Berlin.",
        db_path=db_path,
    )

    relevant_job_id = db.insert_job(
        title="Backend Engineer", description="Python role in Berlin.", db_path=db_path,
    )
    excluded_job_id = db.insert_job(
        title="Senior Python Architect", description="Python architecture role.", db_path=db_path,
    )
    unrelated_job_id = db.insert_job(
        title="Marketing Coordinator", description="No relevant keywords here.", db_path=db_path,
    )

    run_static_matching(criteria_id, db_path=db_path)

    return {
        "db_path": db_path,
        "criteria_id": criteria_id,
        "relevant_job_id": relevant_job_id,
        "excluded_job_id": excluded_job_id,
        "unrelated_job_id": unrelated_job_id,
    }


def _match_for(db_path, job_id):
    for row in db.get_job_matches(db_path=db_path):
        if row["job_id"] == job_id:
            return row
    raise AssertionError(f"no job_matches row for job {job_id}")


def test_run_ai_filtering_only_sends_static_relevant_jobs(scored, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")
    sent_job_ids = []

    def fake_client(job, ai_prompt):
        sent_job_ids.append(job["id"])
        return 80, "Good match."

    counts = run_ai_filtering(scored["criteria_id"], db_path=scored["db_path"], client_fn=fake_client)

    assert sent_job_ids == [scored["relevant_job_id"]]
    assert counts["scored"] == 1

    # The static-rejected jobs' match rows must be untouched (no ai_score at all).
    assert _match_for(scored["db_path"], scored["excluded_job_id"])["ai_score"] is None
    assert _match_for(scored["db_path"], scored["unrelated_job_id"])["ai_score"] is None


def test_run_ai_filtering_writes_ai_score_and_reasoning(scored, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")

    def fake_client(job, ai_prompt):
        return 90, "Strong match on skills and location."

    run_ai_filtering(scored["criteria_id"], db_path=scored["db_path"], client_fn=fake_client)

    match = _match_for(scored["db_path"], scored["relevant_job_id"])
    assert match["ai_score"] == 90
    assert match["ai_reasoning"] == "Strong match on skills and location."
    assert match["status"] == "relevant"  # above threshold, stays relevant


def test_run_ai_filtering_low_score_flips_status_to_rejected(scored, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_RELEVANCE_THRESHOLD", 50)

    def fake_client(job, ai_prompt):
        return 10, "Not actually relevant on closer inspection."

    counts = run_ai_filtering(scored["criteria_id"], db_path=scored["db_path"], client_fn=fake_client)

    assert counts["flipped_to_rejected"] == 1
    match = _match_for(scored["db_path"], scored["relevant_job_id"])
    assert match["ai_score"] == 10
    assert match["status"] == "rejected"
    assert "Not actually relevant" in match["ai_reasoning"]


def test_run_ai_filtering_skips_cleanly_with_no_api_key(scored, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "")
    called = False

    def fake_client(job, ai_prompt):
        nonlocal called
        called = True
        return 100, "should never run"

    result = run_ai_filtering(scored["criteria_id"], db_path=scored["db_path"], client_fn=fake_client)

    assert result == {"skipped": True}
    assert called is False

    match = _match_for(scored["db_path"], scored["relevant_job_id"])
    assert match["ai_score"] is None
    assert match["status"] == "relevant"  # untouched from the static stage


def test_run_ai_filtering_skips_cleanly_with_no_ai_prompt(db_path, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")
    criteria_id = criteria_module.add_criteria(label="no-prompt", keywords=["python"], db_path=db_path)
    db.insert_job(title="Python Developer", description="Python role.", db_path=db_path)
    run_static_matching(criteria_id, db_path=db_path)

    called = False

    def fake_client(job, ai_prompt):
        nonlocal called
        called = True
        return 100, "should never run"

    result = run_ai_filtering(criteria_id, db_path=db_path, client_fn=fake_client)

    assert result == {"skipped": True}
    assert called is False


def test_run_ai_filtering_records_error_without_crashing(scored, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")

    def failing_client(job, ai_prompt):
        raise RuntimeError("simulated API failure")

    counts = run_ai_filtering(scored["criteria_id"], db_path=scored["db_path"], client_fn=failing_client)

    assert counts["errors"] == 1
    assert counts["scored"] == 0
    match = _match_for(scored["db_path"], scored["relevant_job_id"])
    assert match["ai_score"] is None
    assert match["status"] == "relevant"  # left unchanged on error
