#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "No venv found - run ./setup.sh first."
    exit 1
fi
source venv/bin/activate

echo "Job Discovery"
echo "1) Discover job URLs from a site"
echo "q) Quit"
read -rp "> " choice

case "$choice" in
    1)
        read -rp "Careers page URL: " url
        python3 cli.py discover "$url"
        ;;
    q)
        exit 0
        ;;
    *)
        echo "Unknown option."
        ;;
esac
