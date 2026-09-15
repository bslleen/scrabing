"""SQLite data layer for the full pipeline: sources, raw_jobs, jobs,
criteria, job_matches. One file on disk, created on first use.

Pure data access - no scraping, normalization, or filtering logic lives
here. Columns documented as JSON in schema.sql (scrape_config, keywords,
exclude_keywords, locations, employment_types, match_reasons) are stored
and returned as plain TEXT; callers are responsible for json.dumps/loads
so this layer never silently reshapes what it's given. criteria.ai_prompt
is a free-text column stored here for Phase 7's AI-assisted matching;
this layer doesn't interpret it.

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
    """Creates any tables that don't exist yet. Safe to call every run.

    CREATE TABLE IF NOT EXISTS won't add a column to a table that already
    exists on disk from an earlier version of schema.sql, so newly added
    columns get a small explicit ALTER TABLE guard below.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executescript(SCHEMA_PATH.read_text())
            for statement in (
                "ALTER TABLE criteria ADD COLUMN ai_prompt TEXT",
                "ALTER TABLE job_matches ADD COLUMN ai_score REAL",
                "ALTER TABLE job_matches ADD COLUMN ai_reasoning TEXT",
            ):
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError:
                    pass  # column already exists
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


def _update(conn, table, row_id, fields):
    """fields: dict of column -> new value; only these columns are
    touched. Returns True if a row with this id existed. A no-op (empty
    fields) still reports whether the row exists, rather than silently
    claiming success.
    """
    if not fields:
        return _get_by_id(conn, table, row_id) is not None

    assignments = ", ".join(f"{key} = :{key}" for key in fields)
    params = dict(fields)
    params["id"] = row_id
    cursor = conn.execute(f"UPDATE {table} SET {assignments} WHERE id = :id", params)
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


def get_raw_job_by_url(url, db_path=None):
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT * FROM raw_jobs WHERE url = ?", (url,)).fetchone()
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


def update_raw_job_status(raw_job_id, status, db_path=None):
    """Returns True if a raw_jobs row with this id existed and was updated."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(
                "UPDATE raw_jobs SET status = ? WHERE id = ?",
                (status, raw_job_id),
            )
            return cursor.rowcount > 0
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


def delete_job_with_matches(job_id, db_path=None):
    """Deletes a jobs row and every job_matches row referencing it, in one
    transaction. job_matches.job_id has no ON DELETE CASCADE and foreign
    keys are enforced, so the child rows must be removed first or the
    jobs delete would fail. Returns True if the job existed.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM job_matches WHERE job_id = ?", (job_id,))
            return _delete(conn, "jobs", job_id)
    finally:
        conn.close()


def search_jobs(status=None, query_text=None, source_id=None, page=1, per_page=25, db_path=None):
    """Returns (rows, total_count) for the /jobs list page.

    status: 'relevant' | 'rejected' | 'pending' | 'unmatched' | None (all).
    A job can have several job_matches rows (one per criteria profile);
    for this general list page, "the" status shown/filtered on is the
    most recently matched row's status (or 'unmatched' if there are
    none) - a deliberate simplification since this page isn't scoped to
    one criteria profile the way /matches is.
    """
    conn = get_connection(db_path)
    try:
        where = []
        params = {}

        if query_text:
            where.append("(j.title LIKE :q OR j.company LIKE :q)")
            params["q"] = f"%{query_text}%"

        if source_id:
            where.append("rj.source_id = :source_id")
            params["source_id"] = source_id

        latest_status_expr = (
            "(SELECT jm.status FROM job_matches jm "
            "WHERE jm.job_id = j.id ORDER BY jm.matched_at DESC LIMIT 1)"
        )

        if status == "unmatched":
            where.append(f"{latest_status_expr} IS NULL")
        elif status in ("relevant", "rejected", "pending"):
            where.append(f"{latest_status_expr} = :status")
            params["status"] = status

        from_clause = "FROM jobs j LEFT JOIN raw_jobs rj ON rj.id = j.raw_job_id"
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        total = conn.execute(f"SELECT COUNT(*) {from_clause} {where_sql}", params).fetchone()[0]

        rows = conn.execute(
            f"""
            SELECT j.*, rj.source_id AS source_id, {latest_status_expr} AS latest_match_status
            {from_clause}
            {where_sql}
            ORDER BY j.id DESC
            LIMIT :limit OFFSET :offset
            """,
            {**params, "limit": per_page, "offset": (page - 1) * per_page},
        ).fetchall()

        return rows, total
    finally:
        conn.close()


