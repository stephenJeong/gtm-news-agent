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
    def test_calls_claude_and_returns_digest(self):
        items = [
            {"title": "Test Article", "source_name": "Src", "published_date": "2026-04-07",
             "type": "article", "url": "http://example.com/1", "snippet": "Content here."}
        ]
        projects = [{"name": "Old Tool", "description": "Already built", "completed": True, "tags": ["done"]}]

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="## This Week in GTM & MOPS AI\nDigest content here")]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("agent.synthesizer.anthropic.Anthropic", return_value=mock_client):
            digest = synthesize(items, projects=projects)

        assert "This Week in GTM" in digest
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == "claude-sonnet-4-6"
        assert call_kwargs.kwargs["max_tokens"] == 2000
        assert "Old Tool" in call_kwargs.kwargs["messages"][0]["content"]

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
