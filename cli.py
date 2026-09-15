"""Command-line entry point for job discovery."""
import argparse

from discovery import db, sources as sources_module
from discovery.crawler import discover_job_urls
from discovery.normalizer import normalize_raw_job
from discovery.scraper import scrape_source


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

    args = parser.parse_args(argv)

    if args.command in ("add-source", "sources", "scrape", "normalize"):
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


if __name__ == "__main__":
    main()
