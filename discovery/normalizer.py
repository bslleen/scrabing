"""Turns a scraped raw_jobs row into the structured `jobs` schema: title,
company, location, description, requirements, salary range, and
employment type.

Same JSON-LD-first philosophy as discovery.scraper: prefer a page's
schema.org JobPosting for anything it states directly (title, company,
location, employment type, posted date), and fall back to text heuristics
only when the structured data doesn't have it - bilingual (English/German)
section-heading detection to split description from requirements,
keyword-based employment-type detection, and a currency-anchored regex for
salary ranges. Every heuristic leaves a field NULL rather than guessing
when confidence is low; nothing here fabricates a value it isn't
reasonably sure of.

Arbeitnow API-sourced rows skip all of that and map their already-
structured JSON fields directly - see _normalize_arbeitnow_job().

Standalone module: no imports from any other project.
"""
import json
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from discovery import db
from discovery.json_ld import find_job_posting

# Headings that mark the start of the requirements/qualifications section.
# Matched against a whole (trimmed, colon-stripped) line, so this expects
# raw_text where headings sit on their own line - true of both the JSON-LD
# description HTML and the heuristic-selector text discovery.scraper
# produces (both use get_text(separator="\n")).
REQUIREMENTS_HEADINGS = (
    "requirements", "qualifications", "your profile", "what you bring",
    "what we're looking for", "your skills", "what you'll need",
    "anforderungen", "ihr profil", "dein profil", "qualifikationen",
    "was du mitbringst", "was sie mitbringen", "voraussetzungen",
)

# Headings that mark the end of the requirements section (start of the
# next one), so trailing perks/benefits text isn't folded into it.
STOP_HEADINGS = (
    "benefits", "what we offer", "perks", "why join us", "about us",
    "wir bieten", "unser angebot", "deine vorteile", "über uns",
)

EMPLOYMENT_TYPE_KEYWORDS = (
    ("full_time", ("full-time", "full time", "vollzeit")),
    ("part_time", ("part-time", "part time", "teilzeit")),
    ("working_student", ("working student", "werkstudent")),
    ("internship", ("internship", "intern", "praktikum", "praktikant")),
    ("temporary", ("temporary", "befristet")),
    ("contract", ("freelance", "contractor", "contract position")),
)

CURRENCY_HINT = re.compile(r"€|\$|eur|usd|gehalt|salary|compensation|verg[uü]tung", re.IGNORECASE)
SALARY_RANGE_PATTERN = re.compile(
    r"(\d{1,3}(?:[.,]\d{3})*)\s*(k)?\s*(?:€|\$|eur|usd)?"
    r"\s*(?:-|–|to|bis)\s*"
    r"(?:€|\$|eur|usd)?\s*(\d{1,3}(?:[.,]\d{3})*)\s*(k)?",
    re.IGNORECASE,
)

LOCATION_LABEL_PATTERN = re.compile(r"^(?:location|standort|ort)\s*:\s*(.+)$", re.IGNORECASE)
COMPANY_LABEL_PATTERN = re.compile(r"^(?:company|unternehmen|arbeitgeber)\s*:\s*(.+)$", re.IGNORECASE)


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _job_posting_from_html(html):
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        job_posting = find_job_posting(data)
        if job_posting:
            return job_posting
    return None


def _extract_title(job_posting, html, raw_text):
    if job_posting and job_posting.get("title"):
        return str(job_posting["title"]).strip(), "json_ld"

    if html:
        heading = BeautifulSoup(html, "html.parser").find("h1")
        if heading and heading.get_text(strip=True):
            return heading.get_text(strip=True), "heading_h1"

    stripped = (raw_text or "").strip()
    if stripped:
        first_line = stripped.splitlines()[0].strip()
        if first_line:
            return first_line, "first_line"

    return None, None


def _extract_company(job_posting, raw_text):
    if job_posting:
        organization = job_posting.get("hiringOrganization")
        if isinstance(organization, dict) and organization.get("name"):
            return str(organization["name"]).strip(), "json_ld"
        if isinstance(organization, str) and organization.strip():
            return organization.strip(), "json_ld"

    for line in (raw_text or "").splitlines():
        match = COMPANY_LABEL_PATTERN.match(line.strip())
        if match:
            return match.group(1).strip(), "labeled_line"

    return None, None


