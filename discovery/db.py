"""SQLite data layer for the full pipeline: sources, raw_jobs, jobs,
criteria, job_matches. One file on disk, created on first use.

Pure data access - no scraping, normalization, or filtering logic lives
here. Columns documented as JSON in schema.sql (scrape_config, keywords,
exclude_keywords, locations, employment_types, match_reasons) are stored
and returned as plain TEXT; callers are responsible for json.dumps/loads
so this layer never silently reshapes what it's given.

All queries are parameterized - never build SQL by string-formatting a
value into it, even though this database is local and single-user.
"""
import sqlite3
from pathlib import Path

import config

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def get_connection(db_path=None):
    conn = sqlite3.connect(db_path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=None):
    """Creates any tables that don't exist yet. Safe to call every run."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executescript(SCHEMA_PATH.read_text())
    finally:
        conn.close()


# --- generic helpers, shared by every table's thin wrappers ----------------

def _insert(conn, table, fields):
    """fields: dict of column -> value. Returns the new row's id."""
    columns = ", ".join(fields.keys())
    placeholders = ", ".join(f":{key}" for key in fields.keys())
    cursor = conn.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
        fields,
    )
    return cursor.lastrowid


def _get_all(conn, table):
    return conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()


def _get_by_id(conn, table, row_id):
    return conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()


def _delete(conn, table, row_id):
    """Returns True if a row was deleted, False if row_id didn't exist."""
    cursor = conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
    return cursor.rowcount > 0


# --- sources -----------------------------------------------------------

def insert_source(url, name=None, type="custom_html", scrape_config=None, last_scraped_at=None, db_path=None):
    conn = get_connection(db_path)
    try:
        with conn:
            return _insert(conn, "sources", {
                "url": url,
                "name": name,
                "type": type,
                "scrape_config": scrape_config,
                "last_scraped_at": last_scraped_at,
            })
    finally:
        conn.close()


def get_sources(db_path=None):
    conn = get_connection(db_path)
    try:
        return _get_all(conn, "sources")
    finally:
        conn.close()


def get_source(source_id, db_path=None):
    conn = get_connection(db_path)
    try:
        return _get_by_id(conn, "sources", source_id)
    finally:
        conn.close()


def delete_source(source_id, db_path=None):
    conn = get_connection(db_path)
    try:
        with conn:
            return _delete(conn, "sources", source_id)
    finally:
        conn.close()


def update_source_last_scraped(source_id, last_scraped_at, db_path=None):
    """Returns True if a source with this id existed and was updated."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(
                "UPDATE sources SET last_scraped_at = ? WHERE id = ?",
                (last_scraped_at, source_id),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


# --- raw_jobs ------------------------------------------------------------

def insert_raw_job(url, source_id=None, external_id=None, raw_html=None, raw_text=None,
                    scraped_at=None, status="new", db_path=None):
    conn = get_connection(db_path)
    try:
        with conn:
            return _insert(conn, "raw_jobs", {
                "source_id": source_id,
                "external_id": external_id,
                "url": url,
                "raw_html": raw_html,
                "raw_text": raw_text,
                "scraped_at": scraped_at,
                "status": status,
            })
    finally:
        conn.close()


def get_raw_jobs(db_path=None):
    conn = get_connection(db_path)
    try:
        return _get_all(conn, "raw_jobs")
    finally:
        conn.close()


def get_raw_job(raw_job_id, db_path=None):
    conn = get_connection(db_path)
    try:
        return _get_by_id(conn, "raw_jobs", raw_job_id)
    finally:
        conn.close()


def delete_raw_job(raw_job_id, db_path=None):
    conn = get_connection(db_path)
    try:
        with conn:
            return _delete(conn, "raw_jobs", raw_job_id)
    finally:
        conn.close()


# --- jobs ------------------------------------------------------------------

def insert_job(raw_job_id=None, title=None, company=None, location=None, description=None,
               requirements=None, salary_min=None, salary_max=None, employment_type=None,
               posted_at=None, normalized_at=None, db_path=None):
    conn = get_connection(db_path)
    try:
        with conn:
            return _insert(conn, "jobs", {
                "raw_job_id": raw_job_id,
                "title": title,
                "company": company,
                "location": location,
                "description": description,
                "requirements": requirements,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "employment_type": employment_type,
                "posted_at": posted_at,
                "normalized_at": normalized_at,
            })
    finally:
        conn.close()


def get_jobs(db_path=None):
    conn = get_connection(db_path)
    try:
        return _get_all(conn, "jobs")
    finally:
        conn.close()


def get_job(job_id, db_path=None):
    conn = get_connection(db_path)
    try:
        return _get_by_id(conn, "jobs", job_id)
    finally:
        conn.close()


def delete_job(job_id, db_path=None):
    conn = get_connection(db_path)
    try:
        with conn:
            return _delete(conn, "jobs", job_id)
    finally:
        conn.close()


# --- criteria ------------------------------------------------------------

def insert_criteria(label=None, keywords=None, exclude_keywords=None, locations=None,
                     min_salary=None, employment_types=None, db_path=None):
    conn = get_connection(db_path)
    try:
        with conn:
            return _insert(conn, "criteria", {
                "label": label,
                "keywords": keywords,
                "exclude_keywords": exclude_keywords,
                "locations": locations,
                "min_salary": min_salary,
                "employment_types": employment_types,
            })
    finally:
        conn.close()


def get_criteria_list(db_path=None):
    conn = get_connection(db_path)
    try:
        return _get_all(conn, "criteria")
    finally:
        conn.close()


def get_criteria(criteria_id, db_path=None):
    conn = get_connection(db_path)
    try:
        return _get_by_id(conn, "criteria", criteria_id)
    finally:
        conn.close()


def delete_criteria(criteria_id, db_path=None):
    conn = get_connection(db_path)
    try:
        with conn:
            return _delete(conn, "criteria", criteria_id)
    finally:
        conn.close()


# --- job_matches -----------------------------------------------------------

def insert_job_match(job_id, criteria_id, static_score=None, match_reasons=None,
                      status="pending", matched_at=None, db_path=None):
    conn = get_connection(db_path)
    try:
        with conn:
            return _insert(conn, "job_matches", {
                "job_id": job_id,
                "criteria_id": criteria_id,
                "static_score": static_score,
                "match_reasons": match_reasons,
                "status": status,
                "matched_at": matched_at,
            })
    finally:
        conn.close()


def get_job_matches(db_path=None):
    conn = get_connection(db_path)
    try:
        return _get_all(conn, "job_matches")
    finally:
        conn.close()


def get_job_match(job_match_id, db_path=None):
    conn = get_connection(db_path)
    try:
        return _get_by_id(conn, "job_matches", job_match_id)
    finally:
        conn.close()


def delete_job_match(job_match_id, db_path=None):
    conn = get_connection(db_path)
    try:
        with conn:
            return _delete(conn, "job_matches", job_match_id)
    finally:
        conn.close()
