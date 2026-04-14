"""Tests for agent.synthesizer module."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from agent.synthesizer import (
    format_items,
    format_projects,
    load_projects,
    synthesize,
)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


# ---------------------------------------------------------------------------
# format_projects
# ---------------------------------------------------------------------------

class TestFormatProjects:
    def test_formats_completed_projects(self):
        projects = [
            {"name": "Tool A", "description": "Does A", "completed": True, "tags": ["ai", "marketo"]},
            {"name": "Tool B", "description": "Does B", "completed": False, "tags": ["slack"]},
        ]
        result = format_projects(projects)
        assert "Tool A" in result
        assert "Tool B" not in result
        assert "ai, marketo" in result

    def test_empty_projects(self):
        assert format_projects([]) == "None yet."

    def test_none_projects(self):
        assert format_projects(None) == "None yet."


# ---------------------------------------------------------------------------
# format_items
# ---------------------------------------------------------------------------

class TestFormatItems:
    def test_formats_items(self):
        items = [
            {
                "title": "AI in MOPS",
                "source_name": "MarTech",
                "published_date": "2026-04-07",
                "type": "article",
                "url": "http://example.com/1",
                "snippet": "Some text about AI.",
            }
        ]
        result = format_items(items)
        assert "AI in MOPS" in result
        assert "MarTech" in result
        assert "http://example.com/1" in result

    def test_empty_items(self):
        result = format_items([])
        assert "No items" in result


# ---------------------------------------------------------------------------
# load_projects
# ---------------------------------------------------------------------------

class TestLoadProjects:
    def test_loads_from_file(self, tmp_path):
        data = {"projects": [{"name": "X", "completed": True, "description": "test", "tags": []}]}
        path = tmp_path / "projects.json"
        path.write_text(json.dumps(data))
        projects = load_projects(str(path))
        assert len(projects) == 1
        assert projects[0]["name"] == "X"

    def test_missing_file_returns_empty(self, tmp_path):
        projects = load_projects(str(tmp_path / "nonexistent.json"))
        assert projects == []


# ---------------------------------------------------------------------------
# synthesize (mocked Claude API)
# ---------------------------------------------------------------------------

class TestSynthesize:
    def test_calls_claude_and_returns_digest_with_recommendations(self):
        items = [
            {"title": "Test Article", "source_name": "Src", "published_date": "2026-04-07",
             "type": "article", "url": "http://example.com/1", "snippet": "Content here."}
        ]
        projects = [{"name": "Old Tool", "description": "Already built", "completed": True, "tags": ["done"]}]

        raw_output = (
            "## This Week in GTM & MOPS AI\nDigest content here\n"
            "<recommendations_json>\n"
            '[{"title": "Rec A", "trend_signal": "s", "what_to_build": "b", '
            '"why_now": "n", "complexity": "Low", "inferred": false}]\n'
            "</recommendations_json>"
        )
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=raw_output)]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("agent.synthesizer.anthropic.Anthropic", return_value=mock_client):
            digest, recs = synthesize(items, projects=projects)

        assert "This Week in GTM" in digest
        assert "recommendations_json" not in digest
        assert len(recs) == 1
        assert recs[0]["title"] == "Rec A"
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == "claude-sonnet-4-6"
        assert "Old Tool" in call_kwargs.kwargs["messages"][0]["content"]

    def test_handles_missing_recommendations_block(self):
        items = [{"title": "X", "source_name": "Y", "published_date": "", "type": "article", "url": "", "snippet": ""}]

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="Digest without block")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("agent.synthesizer.anthropic.Anthropic", return_value=mock_client):
            digest, recs = synthesize(items, projects=[])

        assert digest == "Digest without block"
        assert recs == []

    def test_does_not_include_incomplete_projects_in_prompt(self):
        items = [{"title": "X", "source_name": "Y", "published_date": "", "type": "article", "url": "", "snippet": ""}]
        projects = [
            {"name": "Done Tool", "description": "Built", "completed": True, "tags": []},
            {"name": "WIP Tool", "description": "Not done", "completed": False, "tags": []},
        ]

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="Digest")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("agent.synthesizer.anthropic.Anthropic", return_value=mock_client):
            synthesize(items, projects=projects)

        prompt_text = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
        assert "Done Tool" in prompt_text
        assert "WIP Tool" not in prompt_text
