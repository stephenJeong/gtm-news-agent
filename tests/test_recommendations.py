"""Tests for agent.recommendations module."""

import json

from agent.recommendations import (
    append_week,
    extract_recommendations,
    format_history,
    load_store,
    mark_built,
)


class TestExtractRecommendations:
    def test_parses_valid_block(self):
        raw = (
            "## Digest heading\nBody\n"
            "<recommendations_json>\n"
            '[{"title": "A", "complexity": "Low"}]\n'
            "</recommendations_json>"
        )
        clean, recs = extract_recommendations(raw)
        assert "recommendations_json" not in clean
        assert "Digest heading" in clean
        assert len(recs) == 1
        assert recs[0]["title"] == "A"

    def test_missing_block_returns_empty(self):
        clean, recs = extract_recommendations("No block here")
        assert clean == "No block here"
        assert recs == []

    def test_malformed_json_returns_empty(self):
        raw = "Body <recommendations_json>not valid json</recommendations_json>"
        clean, recs = extract_recommendations(raw)
        assert "recommendations_json" not in clean
        assert recs == []


class TestAppendWeek:
    def test_appends_with_generated_ids(self, tmp_path):
        path = tmp_path / "recs.json"
        path.write_text(json.dumps({"last_updated": "", "recommendations": []}))

        new_recs = [
            {"title": "One", "trend_signal": "t", "what_to_build": "b",
             "why_now": "n", "complexity": "Low", "inferred": False},
            {"title": "Two", "trend_signal": "t", "what_to_build": "b",
             "why_now": "n", "complexity": "High", "inferred": True},
        ]
        entries = append_week(new_recs, week_of="2026-04-14", path=str(path))
        assert len(entries) == 2
        assert entries[0]["id"] == "2026-04-14_1"
        assert entries[1]["id"] == "2026-04-14_2"
        assert entries[0]["status"] == "recommended"

        data = json.loads(path.read_text())
        assert len(data["recommendations"]) == 2
        assert data["last_updated"] == "2026-04-14"

    def test_empty_list_is_noop(self, tmp_path):
        path = tmp_path / "recs.json"
        path.write_text(json.dumps({"last_updated": "old", "recommendations": []}))
        result = append_week([], path=str(path))
        assert result == []
        data = json.loads(path.read_text())
        assert data["last_updated"] == "old"


class TestMarkBuilt:
    def test_flips_status_and_links_project(self, tmp_path):
        path = tmp_path / "recs.json"
        path.write_text(json.dumps({
            "last_updated": "2026-04-14",
            "recommendations": [{
                "id": "2026-04-14_1",
                "title": "A",
                "status": "recommended",
                "built_project_id": None,
            }],
        }))
        assert mark_built("2026-04-14_1", "my_project", path=str(path)) is True
        data = json.loads(path.read_text())
        assert data["recommendations"][0]["status"] == "built"
        assert data["recommendations"][0]["built_project_id"] == "my_project"

    def test_returns_false_for_unknown_id(self, tmp_path):
        path = tmp_path / "recs.json"
        path.write_text(json.dumps({"last_updated": "", "recommendations": []}))
        assert mark_built("nope", "proj", path=str(path)) is False


class TestFormatHistory:
    def test_marks_built_and_open(self, tmp_path):
        path = tmp_path / "recs.json"
        path.write_text(json.dumps({
            "last_updated": "2026-04-14",
            "recommendations": [
                {"id": "2026-04-14_1", "title": "Open One",
                 "complexity": "Low", "status": "recommended",
                 "built_project_id": None},
                {"id": "2026-04-07_1", "title": "Done One",
                 "complexity": "High", "status": "built",
                 "built_project_id": "proj_x"},
            ],
        }))
        out = format_history(str(path))
        assert "[OPEN]" in out
        assert "[BUILT]" in out
        assert "Open One" in out
        assert "Done One" in out
        assert "proj_x" in out

    def test_empty(self, tmp_path):
        path = tmp_path / "recs.json"
        path.write_text(json.dumps({"last_updated": "", "recommendations": []}))
        assert format_history(str(path)) == "None yet."


class TestLoadStore:
    def test_missing_file_returns_empty_structure(self, tmp_path):
        store = load_store(str(tmp_path / "nope.json"))
        assert store == {"last_updated": "", "recommendations": []}
