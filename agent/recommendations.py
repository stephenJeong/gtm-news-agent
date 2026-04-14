"""
Recommendations store: persists weekly Build Recommendations from the digest
so they can be queried and cross-referenced against completed projects.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STORE_PATH = os.path.join(os.path.dirname(__file__), "..", "memory", "recommendations.json")

RECOMMENDATIONS_BLOCK_RE = re.compile(
    r"<recommendations_json>(.*?)</recommendations_json>", re.DOTALL
)


def extract_recommendations(raw_text):
    """Pull structured recommendations out of the Claude output.

    Returns (clean_markdown, recommendations_list). If the block is missing
    or unparseable, returns (raw_text, []).
    """
    match = RECOMMENDATIONS_BLOCK_RE.search(raw_text)
    if not match:
        logger.warning("No <recommendations_json> block found in digest output")
        return raw_text, []

    clean = RECOMMENDATIONS_BLOCK_RE.sub("", raw_text).rstrip() + "\n"
    try:
        recs = json.loads(match.group(1).strip())
        if not isinstance(recs, list):
            raise ValueError("recommendations block was not a JSON list")
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse recommendations JSON: %s", e)
        return clean, []

    return clean, recs


def load_store(path=None):
    path = path or STORE_PATH
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"last_updated": "", "recommendations": []}


def save_store(data, path=None):
    path = path or STORE_PATH
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def append_week(recommendations, week_of=None, path=None):
    """Append a week's worth of recommendations to the store.

    Each rec gets an id like `2026-04-14_1`, a status of `recommended`, and
    a `recommended_on` date. Returns the list of new entries (with ids).
    """
    if not recommendations:
        return []

    week_of = week_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    store = load_store(path)

    new_entries = []
    for i, rec in enumerate(recommendations, start=1):
        entry = {
            "id": f"{week_of}_{i}",
            "recommended_on": week_of,
            "title": rec.get("title", ""),
            "trend_signal": rec.get("trend_signal", ""),
            "what_to_build": rec.get("what_to_build", ""),
            "why_now": rec.get("why_now", ""),
            "complexity": rec.get("complexity", ""),
            "inferred": bool(rec.get("inferred", False)),
            "status": "recommended",
            "built_project_id": None,
        }
        new_entries.append(entry)

    store["recommendations"].extend(new_entries)
    store["last_updated"] = week_of
    save_store(store, path)
    return new_entries


def mark_built(rec_id, project_id, path=None):
    """Flip a recommendation's status to `built` and link it to a project."""
    store = load_store(path)
    for rec in store["recommendations"]:
        if rec["id"] == rec_id:
            rec["status"] = "built"
            rec["built_project_id"] = project_id
            save_store(store, path)
            return True
    return False


def format_history(store_or_path=None):
    """Format the full recommendation history for prompt injection."""
    if isinstance(store_or_path, dict):
        store = store_or_path
    else:
        store = load_store(store_or_path)

    recs = store.get("recommendations", [])
    if not recs:
        return "None yet."

    lines = []
    for rec in recs:
        status = rec.get("status", "recommended")
        marker = "[BUILT]" if status == "built" else "[OPEN]"
        link = f" -> {rec['built_project_id']}" if rec.get("built_project_id") else ""
        lines.append(
            f"- {marker} {rec['id']} ({rec.get('complexity', '?')}): "
            f"{rec['title']}{link}"
        )
    return "\n".join(lines)
