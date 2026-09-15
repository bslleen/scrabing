"""Command-line entry point for job discovery."""
import argparse

from discovery.crawler import discover_job_urls


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="jobdiscovery",
        description="Discover job posting URLs on a careers site.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    discover_cmd = subcommands.add_parser("discover", help="Crawl a site and discover job posting URLs")
    discover_cmd.add_argument("url", help="Careers page or job board URL to start from")
    discover_cmd.add_argument("--max-pages", type=int, default=None, help="Max pages to follow via pagination")

    args = parser.parse_args(argv)

    if args.command == "discover":
        _run_discover(args)


def _run_discover(args):
    source = {"url": args.url}
    urls = discover_job_urls(source, max_pages=args.max_pages)

    print(f"\nDiscovered {len(urls)} candidate job URL(s):")
    for url in urls:
        print(f"  {url}")
    print("\n(Not saved yet - wiring discovery output into the sources/raw_jobs "
          "tables lands in a later phase.)")


if __name__ == "__main__":
    main()
