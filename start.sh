#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "No venv found - run ./setup.sh first."
    exit 1
fi
source venv/bin/activate

echo "Job Discovery"
echo "1) Run full pipeline on a URL (discover -> scrape -> normalize -> match -> AI filter)"
echo "2) Open web dashboard"
echo "3) Add a criteria profile"
echo "4) List criteria profiles"
echo "5) Discover job URLs from a site (no saving)"
echo "6) Add a source"
echo "7) List sources"
echo "8) Scrape a source"
echo "9) Normalize scraped jobs"
echo "10) Match jobs against a criteria profile (static rules)"
echo "11) Re-score with AI (skipped if no AI_API_KEY is set)"
echo "q) Quit"
read -rp "> " choice

case "$choice" in
    1)
        read -rp "Website URL: " url
        read -rp "Criteria label (optional, e.g. 'default'): " criteria_label
        read -rp "Skip AI filtering? [y/N]: " skip_ai
        args=("$url")
        [ -n "$criteria_label" ] && args+=(--criteria "$criteria_label")
        if [[ "$skip_ai" =~ ^[Yy]$ ]]; then
            args+=(--no-ai)
        fi
        python3 discover.py "${args[@]}"
        ;;
    2)
        echo "Starting web UI - open http://127.0.0.1:5000 in your browser (Ctrl+C to stop)"
        echo "(On macOS, port 5000 is sometimes taken by AirPlay Receiver - set WEBUI_PORT in .env if it won't start.)"
        python3 -m webui.app
        ;;
    3)
        read -rp "Label: " label
        read -rp "Keywords (comma-separated, optional): " keywords
        read -rp "Exclude keywords (comma-separated, optional): " exclude_keywords
        read -rp "Locations (comma-separated, optional): " locations
        read -rp "Minimum salary (optional): " min_salary
        read -rp "AI prompt - free text, used only by AI re-scoring (optional): " ai_prompt
        args=("$label")
        [ -n "$keywords" ] && args+=(--keywords "$keywords")
        [ -n "$exclude_keywords" ] && args+=(--exclude-keywords "$exclude_keywords")
        [ -n "$locations" ] && args+=(--locations "$locations")
        [ -n "$min_salary" ] && args+=(--min-salary "$min_salary")
        [ -n "$ai_prompt" ] && args+=(--ai-prompt "$ai_prompt")
        python3 cli.py add-criteria "${args[@]}"
        ;;
    4)
        python3 cli.py criteria
        ;;
    5)
        read -rp "Careers page URL: " url
        python3 cli.py discover "$url"
        ;;
    6)
        read -rp "Careers page URL: " url
        read -rp "Name (optional): " name
        if [ -z "$name" ]; then
            python3 cli.py add-source "$url"
        else
            python3 cli.py add-source "$url" --name "$name"
        fi
        ;;
    7)
        python3 cli.py sources
        ;;
    8)
        read -rp "Source id: " source_id
        python3 cli.py scrape "$source_id"
        ;;
    9)
        python3 cli.py normalize
        ;;
    10)
        read -rp "Criteria id: " criteria_id
        python3 cli.py match "$criteria_id"
        ;;
    11)
        read -rp "Criteria id: " criteria_id
        python3 cli.py ai-match "$criteria_id"
        ;;
    q)
        exit 0
        ;;
    *)
        echo "Unknown option."
        ;;
esac
