"""Ties discovery.crawler, discovery.scraper, discovery.normalizer,
discovery.matcher, and discovery.ai_filter into one end-to-end run for a
single website: discover -> scrape -> normalize -> static match -> AI
filter (optional). This is what discover.py (the CLI entry point) calls;
it exists as its own module so that logic isn't duplicated if something
else ever needs to trigger the same sequence.

Re-running against the same URL reuses its existing `sources` row rather
than creating a duplicate - discovery is meant to be repeatable.
"""
from discovery import ai_filter, db
from discovery import sources as sources_module
from discovery.matcher import run_static_matching
from discovery.normalizer import normalize_raw_job
from discovery.scraper import scrape_source


def run_pipeline(url, criteria_label=None, use_ai=True, name=None, on_progress=None):
    """Runs the full pipeline for one website URL.

    criteria_label: label of a saved criteria profile (discovery.criteria)
    to match against. If omitted, or if no profile with that label
    exists, static/AI matching is skipped - discovery/scraping/
    normalization still run and are reported.

    on_progress(message), if given, is called with a short human-readable
    string after each stage - discover.py uses this to print progress.

    Returns a summary dict:
        {"source_id": int, "scraped": int, "normalized": int,
         "normalize_errors": int, "criteria_label": str|None,
         "criteria_found": bool, "static": {"relevant": int, "rejected": int},
         "ai": {...}}  # "ai" is {"skipped": True} if AI didn't run
    """
    def report(message):
        if on_progress:
            on_progress(message)

    db.init_db()

    existing_source = db.get_source_by_url(url)
    if existing_source is not None:
        source_id = existing_source["id"]
        report(f"Reusing existing source (id {source_id}) for {url}")
    else:
        source_id = sources_module.add_source(url=url, name=name)
        report(f"Registered new source (id {source_id}) for {url}")

    inserted_raw_job_ids = scrape_source(source_id)
    report(f"Scraped {len(inserted_raw_job_ids)} new job posting(s)")

    pending = [row for row in db.get_raw_jobs() if row["status"] == "new"]
    normalized_count, error_count = 0, 0
    for row in pending:
        if normalize_raw_job(row["id"]) is not None:
            normalized_count += 1
        else:
            error_count += 1
    report(f"Normalized {normalized_count} job(s), {error_count} error(s)")

    static_counts = {"relevant": 0, "rejected": 0}
    ai_result = {"skipped": True}
    criteria_row = None

    if criteria_label:
        criteria_row = db.get_criteria_by_label(criteria_label)
        if criteria_row is None:
            report(f"No criteria profile named '{criteria_label}' found - skipping matching")
        else:
            static_counts = run_static_matching(criteria_row["id"])
            report(f"Static match: {static_counts['relevant']} relevant, "
                   f"{static_counts['rejected']} rejected")

            if use_ai:
                ai_result = ai_filter.run_ai_filtering(criteria_row["id"])
                if ai_result.get("skipped"):
                    report("AI filtering skipped (no AI_API_KEY configured or no ai_prompt set)")
                else:
                    report(f"AI filter: {ai_result['scored']} scored, "
                           f"{ai_result['flipped_to_rejected']} flipped to rejected, "
                           f"{ai_result['errors']} error(s)")
            else:
                report("AI filtering skipped (--no-ai)")
    else:
        report("No criteria label given - skipping static/AI matching")

    return {
        "source_id": source_id,
        "scraped": len(inserted_raw_job_ids),
        "normalized": normalized_count,
        "normalize_errors": error_count,
        "criteria_label": criteria_label,
        "criteria_found": criteria_row is not None,
        "static": static_counts,
        "ai": ai_result,
    }
