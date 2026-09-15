#!/usr/bin/env python3
"""One-command pipeline: discover -> scrape -> normalize -> static match
-> AI filter (optional), for a single website URL.

    python3 discover.py "https://example-company.com/careers" --criteria default

Runs discovery.pipeline.run_pipeline() and prints a progress line after
each stage, then a summary. Every stage below discovery/scraping is
skippable: give no --criteria and matching is skipped entirely; pass
--no-ai and the AI stage is skipped even if AI_API_KEY is configured.
"""
import argparse

from discovery.pipeline import run_pipeline


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="discover.py",
        description="Run the full job-discovery pipeline against one website URL.",
    )
    parser.add_argument("url", help="Careers page or job board URL")
    parser.add_argument(
        "--criteria", default=None, metavar="LABEL",
        help="Label of a saved criteria profile to match against (see `python3 cli.py criteria`)",
    )
    parser.add_argument("--name", default=None, help="A friendly name for this source")
    parser.add_argument(
        "--no-ai", action="store_true",
        help="Skip AI filtering even if AI_API_KEY is configured",
    )
    args = parser.parse_args(argv)

    print(f"==> {args.url}")
    summary = run_pipeline(
        args.url,
        criteria_label=args.criteria,
        use_ai=not args.no_ai,
        name=args.name,
        on_progress=lambda message: print(f"    {message}"),
    )

    print("\nSummary:")
    print(f"  scraped:    {summary['scraped']} new")
    print(f"  normalized: {summary['normalized']} ({summary['normalize_errors']} error(s))")
    if summary["criteria_found"]:
        print(f"  static:     {summary['static']['relevant']} relevant, "
              f"{summary['static']['rejected']} rejected")
        ai = summary["ai"]
        if not ai.get("skipped"):
            print(f"  AI filter:  {ai['scored']} scored, {ai['flipped_to_rejected']} flipped to rejected, "
                  f"{ai['errors']} error(s)")
    elif args.criteria:
        print(f"  matching:   skipped - no criteria profile named '{args.criteria}'")
    else:
        print("  matching:   skipped - no --criteria given")

    print("\nBrowse the results: python3 -m webui.app")


if __name__ == "__main__":
    main()