def _location_from_json_ld(job_posting):
    location = job_posting.get("jobLocation")
    if isinstance(location, list):
        location = location[0] if location else None
    if not isinstance(location, dict):
        return None

    address = location.get("address")
    if isinstance(address, str) and address.strip():
        return address.strip()
    if isinstance(address, dict):
        city = address.get("addressLocality")
        country = address.get("addressCountry")
        parts = [str(part) for part in (city, country) if part]
        if parts:
            return ", ".join(parts)

    return None


def _extract_location(job_posting, raw_text):
    if job_posting:
        location = _location_from_json_ld(job_posting)
        if location:
            return location, "json_ld"

    for line in (raw_text or "").splitlines():
        match = LOCATION_LABEL_PATTERN.match(line.strip())
        if match:
            return match.group(1).strip(), "labeled_line"

    return None, None


def _extract_employment_type(job_posting, raw_text):
    if job_posting and job_posting.get("employmentType"):
        value = job_posting["employmentType"]
        value = value[0] if isinstance(value, list) and value else value
        if isinstance(value, str) and value.strip():
            # schema.org values look like "FULL_TIME" - normalize casing.
            return value.strip().lower().replace(" ", "_"), "json_ld"

    haystack = (raw_text or "").lower()
    for normalized, keywords in EMPLOYMENT_TYPE_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return normalized, "keyword"

    return None, None


def _parse_number(raw, is_thousands):
    cleaned = raw.replace(".", "").replace(",", "")
    value = int(cleaned)
    if is_thousands:
        value *= 1000
    return value


def _extract_salary_range(raw_text):
    for line in (raw_text or "").splitlines():
        if not CURRENCY_HINT.search(line):
            continue
        match = SALARY_RANGE_PATTERN.search(line)
        if not match:
            continue

        low_raw, low_k, high_raw, high_k = match.groups()
        low = _parse_number(low_raw, bool(low_k))
        high = _parse_number(high_raw, bool(high_k))
        if 0 < low <= high:
            return low, high, "regex_range"

    return None, None, None


def _find_section_split(lines, headings):
    for index, line in enumerate(lines):
        normalized = line.strip().strip(":").lower()
        if any(normalized == heading or normalized.startswith(heading) for heading in headings):
            return index
    return None


def _split_description_and_requirements(raw_text):
    if not raw_text or not raw_text.strip():
        return None, None

    lines = raw_text.splitlines()
    split_index = _find_section_split(lines, REQUIREMENTS_HEADINGS)
    if split_index is None:
        return raw_text.strip(), None

    description = "\n".join(lines[:split_index]).strip() or None

    requirements_lines = lines[split_index + 1:]
    stop_index = _find_section_split(requirements_lines, STOP_HEADINGS)
    if stop_index is not None:
        requirements_lines = requirements_lines[:stop_index]
    requirements = "\n".join(requirements_lines).strip() or None

    return description, requirements


def _normalize_via_heuristics(raw_job_id, raw_job, db_path=None):
    raw_html = raw_job["raw_html"]
    raw_text = raw_job["raw_text"] or ""

    job_posting = _job_posting_from_html(raw_html)

    title, title_source = _extract_title(job_posting, raw_html, raw_text)
    if not title:
        print(f"[normalizer] raw_job {raw_job_id}: could not extract even a title, marking error")
        db.update_raw_job_status(raw_job_id, "error", db_path=db_path)
        return None

    company, company_source = _extract_company(job_posting, raw_text)
    location, location_source = _extract_location(job_posting, raw_text)
    employment_type, employment_source = _extract_employment_type(job_posting, raw_text)
    salary_min, salary_max, salary_source = _extract_salary_range(raw_text)
    description, requirements = _split_description_and_requirements(raw_text)
    posted_at = job_posting.get("datePosted") if job_posting else None

    print(
        f"[normalizer] raw_job {raw_job_id}: title via {title_source}, "
        f"company via {company_source or 'not found'}, "
        f"location via {location_source or 'not found'}, "
        f"employment_type via {employment_source or 'not found'}, "
        f"salary via {salary_source or 'not found'}"
    )

    job_id = db.insert_job(
        raw_job_id=raw_job_id,
        title=title,
        company=company,
        location=location,
        description=description,
        requirements=requirements,
        salary_min=salary_min,
        salary_max=salary_max,
        employment_type=employment_type,
        posted_at=posted_at,
        normalized_at=_utc_now_iso(),
        db_path=db_path,
    )
    db.update_raw_job_status(raw_job_id, "normalized", db_path=db_path)
    return job_id


