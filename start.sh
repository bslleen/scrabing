#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "No venv found - run ./setup.sh first."
    exit 1
fi
source venv/bin/activate

echo "Job Discovery"
echo "1) Discover job URLs from a site (no saving)"
echo "2) Add a source"
echo "3) List sources"
echo "4) Scrape a source"
echo "5) Normalize scraped jobs"
echo "6) Add a criteria profile"
echo "7) List criteria profiles"
echo "8) Match jobs against a criteria profile"
echo "q) Quit"
read -rp "> " choice

case "$choice" in
    1)
        read -rp "Careers page URL: " url
        python3 cli.py discover "$url"
        ;;
    2)
        read -rp "Careers page URL: " url
        read -rp "Name (optional): " name
        if [ -z "$name" ]; then
            python3 cli.py add-source "$url"
        else
            python3 cli.py add-source "$url" --name "$name"
        fi
        ;;
    3)
        python3 cli.py sources
        ;;
    4)
        read -rp "Source id: " source_id
        python3 cli.py scrape "$source_id"
        ;;
    5)
        python3 cli.py normalize
        ;;
    6)
        read -rp "Label: " label
        read -rp "Keywords (comma-separated, optional): " keywords
        read -rp "Exclude keywords (comma-separated, optional): " exclude_keywords
        read -rp "Locations (comma-separated, optional): " locations
        read -rp "Minimum salary (optional): " min_salary
        args=("$label")
        [ -n "$keywords" ] && args+=(--keywords "$keywords")
        [ -n "$exclude_keywords" ] && args+=(--exclude-keywords "$exclude_keywords")
        [ -n "$locations" ] && args+=(--locations "$locations")
        [ -n "$min_salary" ] && args+=(--min-salary "$min_salary")
        python3 cli.py add-criteria "${args[@]}"
        ;;
    7)
        python3 cli.py criteria
        ;;
    8)
        read -rp "Criteria id: " criteria_id
        python3 cli.py match "$criteria_id"
        ;;
    q)
        exit 0
        ;;
    *)
        echo "Unknown option."
        ;;
esac
