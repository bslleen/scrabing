import json
import os
import tempfile

import pytest

from discovery import db


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(db_path=path)
    yield path
    os.remove(path)


def test_init_db_is_idempotent(db_path):
    # Fixture already called init_db() once; calling again must not raise.
    db.init_db(db_path=db_path)


def test_source_crud(db_path):
    source_id = db.insert_source(
        url="https://example-company.com/careers",
        name="Example Company",
        type="custom_html",
        scrape_config=json.dumps({"job_link_selector": "a.job"}),
        db_path=db_path,
    )

    row = db.get_source(source_id, db_path=db_path)
    assert row["url"] == "https://example-company.com/careers"
    assert row["name"] == "Example Company"
    assert row["type"] == "custom_html"
    assert json.loads(row["scrape_config"]) == {"job_link_selector": "a.job"}
    assert row in db.get_sources(db_path=db_path)

    assert db.delete_source(source_id, db_path=db_path) is True
    assert db.get_source(source_id, db_path=db_path) is None
    assert db.delete_source(source_id, db_path=db_path) is False


def test_raw_job_crud(db_path):
    source_id = db.insert_source(url="https://example-company.com/careers", db_path=db_path)

    raw_job_id = db.insert_raw_job(
        url="https://example-company.com/careers/backend-engineer",
        source_id=source_id,
        raw_html="<html>...</html>",
        raw_text="Backend Engineer ...",
        status="new",
        db_path=db_path,
    )

    row = db.get_raw_job(raw_job_id, db_path=db_path)
    assert row["url"] == "https://example-company.com/careers/backend-engineer"
    assert row["source_id"] == source_id
    assert row["status"] == "new"
    assert row in db.get_raw_jobs(db_path=db_path)

    assert db.delete_raw_job(raw_job_id, db_path=db_path) is True
    assert db.get_raw_job(raw_job_id, db_path=db_path) is None
    assert db.delete_raw_job(raw_job_id, db_path=db_path) is False


def test_job_crud(db_path):
    source_id = db.insert_source(url="https://example-company.com/careers", db_path=db_path)
    raw_job_id = db.insert_raw_job(
        url="https://example-company.com/careers/backend-engineer",
        source_id=source_id,
        db_path=db_path,
    )

    job_id = db.insert_job(
        raw_job_id=raw_job_id,
        title="Backend Engineer",
        company="Example Company",
        location="Berlin, Germany",
        description="Build things.",
        requirements="Python, SQL.",
        salary_min=55000,
        salary_max=75000,
        employment_type="full_time",
        db_path=db_path,
    )

    row = db.get_job(job_id, db_path=db_path)
    assert row["title"] == "Backend Engineer"
    assert row["salary_min"] == 55000
    assert row["salary_max"] == 75000
    assert row["raw_job_id"] == raw_job_id
    assert row in db.get_jobs(db_path=db_path)

    assert db.delete_job(job_id, db_path=db_path) is True
    assert db.get_job(job_id, db_path=db_path) is None
    assert db.delete_job(job_id, db_path=db_path) is False


def test_criteria_crud(db_path):
    criteria_id = db.insert_criteria(
        label="default",
        keywords=json.dumps(["python", "backend"]),
        exclude_keywords=json.dumps(["senior"]),
        locations=json.dumps(["Berlin", "Remote"]),
        min_salary=50000,
        employment_types=json.dumps(["full_time"]),
        db_path=db_path,
    )

    row = db.get_criteria(criteria_id, db_path=db_path)
    assert row["label"] == "default"
    assert json.loads(row["keywords"]) == ["python", "backend"]
    assert row["min_salary"] == 50000
    assert row in db.get_criteria_list(db_path=db_path)

    assert db.delete_criteria(criteria_id, db_path=db_path) is True
    assert db.get_criteria(criteria_id, db_path=db_path) is None
    assert db.delete_criteria(criteria_id, db_path=db_path) is False


def test_job_match_crud(db_path):
    source_id = db.insert_source(url="https://example-company.com/careers", db_path=db_path)
    raw_job_id = db.insert_raw_job(
        url="https://example-company.com/careers/backend-engineer",
        source_id=source_id,
        db_path=db_path,
    )
    job_id = db.insert_job(raw_job_id=raw_job_id, title="Backend Engineer", db_path=db_path)
    criteria_id = db.insert_criteria(label="default", db_path=db_path)

    match_id = db.insert_job_match(
        job_id=job_id,
        criteria_id=criteria_id,
        static_score=0.85,
        match_reasons=json.dumps(["keyword:python"]),
        status="relevant",
        db_path=db_path,
    )

    row = db.get_job_match(match_id, db_path=db_path)
    assert row["job_id"] == job_id
    assert row["criteria_id"] == criteria_id
    assert row["static_score"] == 0.85
    assert json.loads(row["match_reasons"]) == ["keyword:python"]
    assert row["status"] == "relevant"
    assert row in db.get_job_matches(db_path=db_path)

    assert db.delete_job_match(match_id, db_path=db_path) is True
    assert db.get_job_match(match_id, db_path=db_path) is None
    assert db.delete_job_match(match_id, db_path=db_path) is False