# --- dashboard / cross-table aggregates -------------------------------

def count_sources(db_path=None):
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    finally:
        conn.close()


def count_raw_jobs(db_path=None):
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM raw_jobs").fetchone()[0]
    finally:
        conn.close()


def count_raw_jobs_by_status(status, db_path=None):
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM raw_jobs WHERE status = ?", (status,)
        ).fetchone()[0]
    finally:
        conn.close()


def count_raw_jobs_for_source(source_id, db_path=None):
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM raw_jobs WHERE source_id = ?", (source_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def count_jobs(db_path=None):
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    finally:
        conn.close()


def count_job_matches_by_status(status, db_path=None):
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM job_matches WHERE status = ?", (status,)
        ).fetchone()[0]
    finally:
        conn.close()


def count_job_matches_with_ai_score(db_path=None):
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM job_matches WHERE ai_score IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()


def get_latest_source_scraped_at(db_path=None):
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT MAX(last_scraped_at) AS latest FROM sources").fetchone()
        return row["latest"]
    finally:
        conn.close()


def get_recent_relevant_matches(limit=10, db_path=None):
    """Joined job_matches + jobs rows, status='relevant', newest first -
    for the dashboard's "Recent relevant matches" table.
    """
    conn = get_connection(db_path)
    try:
        return conn.execute(
            """
            SELECT jm.*, j.title, j.company, j.location
            FROM job_matches jm
            JOIN jobs j ON j.id = jm.job_id
            WHERE jm.status = 'relevant'
            ORDER BY jm.matched_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()


def get_matches_for_display(filter_mode="all", db_path=None):
    """Joined job_matches + jobs rows for the /matches page.

    filter_mode: 'all' (status='relevant'), 'ai_confirmed' (+ ai_score is
    set), 'static_only' (+ ai_score is not set). Sorted by AI score desc
    (rows with no AI score sort after those with one), then static score
    desc.
    """
    conn = get_connection(db_path)
    try:
        where = "jm.status = 'relevant'"
        if filter_mode == "ai_confirmed":
            where += " AND jm.ai_score IS NOT NULL"
        elif filter_mode == "static_only":
            where += " AND jm.ai_score IS NULL"

        return conn.execute(
            f"""
            SELECT jm.*, j.title, j.company, j.location
            FROM job_matches jm
            JOIN jobs j ON j.id = jm.job_id
            WHERE {where}
            ORDER BY
                CASE WHEN jm.ai_score IS NULL THEN 1 ELSE 0 END,
                jm.ai_score DESC,
                jm.static_score DESC
            """
        ).fetchall()
    finally:
        conn.close()


def get_job_matches_for_job(job_id, db_path=None):
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT * FROM job_matches WHERE job_id = ? ORDER BY matched_at DESC", (job_id,)
        ).fetchall()
    finally:
        conn.close()


# --- criteria ------------------------------------------------------------

def insert_criteria(label=None, keywords=None, exclude_keywords=None, locations=None,
                     min_salary=None, employment_types=None, ai_prompt=None, db_path=None):
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
                "ai_prompt": ai_prompt,
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


def delete_criteria_with_matches(criteria_id, db_path=None):
    """Deletes a criteria row and every job_matches row referencing it, in
    one transaction - same reasoning as delete_job_with_matches. Unlike a
    source (whose raw_jobs/jobs cascade would risk losing scraped and
    normalized data), a criteria's only dependents are its cheap-to-
    regenerate job_matches rows, so cascading here is the more useful
    default rather than blocking with a foreign-key error.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM job_matches WHERE criteria_id = ?", (criteria_id,))
            return _delete(conn, "criteria", criteria_id)
    finally:
        conn.close()


def update_criteria(criteria_id, db_path=None, **fields):
    """Updates only the given columns of a criteria row. Returns True if
    the row existed.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            return _update(conn, "criteria", criteria_id, fields)
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


def update_job_match(job_match_id, db_path=None, **fields):
    """Updates only the given columns of a job_matches row. Returns True
    if the row existed.
    """
    conn = get_connection(db_path)
    try:
        with conn:
            return _update(conn, "job_matches", job_match_id, fields)
    finally:
        conn.close()
