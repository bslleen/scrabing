"""Command-line entry point for job discovery."""
import argparse

from discovery import criteria as criteria_module
from discovery import db, sources as sources_module
from discovery.ai_filter import run_ai_filtering
from discovery.crawler import discover_job_urls
from discovery.matcher import run_static_matching
from discovery.normalizer import normalize_raw_job
from discovery.scraper import scrape_source


def _split_list(value):
    return [item.strip() for item in value.split(",") if item.strip()] if value else []


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="jobdiscovery",
        description="Discover and scrape job posting URLs on a careers site.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    discover_cmd = subcommands.add_parser("discover", help="Crawl a site and print discovered job URLs (no saving)")
    discover_cmd.add_argument("url", help="Careers page or job board URL to start from")
    discover_cmd.add_argument("--max-pages", type=int, default=None, help="Max pages to follow via pagination")

    add_source_cmd = subcommands.add_parser("add-source", help="Register a site as a source")
    add_source_cmd.add_argument("url", help="Careers page or job board URL")
    add_source_cmd.add_argument("--name", default=None, help="A friendly name for this source")

    subcommands.add_parser("sources", help="List registered sources")

    scrape_cmd = subcommands.add_parser("scrape", help="Discover and scrape all job postings for a source")
    scrape_cmd.add_argument("source_id", type=int, help="Id of the source to scrape (see `sources`)")

    normalize_cmd = subcommands.add_parser("normalize", help="Normalize scraped raw_jobs into the jobs table")
    normalize_cmd.add_argument(
        "raw_job_id", type=int, nargs="?", default=None,
        help="Normalize just this raw_jobs id; omit to normalize every row with status='new'",
    )

    add_criteria_cmd = subcommands.add_parser("add-criteria", help="Create a criteria profile")
    add_criteria_cmd.add_argument("label", help="A name for this profile, e.g. 'backend-berlin'")
    add_criteria_cmd.add_argument("--keywords", default="", help="Comma-separated keywords to look for")
    add_criteria_cmd.add_argument("--exclude-keywords", default="", help="Comma-separated keywords to reject on")
    add_criteria_cmd.add_argument("--locations", default="", help="Comma-separated acceptable locations")
    add_criteria_cmd.add_argument("--min-salary", type=int, default=None, help="Minimum acceptable salary")
    add_criteria_cmd.add_argument(
        "--ai-prompt", default=None,
        help="Free-text description of what you're looking for, used by `ai-match`",
    )

    subcommands.add_parser("criteria", help="List saved criteria profiles")

    match_cmd = subcommands.add_parser("match", help="Score all jobs against a criteria profile (static rules only)")
    match_cmd.add_argument("criteria_id", type=int, help="Id of the criteria profile (see `criteria`)")

    ai_match_cmd = subcommands.add_parser(
        "ai-match", help="Re-score static-relevant jobs with AI (skipped if no AI_API_KEY is set)",
    )
    ai_match_cmd.add_argument("criteria_id", type=int, help="Id of the criteria profile (see `criteria`)")

    args = parser.parse_args(argv)

    if args.command in (
        "add-source", "sources", "scrape", "normalize",
        "add-criteria", "criteria", "match", "ai-match",
    ):
        db.init_db()

    if args.command == "discover":
        _run_discover(args)
    elif args.command == "add-source":
        _run_add_source(args)
    elif args.command == "sources":
        _run_list_sources()
    elif args.command == "scrape":
        _run_scrape(args)
    elif args.command == "normalize":
        _run_normalize(args)
    elif args.command == "add-criteria":
        _run_add_criteria(args)
    elif args.command == "criteria":
        _run_list_criteria()
    elif args.command == "match":
        _run_match(args)
    elif args.command == "ai-match":
        _run_ai_match(args)


def _run_discover(args):
    source = {"url": args.url}
    urls = discover_job_urls(source, max_pages=args.max_pages)

    print(f"\nDiscovered {len(urls)} candidate job URL(s):")
    for url in urls:
        print(f"  {url}")
    print("\n(Not saved - use `add-source` + `scrape` to persist results.)")


def _run_add_source(args):
    source_id = sources_module.add_source(url=args.url, name=args.name)
    print(f"Added source {source_id}: {args.url}")


def _run_list_sources():
    rows = sources_module.list_sources()
    if not rows:
        print("No sources registered yet. Use `add-source <url>`.")
        return

    for row in rows:
        label = row["name"] or row["url"]
        print(f"  [{row['id']}] {label}  ({row['url']})  last_scraped_at={row['last_scraped_at']}")


def _run_scrape(args):
    inserted_ids = scrape_source(args.source_id)
    print(f"\nInserted {len(inserted_ids)} new raw_jobs row(s) for source {args.source_id}")


def _run_normalize(args):
    if args.raw_job_id is not None:
        job_id = normalize_raw_job(args.raw_job_id)
        if job_id is not None:
            print(f"Normalized raw_job {args.raw_job_id} -> job {job_id}")
        return

    pending = [row for row in db.get_raw_jobs() if row["status"] == "new"]
    print(f"Normalizing {len(pending)} raw_jobs row(s) with status='new'...")

    normalized, errored = 0, 0
    for row in pending:
        if normalize_raw_job(row["id"]) is not None:
            normalized += 1
        else:
            errored += 1

    print(f"\nDone: {normalized} normalized, {errored} error(s)")


def _run_add_criteria(args):
    criteria_id = criteria_module.add_criteria(
        label=args.label,
        keywords=_split_list(args.keywords),
        exclude_keywords=_split_list(args.exclude_keywords),
        locations=_split_list(args.locations),
        min_salary=args.min_salary,
        ai_prompt=args.ai_prompt,
    )
    print(f"Added criteria {criteria_id}: {args.label}")


def _run_list_criteria():
    rows = criteria_module.list_criteria()
    if not rows:
        print("No criteria profiles yet. Use `add-criteria <label>`.")
        return

    for row in rows:
        print(f"  [{row['id']}] {row['label']}  keywords={row['keywords']}  min_salary={row['min_salary']}")


def _run_match(args):
    counts = run_static_matching(args.criteria_id)
    print(f"\n{counts['relevant']} relevant, {counts['rejected']} rejected")


def _run_ai_match(args):
    result = run_ai_filtering(args.criteria_id)
    if result.get("skipped"):
        return
    print(f"\n{result['scored']} scored, {result['flipped_to_rejected']} flipped to rejected, "
          f"{result['errors']} error(s)")


if __name__ == "__main__":
    main()
