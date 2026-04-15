"""Tests for agent.collector module."""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent.collector import (
    _resolve_google_news_url,
    collect_all,
    collect_linkedin,
    collect_rss,
    collect_website,
    deduplicate,
    load_sources,
)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rss_xml(entries):
    """Build a minimal RSS XML string for testing."""
    items_xml = ""
    for e in entries:
        items_xml += f"""
        <item>
            <title>{e['title']}</title>
            <link>{e['url']}</link>
            <pubDate>{e['pub_date']}</pubDate>
            <description>{e.get('summary', '')}</description>
        </item>"""
    return f"""<?xml version="1.0"?>
    <rss version="2.0">
        <channel>
            <title>Test Feed</title>
            {items_xml}
        </channel>
    </rss>"""


def _rss_date(days_ago=0):
    """Return an RFC 822 date string N days ago."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


# ---------------------------------------------------------------------------
# RSS collector tests
# ---------------------------------------------------------------------------

class TestCollectRSS:
    def test_collects_recent_entries(self):
        import feedparser as fp
        source = {"id": "test_rss", "name": "Test RSS", "type": "substack_rss", "url": "http://example.com/feed"}
        xml = _make_rss_xml([
            {"title": "Recent Post", "url": "http://example.com/1", "pub_date": _rss_date(1), "summary": "A recent summary"},
            {"title": "Old Post", "url": "http://example.com/2", "pub_date": _rss_date(14), "summary": "An old summary"},
        ])
        parsed = fp.parse(xml)
        with patch("agent.collector.feedparser.parse", return_value=parsed):
            items = collect_rss(source)

        assert len(items) == 1
        assert items[0]["title"] == "Recent Post"
        assert items[0]["source_id"] == "test_rss"
        assert items[0]["type"] == "article"

    def test_fetches_snippet_when_no_summary(self):
        import feedparser as fp
        source = {"id": "test_rss", "name": "Test RSS", "type": "substack_rss", "url": "http://example.com/feed"}
        xml = _make_rss_xml([
            {"title": "No Summary", "url": "http://example.com/1", "pub_date": _rss_date(1), "summary": ""},
        ])
        parsed = fp.parse(xml)
        with patch("agent.collector.feedparser.parse", return_value=parsed), \
             patch("agent.collector._fetch_snippet_from_url", return_value="Fallback text"):
            items = collect_rss(source)

        assert len(items) == 1
        assert items[0]["snippet"] == "Fallback text"

    def test_empty_feed_returns_empty(self):
        import feedparser as fp
        source = {"id": "empty", "name": "Empty", "type": "substack_rss", "url": "http://example.com/feed"}
        xml = _make_rss_xml([])
        parsed = fp.parse(xml)
        with patch("agent.collector.feedparser.parse", return_value=parsed):
            items = collect_rss(source)

        assert items == []


# ---------------------------------------------------------------------------
# Website scraper tests
# ---------------------------------------------------------------------------

SAMPLE_HTML = """
<html><body>
<article>
    <h2><a href="/post/ai-lead-scoring">AI Lead Scoring Trends</a></h2>
    <time datetime="{}">Today</time>
    <p>Marketers are using AI to improve lead scoring models across their GTM stack.</p>
</article>
<article>
    <h2><a href="/post/unrelated">Cooking Tips</a></h2>
    <time datetime="{}">Today</time>
    <p>Here are some cooking tips for the weekend.</p>
