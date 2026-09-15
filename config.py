"""Reads configuration from environment variables (.env), with safe defaults
for everything so an empty or missing .env never breaks a run.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = os.getenv("DB_PATH", str(DATA_DIR / "jobs.db"))

USER_AGENT = os.getenv(
    "USER_AGENT",
    "JobDiscoveryBot/0.1 (+contact: not-set)",
)

REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "10"))
CRAWL_DELAY_SECONDS = float(os.getenv("CRAWL_DELAY_SECONDS", "1"))
MAX_PAGES_PER_SITE = int(os.getenv("MAX_PAGES_PER_SITE", "20"))
