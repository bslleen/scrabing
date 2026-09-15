"""AI-assisted relevance re-ranking, layered on top of the static filter
(discovery.matcher). This is the one deliberate network/AI exception in
this project - everywhere else, no data leaves the user's machine except
page fetches. It's scoped tightly to stay fast, cheap, and fully
optional: the pipeline produces a complete, usable result from static
filtering alone (see discovery.matcher), whether or not this stage ever
runs.

Only jobs the static filter already marked 'relevant' for a given
criteria profile are sent to the LLM - never the full raw pool - which
bounds both token usage and cost. Each candidate is scored 0-100 against
the criteria's free-text ai_prompt, with a short reasoning string stored
alongside the score so the UI can show exactly why the AI agreed or
disagreed with the static filter. A score below AI_RELEVANCE_THRESHOLD
flips a static-'relevant' job to 'rejected'; at or above it, 'relevant'
stays as-is. This stage never promotes a static-'rejected' job - it only
narrows what the static filter already accepted.

If no API key is configured (or a criteria profile has no ai_prompt),
this logs a clear message and returns without error - callers never need
to check for a key before calling run_ai_filtering().

Requests run through a small thread pool (a handful of concurrent HTTP
calls) rather than one-by-one sequential blocking calls, since scoring is
I/O-bound. That's the extent of the "batching" here by design - the
OpenAI Batch API's async/24-hour-turnaround model is real overkill for a
stage meant to be fast and optional; correctness first.
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

import config
from discovery import db

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"

SYSTEM_PROMPT = (
    "You are a strict job-relevance screener. Given a candidate's search "
    "criteria and one job posting, decide how well the posting matches "
    "what the candidate is looking for. Respond with a JSON object only, "
    'shaped exactly like {"score": <integer 0-100>, "reasoning": '
    '"<one to two sentence explanation>"}. 0 means completely irrelevant, '
    "100 means a perfect match."
)


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _build_user_prompt(ai_prompt, job):
    lines = [f"Candidate's criteria: {ai_prompt}", ""]
    lines.append(f"Job title: {job['title'] or '(none)'}")
    if job["company"]:
        lines.append(f"Company: {job['company']}")
    if job["location"]:
        lines.append(f"Location: {job['location']}")
    lines.append(f"Description: {job['description'] or '(none)'}")
    if job["requirements"]:
        lines.append(f"Requirements: {job['requirements']}")
    return "\n".join(lines)


def _default_client(job, ai_prompt):
    """Calls the OpenAI Chat Completions API for one job. Returns
    (score, reasoning). Raises on any failure - the caller (_score_one_job)
    catches it so one bad call doesn't kill the whole batch.
    """
    headers = {
        "Authorization": f"Bearer {config.AI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.AI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(ai_prompt, job)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    response = requests.post(
        OPENAI_CHAT_COMPLETIONS_URL,
        headers=headers,
        json=payload,
        timeout=config.AI_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return int(parsed["score"]), str(parsed["reasoning"]).strip()


def _score_one_job(job, ai_prompt, client_fn):
    try:
        score, reasoning = client_fn(job, ai_prompt)
        score = max(0, min(100, score))  # clamp defensively against a misbehaving model
        return job["id"], score, reasoning, None
    except Exception as exc:  # noqa: BLE001 - one bad job must not kill the batch
        return job["id"], None, None, str(exc)


def run_ai_filtering(criteria_id, db_path=None, client_fn=None, max_workers=None):
    """Re-scores every static-'relevant' job_matches row for one criteria
    profile using an LLM, storing ai_score/ai_reasoning and possibly
    flipping status to 'rejected' when the AI disagrees strongly enough.

    client_fn(job, ai_prompt) -> (score, reasoning) is injectable for
    testing (no real network call needed); defaults to a real OpenAI call.

    Returns {"scored": n, "flipped_to_rejected": n, "errors": n}, or
    {"skipped": True} if there's no API key or no ai_prompt to use.
    """
    if not config.AI_API_KEY:
        print("[ai_filter] AI filtering skipped - no API key configured")
        return {"skipped": True}

    criteria_row = db.get_criteria(criteria_id, db_path=db_path)
    if criteria_row is None:
        print(f"[ai_filter] no criteria with id={criteria_id}")
        return {"scored": 0, "flipped_to_rejected": 0, "errors": 0}

    ai_prompt = criteria_row["ai_prompt"]
    if not ai_prompt:
        print(f"[ai_filter] criteria {criteria_id} has no ai_prompt set, skipping AI filtering")
        return {"skipped": True}

    client = client_fn or _default_client
    max_workers = config.AI_MAX_CONCURRENT_REQUESTS if max_workers is None else max_workers

    relevant_matches = [
        row for row in db.get_job_matches(db_path=db_path)
        if row["criteria_id"] == criteria_id and row["status"] == "relevant"
    ]
    print(f"[ai_filter] sending {len(relevant_matches)} static-relevant job(s) "
          f"to the AI for criteria {criteria_id}")

    jobs_by_match_id = {}
    for match in relevant_matches:
        job = db.get_job(match["job_id"], db_path=db_path)
        if job is not None:
            jobs_by_match_id[match["id"]] = job

    counts = {"scored": 0, "flipped_to_rejected": 0, "errors": 0}

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {
            executor.submit(_score_one_job, jobs_by_match_id[match["id"]], ai_prompt, client): match
            for match in relevant_matches
            if match["id"] in jobs_by_match_id
        }

        for future in as_completed(futures):
            match = futures[future]
            job_id, score, reasoning, error = future.result()

            if error is not None:
                counts["errors"] += 1
                print(f"[ai_filter] job {job_id}: AI scoring failed ({error}), leaving status unchanged")
                continue

            new_status = match["status"]
            if score < config.AI_RELEVANCE_THRESHOLD:
                new_status = "rejected"
                counts["flipped_to_rejected"] += 1
                print(f"[ai_filter] job {job_id}: AI score {score} < "
                      f"{config.AI_RELEVANCE_THRESHOLD}, flipping to rejected")
            else:
                print(f"[ai_filter] job {job_id}: AI score {score}, staying relevant")

            db.update_job_match(
                match["id"],
                db_path=db_path,
                ai_score=score,
                ai_reasoning=reasoning,
                status=new_status,
                matched_at=_utc_now_iso(),
            )
            counts["scored"] += 1

    print(f"[ai_filter] done: {counts['scored']} scored, "
          f"{counts['flipped_to_rejected']} flipped to rejected, {counts['errors']} error(s)")
    return counts
