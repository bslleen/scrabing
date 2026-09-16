"""Scores normalized jobs against a criteria profile using simple,
transparent, weighted rules - no AI. This is the *only* filtering stage
when no AI API key is configured (a later phase may layer AI-assisted
matching on top via criteria.ai_prompt), so it has to produce a complete,
usable relevant/rejected split entirely on its own.

Every rule that fires - positive or negative - is recorded as a short
human-readable string in `reasons`, so the UI can show exactly why a job
landed where it did.

Exclude-keyword hits are a hard reject: if any excluded keyword is found,
the job's score is forced below any realistic threshold no matter what
else matched. That's deliberate - an exclude list means "never show me
this," not "penalize this a bit."

Everything else is purely additive: a rule only ever adds points when it
fires, and missing data (no location, no salary) is neutral rather than
punished. The one exception is a salary that *is* present and falls below
the criteria's minimum - that's known information, not an absence of it,
so it carries a real (non-fatal) penalty rather than staying neutral.

employment_types is stored on a criteria profile (discovery.criteria) but
isn't scored here yet - out of scope for this phase.
"""
import json
from datetime import datetime, timezone

import config
from discovery import db

KEYWORD_MATCH_WEIGHT = config.KEYWORD_MATCH_WEIGHT
LOCATION_MATCH_WEIGHT = config.LOCATION_MATCH_WEIGHT
SALARY_MATCH_WEIGHT = config.SALARY_MATCH_WEIGHT

# Any value reliably below STATIC_MATCH_THRESHOLD works here - the exact
# magnitude doesn't matter, only that it guarantees rejection.
HARD_REJECT_SCORE = -1000.0


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _field(row, key):
    """Safe accessor for both a sqlite3.Row (raises IndexError on a
    missing key) and a plain dict (raises KeyError) - returns None either
    way, so score_job works with a real `jobs` row or a hand-built dict.
    """
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _load_json_list(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def parse_criteria(criteria_row):
    """Converts a raw `criteria` db row (JSON columns as TEXT) into a
    plain dict with those columns as Python lists, ready for score_job().
    """
    return {
        "keywords": _load_json_list(_field(criteria_row, "keywords")),
        "exclude_keywords": _load_json_list(_field(criteria_row, "exclude_keywords")),
        "locations": _load_json_list(_field(criteria_row, "locations")),
        "min_salary": _field(criteria_row, "min_salary"),
        "employment_types": _load_json_list(_field(criteria_row, "employment_types")),
    }


def score_job(job, criteria):
    """Scores one job against one criteria profile.

    `job` is a mapping with title/description/requirements/location/
    salary_min/salary_max (a `jobs` db row works directly). `criteria` is
    a plain dict shaped like parse_criteria()'s output.

    Returns (score, reasons): score is a float (can be negative), reasons
    is a list of short human-readable strings for every rule that fired.
    """
    haystack = " ".join(
        str(part) for part in (
            _field(job, "title"),
            _field(job, "description"),
            _field(job, "requirements"),
        )
        if part
    ).lower()

    reasons = []
    score = 0.0
    hard_rejected = False

    for keyword in criteria.get("keywords") or []:
        if keyword and keyword.lower() in haystack:
            score += KEYWORD_MATCH_WEIGHT
            reasons.append(f"matched keyword: {keyword}")

    for keyword in criteria.get("exclude_keywords") or []:
        if keyword and keyword.lower() in haystack:
            hard_rejected = True
            reasons.append(f"excluded keyword: {keyword}")

    job_location = (_field(job, "location") or "").lower()
    if job_location:
        if "remote" in job_location:
            score += LOCATION_MATCH_WEIGHT
            reasons.append("location match: remote")
        else:
            matched_location = next(
                (loc for loc in criteria.get("locations") or [] if loc.lower() in job_location),
                None,
            )
            if matched_location:
                score += LOCATION_MATCH_WEIGHT
                reasons.append(f"location match: {matched_location}")

    min_salary = criteria.get("min_salary")
    job_salary = _field(job, "salary_max")
    if job_salary is None:
        job_salary = _field(job, "salary_min")
    if min_salary is not None and job_salary is not None:
        if job_salary >= min_salary:
            score += SALARY_MATCH_WEIGHT
            reasons.append(f"salary meets minimum: {job_salary} >= {min_salary}")
        else:
            score -= SALARY_MATCH_WEIGHT
            reasons.append(f"salary below minimum: {job_salary} < {min_salary}")

    if hard_rejected:
        score = min(score, HARD_REJECT_SCORE)

    return score, reasons


def run_static_matching(criteria_id, db_path=None):
    """Scores every job in `jobs` against one criteria profile and
    inserts/updates one job_matches row per job: 'relevant' at or above
    STATIC_MATCH_THRESHOLD, 'rejected' below it. Re-running for the same
    criteria updates existing rows rather than duplicating them.

    Returns {"relevant": n, "rejected": n}.
    """
    criteria_row = db.get_criteria(criteria_id, db_path=db_path)
    if criteria_row is None:
        print(f"[matcher] no criteria with id={criteria_id}")
        return {"relevant": 0, "rejected": 0}

    criteria = parse_criteria(criteria_row)
    jobs = db.get_jobs(db_path=db_path)

    existing_matches = {
        row["job_id"]: row
        for row in db.get_job_matches(db_path=db_path)
        if row["criteria_id"] == criteria_id
    }

    counts = {"relevant": 0, "rejected": 0}
    for job in jobs:
        existing = existing_matches.get(job["id"])
        if existing is not None and existing["status"] == "applied":
            # A downstream tool (CV/email generation) has already acted
            # on this one - a rerun must never silently un-apply it by
            # recomputing and overwriting its status.
            print(f"[matcher] job {job['id']} ({job['title']!r}): already applied, leaving untouched")
            continue

        score, reasons = score_job(job, criteria)
        status = "relevant" if score >= config.STATIC_MATCH_THRESHOLD else "rejected"
        counts[status] += 1
        print(f"[matcher] job {job['id']} ({job['title']!r}): score={score}, status={status}")

        fields = {
            "static_score": score,
            "match_reasons": json.dumps(reasons),
            "status": status,
            "matched_at": _utc_now_iso(),
        }

        if existing is not None:
            db.update_job_match(existing["id"], db_path=db_path, **fields)
        else:
            db.insert_job_match(job_id=job["id"], criteria_id=criteria_id, db_path=db_path, **fields)

    print(f"[matcher] criteria {criteria_id}: {counts['relevant']} relevant, {counts['rejected']} rejected")
    return counts
