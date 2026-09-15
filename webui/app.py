"""Local web UI: view, insert, and delete rows across the pipeline's
tables, and trigger discovery/scraping/matching from the browser instead
of the CLI. No AI beyond what discovery.ai_filter already does, no auth,
no telemetry - a single-user tool that only ever talks to the local
SQLite file (plus whatever discovery/scraper.py and discovery/ai_filter.py
already reach out to when a "Run" button is pressed). Flask is a thin
request/response layer here; all data access goes through discovery.db.
"""
import json
import sqlite3
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO

from flask import Flask, abort, current_app, flash, redirect, render_template, request, url_for

import config
from discovery import ai_filter, criteria as criteria_module, db, sources as sources_module
from discovery.matcher import run_static_matching
from discovery.normalizer import normalize_raw_job
from discovery.scraper import scrape_source

EMPLOYMENT_TYPE_CHOICES = ("full-time", "part-time", "contract", "internship")
JOBS_PER_PAGE = 25
STATIC_SCORE_CONFIDENCE_CEILING = 20  # see _confidence_percent()


def create_app(db_path=None):
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path or config.DB_PATH
    app.secret_key = "job-discovery-local-dev"  # local single-user tool, no auth/sessions of value

    db.init_db(db_path=app.config["DB_PATH"])

    app.jinja_env.filters["relative_time"] = relative_time
    app.jinja_env.filters["salary_range"] = format_salary_range
    app.jinja_env.filters["dash"] = dash_if_empty
    app.jinja_env.filters["json_list_display"] = json_list_display
    app.jinja_env.globals["confidence_percent"] = confidence_percent

    _register_routes(app)
    return app


def _db_path():
    return current_app.config["DB_PATH"]


# --- template helpers (also used directly by routes) -----------------------

def dash_if_empty(value):
    if value is None or value == "":
        return "—"
    return value


