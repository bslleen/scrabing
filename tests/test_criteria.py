import json
import os
import tempfile

import pytest

from discovery import criteria as criteria_module
from discovery import db


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db.init_db(db_path=path)
    yield path
    os.remove(path)


def test_add_criteria_serializes_json_list_fields(db_path):
    criteria_id = criteria_module.add_criteria(
        label="backend-berlin",
        keywords=["python", "django"],
        exclude_keywords=["senior"],
        locations=["Berlin", "Remote"],
        min_salary=55000,
        employment_types=["full_time"],
        ai_prompt="Looking for a backend role using Python.",
        db_path=db_path,
    )

    row = db.get_criteria(criteria_id, db_path=db_path)
    assert row["label"] == "backend-berlin"
    assert json.loads(row["keywords"]) == ["python", "django"]
    assert json.loads(row["exclude_keywords"]) == ["senior"]
    assert json.loads(row["locations"]) == ["Berlin", "Remote"]
    assert row["min_salary"] == 55000
    assert json.loads(row["employment_types"]) == ["full_time"]
    assert row["ai_prompt"] == "Looking for a backend role using Python."


def test_list_and_remove_criteria(db_path):
    first_id = criteria_module.add_criteria(label="A", db_path=db_path)
    second_id = criteria_module.add_criteria(label="B", db_path=db_path)

    listed_ids = {row["id"] for row in criteria_module.list_criteria(db_path=db_path)}
    assert listed_ids == {first_id, second_id}

    assert criteria_module.remove_criteria(first_id, db_path=db_path) is True
    remaining_ids = {row["id"] for row in criteria_module.list_criteria(db_path=db_path)}
    assert remaining_ids == {second_id}
    assert criteria_module.remove_criteria(first_id, db_path=db_path) is False


def test_update_criteria_changes_only_given_fields(db_path):
    criteria_id = criteria_module.add_criteria(
        label="backend-berlin", keywords=["python"], min_salary=50000, db_path=db_path,
    )

    updated = criteria_module.update_criteria(criteria_id, keywords=["python", "go"], db_path=db_path)
    assert updated is True

    row = db.get_criteria(criteria_id, db_path=db_path)
    assert json.loads(row["keywords"]) == ["python", "go"]
    assert row["label"] == "backend-berlin"  # untouched
    assert row["min_salary"] == 50000  # untouched


def test_update_criteria_returns_false_for_unknown_id(db_path):
    assert criteria_module.update_criteria(999, keywords=["python"], db_path=db_path) is False