</article>
</body></html>
"""


class TestCollectWebsite:
    def test_collects_articles_with_keyword_filter(self):
        now_str = datetime.now(timezone.utc).isoformat()
        html = SAMPLE_HTML.format(now_str, now_str)
        source = {
            "id": "test_site",
            "name": "Test Site",
            "type": "website",
            "url": "http://example.com/",
            "article_selector": "article",
            "filter_keywords": ["AI", "lead"],
        }
        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()

        with patch("agent.collector.requests.get", return_value=mock_resp), \
             patch("agent.collector._check_robots", return_value=True):
            items = collect_website(source)

        assert len(items) == 1
        assert "AI Lead Scoring" in items[0]["title"]

    def test_collects_all_articles_without_filter(self):
        now_str = datetime.now(timezone.utc).isoformat()
        html = SAMPLE_HTML.format(now_str, now_str)
        source = {
            "id": "test_site",
            "name": "Test Site",
            "type": "website",
            "url": "http://example.com/",
            "article_selector": "article",
        }
        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()

        with patch("agent.collector.requests.get", return_value=mock_resp), \
             patch("agent.collector._check_robots", return_value=True):
            items = collect_website(source)

        assert len(items) == 2

    def test_respects_robots_txt(self):
        source = {
            "id": "blocked",
            "name": "Blocked",
            "type": "website",
            "url": "http://example.com/",
            "article_selector": "article",
        }
        with patch("agent.collector._check_robots", return_value=False):
            items = collect_website(source)

        assert items == []


# ---------------------------------------------------------------------------
# LinkedIn collector tests
# ---------------------------------------------------------------------------

class TestCollectLinkedIn:
    def test_skips_when_no_token(self):
        source = {"id": "li", "name": "LI", "type": "linkedin_apify", "linkedin_url": "http://linkedin.com/company/test"}
        with patch.dict(os.environ, {}, clear=True):
            items = collect_linkedin(source)
        assert items == []

    def test_handles_apify_failure_gracefully(self):
        source = {
            "id": "li",
            "name": "LI",
            "type": "linkedin_apify",
            "apify_actor": "apify/linkedin-company-posts-scraper",
            "linkedin_url": "http://linkedin.com/company/test",
        }
        mock_client = MagicMock()
        mock_client.actor.return_value.call.side_effect = Exception("Quota exceeded")

        with patch.dict(os.environ, {"APIFY_API_TOKEN": "fake"}), \
             patch("apify_client.ApifyClient", return_value=mock_client):
            items = collect_linkedin(source)

        assert items == []


# ---------------------------------------------------------------------------
# Deduplication tests
# ---------------------------------------------------------------------------

class TestDeduplicate:
    def test_deduplicates_by_url_keeps_longer_snippet(self):
        items = [
            {"url": "http://example.com/1", "source_name": "Source A", "snippet": "short"},
            {"url": "http://example.com/1", "source_name": "Source B", "snippet": "a much longer snippet here"},
        ]
        result = deduplicate(items)
        assert len(result) == 1
        assert "longer" in result[0]["snippet"]
        assert "Source A" in result[0]["source_name"]
        assert "Source B" in result[0]["source_name"]

    def test_keeps_items_with_different_urls(self):
        items = [
            {"url": "http://example.com/1", "source_name": "A", "snippet": "x"},
            {"url": "http://example.com/2", "source_name": "B", "snippet": "y"},
        ]
        result = deduplicate(items)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Integration: collect_all
# ---------------------------------------------------------------------------

class TestCollectAll:
    def test_collect_all_dispatches_by_type(self):
        sources = [
            {"id": "rss1", "name": "RSS", "type": "substack_rss", "url": "http://example.com/feed"},
        ]
        mock_return = [{"url": "http://example.com/1", "source_name": "RSS", "snippet": "hi"}]
        with patch.dict("agent.collector.COLLECTOR_MAP", {"substack_rss": MagicMock(return_value=mock_return)}) as patched:
            items = collect_all(sources)
            assert len(items) == 1

    def test_collect_all_skips_unknown_type(self):
        sources = [{"id": "x", "name": "X", "type": "magic_portal", "url": "http://example.com"}]
        items = collect_all(sources)
        assert items == []


class TestResolveGoogleNewsUrl:
    def test_passthrough_non_google_url(self):
        url = "https://martech.org/some-article"
        assert _resolve_google_news_url(url) == url

    def test_passthrough_empty_url(self):
        assert _resolve_google_news_url("") == ""

    def test_decodes_google_news_url(self):
        google_url = "https://news.google.com/rss/articles/CBMiABC"
        decoded = "https://publisher.com/real-article"
        with patch("googlenewsdecoder.gnewsdecoder", return_value={"status": True, "decoded_url": decoded}):
            assert _resolve_google_news_url(google_url) == decoded

    def test_falls_back_to_original_on_decoder_failure(self):
        google_url = "https://news.google.com/rss/articles/CBMiABC"
        with patch("googlenewsdecoder.gnewsdecoder", return_value={"status": False, "message": "boom"}):
            assert _resolve_google_news_url(google_url) == google_url

    def test_falls_back_to_original_on_decoder_exception(self):
        google_url = "https://news.google.com/rss/articles/CBMiABC"
        with patch("googlenewsdecoder.gnewsdecoder", side_effect=RuntimeError("network down")):
            assert _resolve_google_news_url(google_url) == google_url
