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

# Static matcher weights (discovery/matcher.py) and the score at/above
# which a job is marked 'relevant' rather than 'rejected'.
KEYWORD_MATCH_WEIGHT = float(os.getenv("KEYWORD_MATCH_WEIGHT", "2"))
LOCATION_MATCH_WEIGHT = float(os.getenv("LOCATION_MATCH_WEIGHT", "3"))
SALARY_MATCH_WEIGHT = float(os.getenv("SALARY_MATCH_WEIGHT", "2"))
STATIC_MATCH_THRESHOLD = float(os.getenv("STATIC_MATCH_THRESHOLD", "3"))
