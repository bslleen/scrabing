"""CRUD for named criteria profiles - what "relevant" means for one job
search: keywords to look for, keywords that disqualify a posting,
acceptable locations, a minimum salary, and acceptable employment types.
Multiple profiles can be saved (e.g. "backend-berlin", "any-remote").

Also stores ai_prompt, a free-text field for Phase 7's AI-assisted
matching - it's just persisted here, not read or interpreted by anything
in this phase.
"""
import json

from discovery import db

# Columns stored as JSON arrays - add_criteria/update_criteria accept a
# Python list (serialized here) or an already-JSON string, same
# convenience discovery.sources gives for scrape_config.
JSON_LIST_FIELDS = ("keywords", "exclude_keywords", "locations", "employment_types")


def _to_json(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def add_criteria(label, keywords=None, exclude_keywords=None, locations=None,
                  min_salary=None, employment_types=None, ai_prompt=None, db_path=None):
    """Creates a new criteria profile and returns its id."""
    return db.insert_criteria(
        label=label,
        keywords=_to_json(keywords),
        exclude_keywords=_to_json(exclude_keywords),
        locations=_to_json(locations),
        min_salary=min_salary,
        employment_types=_to_json(employment_types),
        ai_prompt=ai_prompt,
        db_path=db_path,
    )


def list_criteria(db_path=None):
    return db.get_criteria_list(db_path=db_path)


def get_criteria(criteria_id, db_path=None):
    return db.get_criteria(criteria_id, db_path=db_path)


def update_criteria(criteria_id, db_path=None, **fields):
    """Updates only the given fields of an existing criteria profile.
    Returns True if the profile existed. JSON-list fields accept a Python
    list or an already-JSON string, same as add_criteria.
    """
    normalized = {
        key: (_to_json(value) if key in JSON_LIST_FIELDS else value)
        for key, value in fields.items()
    }
    return db.update_criteria(criteria_id, db_path=db_path, **normalized)


def remove_criteria(criteria_id, db_path=None):
    return db.delete_criteria(criteria_id, db_path=db_path)
