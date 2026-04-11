"""
Collector module: fetches content from RSS feeds, websites, and LinkedIn (via Apify).
All sources are driven by config/sources.json.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import feedparser
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "sources.json")
FIXTURE_OUTPUT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "tests", "fixtures", "sample_collected.json"
)


def load_sources(path=None):
    path = path or CONFIG_PATH
    with open(path) as f:
        return json.load(f)


def _cutoff_date(days=7):
    return datetime.now(timezone.utc) - timedelta(days=days)


# ---------------------------------------------------------------------------
# Substack / RSS
# ---------------------------------------------------------------------------

def _parse_rss_date(entry):
    """Return a timezone-aware datetime from a feedparser entry, or None."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        from calendar import timegm
        return datetime.fromtimestamp(timegm(entry.published_parsed), tz=timezone.utc)
    return None


def _fetch_snippet_from_url(url, chars=500):
    """Fallback: fetch the article page and grab the first N chars of body text."""
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "GTMNewsAgent/1.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return text[:chars]
    except Exception as e:
        logger.warning("Failed to fetch snippet from %s: %s", url, e)
        return ""


def collect_rss(source, cutoff=None):
    """Collect articles from a Substack (or any) RSS feed published after cutoff."""
    cutoff = cutoff or _cutoff_date()
    items = []

    feed = feedparser.parse(source["url"])
    if feed.bozo and not feed.entries:
        logger.warning("Feed error for %s: %s", source["id"], feed.bozo_exception)
        return items

    for entry in feed.entries:
        pub_date = _parse_rss_date(entry)
        if pub_date and pub_date < cutoff:
            continue

        snippet = getattr(entry, "summary", "") or ""
        if not snippet and hasattr(entry, "link"):
            snippet = _fetch_snippet_from_url(entry.link)

        items.append({
            "source_id": source["id"],
            "source_name": source["name"],
            "title": getattr(entry, "title", ""),
            "url": getattr(entry, "link", ""),
            "published_date": pub_date.strftime("%Y-%m-%d") if pub_date else "",
            "snippet": snippet[:500],
            "type": "article",
        })

    logger.info("Collected %d items from RSS source %s", len(items), source["id"])
    return items


# ---------------------------------------------------------------------------
# Website scraping
# ---------------------------------------------------------------------------

def _check_robots(base_url):
    """Return True if we're allowed to fetch the base URL per robots.txt."""
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    try:
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch("GTMNewsAgent", base_url)
    except Exception:
        return True  # If we can't read robots.txt, proceed cautiously


def _extract_date(article_tag):
    """Try to pull a publication date from a <time> tag or meta content."""
    time_tag = article_tag.find("time")
    if time_tag:
        dt_str = time_tag.get("datetime") or time_tag.get_text(strip=True)
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass
    return None


