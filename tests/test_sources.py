import json
import os
import tempfile

import pytest

from discovery import db, sources


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(db_path=path)
    yield path
    os.remove(path)


def test_add_source_without_scrape_config_uses_default_heuristic(db_path):
    source_id = sources.add_source(url="https://example.com/careers", db_path=db_path)

    row = db.get_source(source_id, db_path=db_path)
    assert row["type"] == "custom_html"
    assert json.loads(row["scrape_config"]) == sources.DEFAULT_SCRAPE_CONFIG


def test_add_source_with_explicit_scrape_config(db_path):
    custom_config = {"job_link_selector": "a.job-card", "job_link_keywords": ["stelle"]}
    source_id = sources.add_source(
        url="https://example.com/jobs",
        name="Example Jobs",
        scrape_config=custom_config,
        db_path=db_path,
    )

    row = db.get_source(source_id, db_path=db_path)
    assert row["name"] == "Example Jobs"
    assert json.loads(row["scrape_config"]) == custom_config


def test_list_and_remove_sources(db_path):
    first_id = sources.add_source(url="https://example.com/careers", name="Source A", db_path=db_path)
    second_id = sources.add_source(url="https://example.com/jobs", name="Source B", db_path=db_path)

    listed = sources.list_sources(db_path=db_path)
    listed_ids = {row["id"] for row in listed}
    assert listed_ids == {first_id, second_id}

    assert sources.remove_source(first_id, db_path=db_path) is True

    remaining = sources.list_sources(db_path=db_path)
    remaining_ids = {row["id"] for row in remaining}
    assert remaining_ids == {second_id}

    # Removing again confirms it's actually gone, not just filtered out.
    assert sources.remove_source(first_id, db_path=db_path) is False