def _normalize_arbeitnow_job(raw_job_id, raw_job, db_path=None):
    """Direct field mapping for Arbeitnow API-sourced jobs (discovery.
    arbeitnow) - no JSON-LD lookup, no heuristics. The API already gives
    structured title/company/location/remote/job_types/created_at, so
    guessing at any of them the usual way would only make results less
    accurate, not more.
    """
    item = json.loads(raw_job["raw_json"])

    title = (item.get("title") or "").strip() or None
    if not title:
        print(f"[normalizer] raw_job {raw_job_id}: Arbeitnow item has no title, marking error")
        db.update_raw_job_status(raw_job_id, "error", db_path=db_path)
        return None

    company = (item.get("company_name") or "").strip() or None

    location = (item.get("location") or "").strip() or None
    if item.get("remote"):
        # Folded into location (there's no dedicated boolean column) so
        # discovery.matcher's existing "remote" location match still
        # picks these jobs up for free.
        location = f"{location} (Remote)" if location else "Remote"

    job_types = item.get("job_types") or []
    employment_type = ", ".join(job_types) if job_types else None

    posted_at = None
    created_at = item.get("created_at")
    if isinstance(created_at, (int, float)):
        posted_at = datetime.fromtimestamp(created_at, tz=timezone.utc).isoformat()

    print(
        f"[normalizer] raw_job {raw_job_id}: mapped directly from Arbeitnow API fields "
        f"(company={'yes' if company else 'no'}, location={'yes' if location else 'no'}, "
        f"employment_type={'yes' if employment_type else 'no'}, posted_at={'yes' if posted_at else 'no'})"
    )

    job_id = db.insert_job(
        raw_job_id=raw_job_id,
        title=title,
        company=company,
        location=location,
        description=raw_job["raw_text"] or None,
        requirements=None,  # not split out - see module docstring on skipping heuristics
        salary_min=None,    # not provided by this API - never guessed
        salary_max=None,
        employment_type=employment_type,
        posted_at=posted_at,
        normalized_at=_utc_now_iso(),
        db_path=db_path,
    )
    db.update_raw_job_status(raw_job_id, "normalized", db_path=db_path)
    return job_id


def normalize_raw_job(raw_job_id, db_path=None):
    """Extracts structured fields from a raw_jobs row and inserts a jobs
    row. Sets raw_jobs.status to 'normalized' on success, or 'error' if
    extraction fails badly (no usable title at all, or an unexpected
    exception) - the raw_jobs row is never deleted or silently left as
    'new' either way. Returns the new jobs.id, or None on failure.

    Arbeitnow API-sourced rows (raw_json populated, source type
    'arbeitnow_api') are mapped directly from their JSON fields instead
    of running the JSON-LD/heuristic pipeline below - see
    _normalize_arbeitnow_job().
    """
    raw_job = db.get_raw_job(raw_job_id, db_path=db_path)
    if raw_job is None:
        print(f"[normalizer] no raw_jobs row with id={raw_job_id}")
        return None

    try:
        source = db.get_source(raw_job["source_id"], db_path=db_path) if raw_job["source_id"] else None
        if source is not None and source["type"] == "arbeitnow_api" and raw_job["raw_json"]:
            return _normalize_arbeitnow_job(raw_job_id, raw_job, db_path=db_path)
        return _normalize_via_heuristics(raw_job_id, raw_job, db_path=db_path)

    except Exception as exc:  # noqa: BLE001 - a bad row must be recorded, never dropped
        print(f"[normalizer] raw_job {raw_job_id}: extraction failed ({exc!r}), marking error")
        db.update_raw_job_status(raw_job_id, "error", db_path=db_path)
        return None