def collect_website(source, cutoff=None):
    """Scrape a website for articles using the configured selector."""
    cutoff = cutoff or _cutoff_date()
    items = []

    if not _check_robots(source["url"]):
        logger.warning("robots.txt disallows scraping %s, skipping", source["url"])
        return items

    try:
        resp = requests.get(
            source["url"], timeout=15, headers={"User-Agent": "GTMNewsAgent/1.0"}
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch %s: %s", source["url"], e)
        return items

    soup = BeautifulSoup(resp.text, "html.parser")
    selector = source.get("article_selector", "article")
    articles = soup.select(selector)
    filter_keywords = [kw.lower() for kw in source.get("filter_keywords", [])]

    for article in articles:
        headline_tag = article.find(["h1", "h2", "h3", "a"])
        headline = headline_tag.get_text(strip=True) if headline_tag else ""

        link_tag = article.find("a", href=True)
        article_url = ""
        if link_tag:
            article_url = urljoin(source["url"], link_tag["href"])

        pub_date = _extract_date(article)

        for tag in article(["script", "style"]):
            tag.decompose()
        body_text = article.get_text(separator=" ", strip=True)[:300]

        if filter_keywords:
            combined = (headline + " " + body_text).lower()
            if not any(kw in combined for kw in filter_keywords):
                continue

        items.append({
            "source_id": source["id"],
            "source_name": source["name"],
            "title": headline,
            "url": article_url,
            "published_date": pub_date.strftime("%Y-%m-%d") if pub_date else "",
            "snippet": body_text,
            "type": "article",
        })

    logger.info("Collected %d items from website %s", len(items), source["id"])
    return items


# ---------------------------------------------------------------------------
# LinkedIn via Apify (stub until account is ready)
# ---------------------------------------------------------------------------

def collect_linkedin(source, cutoff=None):
    """Collect LinkedIn posts via Apify actor. Requires APIFY_API_TOKEN env var."""
    cutoff = cutoff or _cutoff_date()
    token = os.environ.get("APIFY_API_TOKEN")
    if not token:
        logger.info("APIFY_API_TOKEN not set, skipping LinkedIn source %s", source["id"])
        return []

    try:
        from apify_client import ApifyClient
    except ImportError:
        logger.warning("apify-client not installed, skipping LinkedIn source %s", source["id"])
        return []

    client = ApifyClient(token)
    actor = source.get("apify_actor", "apify/linkedin-company-posts-scraper")

    try:
        run = client.actor(actor).call(
            run_input={"urls": [source["linkedin_url"]], "maxResults": 20}
        )
    except Exception as e:
        logger.warning("Apify actor failed for %s (quota exceeded?): %s", source["id"], e)
        return []

    items = []
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        post_text = (item.get("text") or "")[:500]
        post_date_str = item.get("postedAt") or item.get("date") or ""
        post_url = item.get("url") or ""

        try:
            post_date = datetime.fromisoformat(post_date_str.replace("Z", "+00:00"))
            if post_date < cutoff:
                continue
            date_formatted = post_date.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            date_formatted = ""

        items.append({
            "source_id": source["id"],
            "source_name": source["name"],
            "title": post_text[:80],
            "url": post_url,
            "published_date": date_formatted,
            "snippet": post_text,
            "type": "post",
        })

    logger.info("Collected %d items from LinkedIn source %s", len(items), source["id"])
    return items


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

COLLECTOR_MAP = {
    "substack_rss": collect_rss,
    "website": collect_website,
    "linkedin_apify": collect_linkedin,
}


def deduplicate(items):
    """Deduplicate by URL. Keep the item with the longer snippet; merge source names."""
    by_url = {}
    for item in items:
        url = item.get("url")
        if not url:
            by_url[id(item)] = item
            continue
        if url in by_url:
            existing = by_url[url]
            if len(item.get("snippet", "")) > len(existing.get("snippet", "")):
                merged_name = f"{existing['source_name']}, {item['source_name']}"
                item["source_name"] = merged_name
                by_url[url] = item
            else:
                merged_name = f"{existing['source_name']}, {item['source_name']}"
                existing["source_name"] = merged_name
        else:
            by_url[url] = item
    return list(by_url.values())


def collect_all(sources=None, dry_run=False):
    """Run all collectors and return deduplicated items."""
    sources = sources or load_sources()
    all_items = []

    for source in sources:
        source_type = source.get("type")
        collector_fn = COLLECTOR_MAP.get(source_type)
        if not collector_fn:
            logger.warning("Unknown source type '%s' for %s", source_type, source["id"])
            continue
        try:
            items = collector_fn(source)
            all_items.extend(items)
        except Exception as e:
            logger.error("Collector failed for %s: %s", source["id"], e)

    deduped = deduplicate(all_items)
    logger.info("Total collected: %d items (%d after dedup)", len(all_items), len(deduped))

    if dry_run:
        print(json.dumps(deduped, indent=2))

    return deduped


def main():
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Collect GTM/MOPS content from configured sources")
    parser.add_argument("--dry-run", action="store_true", help="Print collected items as JSON without further processing")
    parser.add_argument("--output", type=str, help="Write collected items to a JSON file")
    args = parser.parse_args()

    items = collect_all(dry_run=args.dry_run)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(items, f, indent=2)
        logger.info("Wrote %d items to %s", len(items), args.output)


if __name__ == "__main__":
    main()
