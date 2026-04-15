"""
Synthesizer module: takes collected items + project memory and calls Claude API
to produce a structured weekly digest.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import anthropic

from agent.collector import collect_all
from agent.prompts import SYNTHESIS_SYSTEM_PROMPT, SYNTHESIS_USER_PROMPT
from agent.recommendations import extract_recommendations

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MEMORY_PATH = os.path.join(os.path.dirname(__file__), "..", "memory", "projects.json")
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8000


def load_projects(path=None):
    """Load completed projects from memory/projects.json."""
    path = path or MEMORY_PATH
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("projects", [])
    except FileNotFoundError:
        logger.warning("projects.json not found at %s, using empty list", path)
        return []


def format_projects(projects):
    """Format projects list for the prompt."""
    if not projects:
        return "None yet."
    lines = []
    for p in projects:
        if p.get("completed"):
            tags = ", ".join(p.get("tags", []))
            lines.append(f"- {p['name']}: {p['description']} [Tags: {tags}]")
    return "\n".join(lines) if lines else "None yet."


def format_items(items):
    """Format collected items for the prompt."""
    if not items:
        return "No items collected this week."
    lines = []
    for item in items:
        line = (
            f"**{item.get('title', 'Untitled')}**\n"
            f"Source: {item.get('source_name', 'Unknown')} | "
            f"Date: {item.get('published_date', 'Unknown')} | "
            f"Type: {item.get('type', 'article')}\n"
            f"URL: {item.get('url', '')}\n"
            f"Snippet: {item.get('snippet', '')}\n"
        )
        lines.append(line)
    return "\n---\n".join(lines)


def synthesize(items, projects=None, model=None):
    """Call Claude API to produce the weekly digest.

    Returns a tuple (digest_markdown, recommendations_list). The markdown
    has the machine-readable recommendations block stripped out so it's
    safe to post directly to Slack.
    """
    if projects is None:
        projects = load_projects()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    user_prompt = SYNTHESIS_USER_PROMPT.format(
        date=today,
        formatted_items=format_items(items),
        projects_list=format_projects(projects),
    )

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model or MODEL,
        max_tokens=MAX_TOKENS,
        system=SYNTHESIS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = message.content[0].text
    digest, recommendations = extract_recommendations(raw)
    logger.info(
        "Digest generated (%d chars, %d recommendations)",
        len(digest), len(recommendations),
    )
    return digest, recommendations


def run_full_pipeline(fixture_path=None):
    """Run collector -> synthesizer pipeline. Optionally use fixture data.

    Returns (digest_markdown, recommendations_list) or None if no items.
    """
    if fixture_path:
        with open(fixture_path) as f:
            items = json.load(f)
        logger.info("Loaded %d items from fixture %s", len(items), fixture_path)
    else:
        items = collect_all()

    if not items:
        logger.warning("No items collected. Skipping synthesis.")
        return None

    return synthesize(items)


def main():
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Synthesize weekly GTM/MOPS digest")
    parser.add_argument(
        "--fixture", type=str,
        help="Path to a JSON fixture file to use instead of live scraping"
    )
    parser.add_argument(
        "--output", type=str,
        help="Write digest to a file instead of stdout"
    )
    args = parser.parse_args()

    result = run_full_pipeline(fixture_path=args.fixture)
    if result is None:
        print("No content to synthesize.")
        sys.exit(1)

    digest, _recommendations = result
    if args.output:
        with open(args.output, "w") as f:
            f.write(digest)
        logger.info("Digest written to %s", args.output)
    else:
        print(digest)


if __name__ == "__main__":
    main()
