#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example - edit it if you want non-default settings."
fi

echo "Setup complete. Run ./start.sh to launch."