def relative_time(value):
    """'2h ago' style label. Callers show the raw timestamp in a title
    tooltip alongside this, per the "full timestamp on hover" rule.
    """
    if not value:
        return "never"
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    seconds = int((datetime.now(timezone.utc) - moment).total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _format_thousands(value):
    if value % 1000 == 0:
        return f"{value // 1000}k"
    return f"{value / 1000:.1f}k"


def format_salary_range(job):
    salary_min, salary_max = job["salary_min"], job["salary_max"]
    if salary_min is None and salary_max is None:
        return "—"
    if salary_min is not None and salary_max is not None:
        return f"€{_format_thousands(salary_min)}–{_format_thousands(salary_max)}"
    if salary_min is not None:
        return f"€{_format_thousands(salary_min)}+"
    return f"up to €{_format_thousands(salary_max)}"


def json_list_display(value):
    """Renders a JSON-array TEXT column as plain comma-joined text - a
    table cell or badge row must never show raw JSON.
    """
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def confidence_percent(match):
    """0-100 bar width for /matches. ai_score is already 0-100 and used
    directly; a static-only score has no natural upper bound, so it's
    scaled against an assumed practical ceiling instead.
    """
    if match["ai_score"] is not None:
        return max(0, min(100, match["ai_score"]))
    static_score = match["static_score"] or 0
    return max(0, min(100, (static_score / STATIC_SCORE_CONFIDENCE_CEILING) * 100))


def _lines_to_list(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _parse_optional_int(form, field_name, errors, label):
    raw = (form.get(field_name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        errors[field_name] = f"{label} must be a whole number."
        return None


def _find_blocked_message(captured_output):
    """Looks for the crawler's own 403/429 message in captured stdout, so
    it can be flashed verbatim rather than a generic failure notice.
    """
    for line in captured_output.splitlines():
        if "returned HTTP 403" in line or "returned HTTP 429" in line:
            return line.split("] ", 1)[-1] if "] " in line else line
    return None


def _criteria_form_to_fields(form):
    errors = {}
    label = (form.get("label") or "").strip()
    if not label:
        errors["label"] = "Label is required."

    min_salary = _parse_optional_int(form, "min_salary", errors, "Minimum salary")

    fields = {
        "label": label,
        "keywords": _lines_to_list(form.get("keywords")),
        "exclude_keywords": _lines_to_list(form.get("exclude_keywords")),
        "locations": _lines_to_list(form.get("locations")),
        "min_salary": min_salary,
        "employment_types": form.getlist("employment_types"),
        "ai_prompt": (form.get("ai_prompt") or "").strip() or None,
    }
    return fields, errors


def _criteria_row_to_form(row):
    return {
        "label": row["label"],
        "keywords": "\n".join(json_list_display(row["keywords"])),
        "exclude_keywords": "\n".join(json_list_display(row["exclude_keywords"])),
        "locations": "\n".join(json_list_display(row["locations"])),
        "min_salary": row["min_salary"],
        "employment_types": json_list_display(row["employment_types"]),
        "ai_prompt": row["ai_prompt"],
    }


def _register_routes(app):
    # --- dashboard ----------------------------------------------------

    @app.route("/")
    def dashboard():
        db_path = _db_path()
        sources_count = db.count_sources(db_path=db_path)
        raw_jobs_count = db.count_raw_jobs(db_path=db_path)
        raw_jobs_new = db.count_raw_jobs_by_status("new", db_path=db_path)
        raw_jobs_error = db.count_raw_jobs_by_status("error", db_path=db_path)
        jobs_count = db.count_jobs(db_path=db_path)
        relevant_count = db.count_job_matches_by_status("relevant", db_path=db_path)
        ai_scored_count = db.count_job_matches_with_ai_score(db_path=db_path)
        last_scraped = db.get_latest_source_scraped_at(db_path=db_path)
        recent_matches = db.get_recent_relevant_matches(limit=10, db_path=db_path)

        stats = [
            {"label": "Sources", "number": sources_count,
             "subline": f"last scrape {relative_time(last_scraped)}"},
            {"label": "Raw jobs scraped", "number": raw_jobs_count,
             "subline": f"{raw_jobs_new} pending normalization"},
            {"label": "Jobs normalized", "number": jobs_count,
             "subline": f"{raw_jobs_error} error(s)"},
            {"label": "Relevant matches", "number": relevant_count,
             "subline": f"{ai_scored_count} with AI score"},
        ]

        return render_template("dashboard.html", stats=stats, recent_matches=recent_matches)

    # --- sources --------------------------------------------------------

    @app.route("/sources")
    def sources_list():
        db_path = _db_path()
        rows = db.get_sources(db_path=db_path)
        jobs_found = {row["id"]: db.count_raw_jobs_for_source(row["id"], db_path=db_path) for row in rows}
        return render_template("sources/list.html", sources=rows, jobs_found=jobs_found)

    @app.route("/sources/new", methods=["GET"])
    def sources_new():
        return render_template("sources/new.html", form={}, errors={})

    @app.route("/sources/new", methods=["POST"])
    def sources_create():
        form = request.form
        errors = {}

        url = (form.get("url") or "").strip()
        name = (form.get("name") or "").strip() or None
        scrape_config_raw = (form.get("scrape_config") or "").strip()

        if not url:
            errors["url"] = "URL is required."
        elif not (url.startswith("http://") or url.startswith("https://")):
            errors["url"] = "URL must start with http:// or https://."

        scrape_config_value = None
        if scrape_config_raw:
            try:
                scrape_config_value = json.dumps(json.loads(scrape_config_raw))
            except json.JSONDecodeError:
                errors["scrape_config"] = "Must be valid JSON."

        if errors:
            return render_template("sources/new.html", form=form, errors=errors)

        sources_module.add_source(url=url, name=name, scrape_config=scrape_config_value, db_path=_db_path())
        flash("Source added.", "success")
        return redirect(url_for("sources_list"))

    @app.route("/sources/<int:source_id>/run", methods=["POST"])
    def sources_run(source_id):
        db_path = _db_path()
        if db.get_source(source_id, db_path=db_path) is None:
            flash("Source not found.", "danger")
            return redirect(url_for("sources_list"))

        buffer = StringIO()
        with redirect_stdout(buffer):
            inserted_ids = scrape_source(source_id, db_path=db_path)

            pending = [row for row in db.get_raw_jobs(db_path=db_path) if row["status"] == "new"]
            for row in pending:
                normalize_raw_job(row["id"], db_path=db_path)

            relevant_total = 0
            for one_criteria in db.get_criteria_list(db_path=db_path):
                counts = run_static_matching(one_criteria["id"], db_path=db_path)
                relevant_total += counts["relevant"]

        blocked_message = _find_blocked_message(buffer.getvalue())
        if blocked_message:
            flash(blocked_message, "danger")
        else:
            flash(f"Discovered {len(inserted_ids)} job(s), {relevant_total} relevant.", "success")

        return redirect(url_for("sources_list"))

    @app.route("/sources/<int:source_id>/delete", methods=["POST"])
    def sources_delete(source_id):
        db_path = _db_path()
        try:
            deleted = db.delete_source(source_id, db_path=db_path)
        except sqlite3.IntegrityError:
            count = db.count_raw_jobs_for_source(source_id, db_path=db_path)
            flash(f"Cannot delete: {count} scraped job(s) still reference this source.", "danger")
            return redirect(url_for("sources_list"))

        flash("Source deleted." if deleted else "Source not found.", "success" if deleted else "danger")
        return redirect(url_for("sources_list"))

    # --- jobs -------------------------------------------------------------

    @app.route("/jobs")
    def jobs_list():
        db_path = _db_path()
        status = request.args.get("status") or None
        query_text = request.args.get("q") or None
        source_id = request.args.get("source_id", type=int)
        page = max(1, request.args.get("page", default=1, type=int) or 1)

        rows, total = db.search_jobs(
            status=status, query_text=query_text, source_id=source_id,
            page=page, per_page=JOBS_PER_PAGE, db_path=db_path,
        )

        return render_template(
            "jobs/list.html",
            jobs=rows, total=total, page=page, per_page=JOBS_PER_PAGE,
            status=status or "", query_text=query_text or "", source_id=source_id,
            sources=db.get_sources(db_path=db_path),
            has_filters=bool(status or query_text or source_id),
        )

    @app.route("/jobs/new", methods=["GET"])
    def jobs_new():
        return render_template("jobs/new.html", form={}, errors={})

    @app.route("/jobs/new", methods=["POST"])
    def jobs_create():
        db_path = _db_path()
        form = request.form
        errors = {}

        title = (form.get("title") or "").strip()
        company = (form.get("company") or "").strip()
        if not title:
            errors["title"] = "Title is required."
        if not company:
            errors["company"] = "Company is required."

        salary_min = _parse_optional_int(form, "salary_min", errors, "Minimum salary")
        salary_max = _parse_optional_int(form, "salary_max", errors, "Maximum salary")

        if errors:
            return render_template("jobs/new.html", form=form, errors=errors)

        source_url = (form.get("source_url") or "").strip()
        raw_job_id = None
        if source_url:
            existing = db.get_raw_job_by_url(source_url, db_path=db_path)
            raw_job_id = existing["id"] if existing else db.insert_raw_job(
                url=source_url, status="normalized", db_path=db_path,
            )

        job_id = db.insert_job(
            raw_job_id=raw_job_id,
            title=title,
            company=company,
            location=(form.get("location") or "").strip() or None,
            description=(form.get("description") or "").strip() or None,
            requirements=(form.get("requirements") or "").strip() or None,
            salary_min=salary_min,
            salary_max=salary_max,
            employment_type=(form.get("employment_type") or "").strip() or None,
            posted_at=(form.get("posted_at") or "").strip() or None,
            db_path=db_path,
        )
        flash("Job added.", "success")
        return redirect(url_for("jobs_detail", job_id=job_id))

    @app.route("/jobs/<int:job_id>")
    def jobs_detail(job_id):
        db_path = _db_path()
        job = db.get_job(job_id, db_path=db_path)
        if job is None:
            abort(404)

        raw_job = db.get_raw_job(job["raw_job_id"], db_path=db_path) if job["raw_job_id"] else None
        source = db.get_source(raw_job["source_id"], db_path=db_path) if raw_job and raw_job["source_id"] else None

        matches = db.get_job_matches_for_job(job_id, db_path=db_path)
        # A job can have one job_matches row per criteria profile; this
        # single-job page shows one Match card, so the most recently
        # scored profile wins (get_job_matches_for_job is already sorted
        # newest-first).
        latest_match = matches[0] if matches else None
        match_reasons = json_list_display(latest_match["match_reasons"]) if latest_match else []

        return render_template(
            "jobs/detail.html", job=job, raw_job=raw_job, source=source,
            latest_match=latest_match, match_reasons=match_reasons,
        )

    @app.route("/jobs/<int:job_id>/delete", methods=["POST"])
    def jobs_delete(job_id):
        deleted = db.delete_job_with_matches(job_id, db_path=_db_path())
        flash("Job deleted." if deleted else "Job not found.", "success" if deleted else "danger")
        return redirect(url_for("jobs_list"))

    # --- matches ------------------------------------------------------

    @app.route("/matches")
    def matches_list():
        filter_mode = request.args.get("filter", "all")
        if filter_mode not in ("all", "ai_confirmed", "static_only"):
            filter_mode = "all"
        rows = db.get_matches_for_display(filter_mode=filter_mode, db_path=_db_path())
        return render_template("matches/list.html", matches=rows, filter_mode=filter_mode)

    @app.route("/matches/<int:match_id>/reject", methods=["POST"])
    def matches_reject(match_id):
        updated = db.update_job_match(match_id, db_path=_db_path(), status="rejected")
        flash("Match rejected." if updated else "Match not found.", "success" if updated else "danger")
        return redirect(url_for("matches_list"))

    # --- criteria -------------------------------------------------------

    @app.route("/criteria")
    def criteria_list():
        rows = criteria_module.list_criteria(db_path=_db_path())
        return render_template("criteria/list.html", criteria_list=rows)

    @app.route("/criteria/new", methods=["GET"])
    def criteria_new():
        return render_template(
            "criteria/form.html", form={}, errors={}, mode="new",
            employment_choices=EMPLOYMENT_TYPE_CHOICES,
        )

    @app.route("/criteria/new", methods=["POST"])
    def criteria_create():
        fields, errors = _criteria_form_to_fields(request.form)
        if errors:
            return render_template(
                "criteria/form.html", form=request.form, errors=errors, mode="new",
                employment_choices=EMPLOYMENT_TYPE_CHOICES,
            )

        criteria_module.add_criteria(db_path=_db_path(), **fields)
        flash("Criteria profile created.", "success")
        return redirect(url_for("criteria_list"))

    @app.route("/criteria/<int:criteria_id>/edit", methods=["GET"])
    def criteria_edit(criteria_id):
        row = criteria_module.get_criteria(criteria_id, db_path=_db_path())
        if row is None:
            abort(404)
        return render_template(
            "criteria/form.html", form=_criteria_row_to_form(row), errors={}, mode="edit",
            criteria_id=criteria_id, employment_choices=EMPLOYMENT_TYPE_CHOICES,
        )

    @app.route("/criteria/<int:criteria_id>/edit", methods=["POST"])
    def criteria_update(criteria_id):
        fields, errors = _criteria_form_to_fields(request.form)
        if errors:
            return render_template(
                "criteria/form.html", form=request.form, errors=errors, mode="edit",
                criteria_id=criteria_id, employment_choices=EMPLOYMENT_TYPE_CHOICES,
            )

        criteria_module.update_criteria(criteria_id, db_path=_db_path(), **fields)
        flash("Criteria profile updated.", "success")
        return redirect(url_for("criteria_list"))

    @app.route("/criteria/<int:criteria_id>/delete", methods=["POST"])
    def criteria_delete(criteria_id):
        deleted = db.delete_criteria_with_matches(criteria_id, db_path=_db_path())
        flash("Criteria profile deleted." if deleted else "Criteria profile not found.",
              "success" if deleted else "danger")
        return redirect(url_for("criteria_list"))

    @app.route("/criteria/<int:criteria_id>/run", methods=["POST"])
    def criteria_run(criteria_id):
        db_path = _db_path()
        static_counts = run_static_matching(criteria_id, db_path=db_path)
        total = static_counts["relevant"] + static_counts["rejected"]

        ai_result = ai_filter.run_ai_filtering(criteria_id, db_path=db_path)
        ai_note = "skipped - no key" if ai_result.get("skipped") else "ran"

        flash(f"Matched {static_counts['relevant']} relevant of {total} jobs (AI: {ai_note}).", "success")
        return redirect(url_for("criteria_list"))


if __name__ == "__main__":
    # Not created at module level: importing this module (e.g. from
    # tests, which want create_app(db_path=<temp db>)) must not have the
    # side effect of touching the real default database file.
    create_app().run(host=config.WEBUI_HOST, port=config.WEBUI_PORT, debug=False)
