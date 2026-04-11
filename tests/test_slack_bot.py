"""Tests for agent.slack_bot module — Phase 4: Conversation handling."""

import json
import os
from unittest.mock import MagicMock, patch, call

import pytest

from agent.slack_bot import (
    _get_reply,
    _handle_digest_now,
    _handle_project_done,
    _handle_sources,
    _markdown_to_blocks,
    _process_event,
    post_digest,
)
import agent.slack_bot as slack_bot_module


# ---------------------------------------------------------------------------
# Block Kit formatting
# ---------------------------------------------------------------------------

class TestMarkdownToBlocks:
    def test_headers_become_header_blocks(self):
        text = "## This Week in GTM & MOPS AI\nSome body text."
        blocks = _markdown_to_blocks(text)
        header_blocks = [b for b in blocks if b["type"] == "header"]
        assert len(header_blocks) == 1
        assert header_blocks[0]["text"]["text"] == "This Week in GTM & MOPS AI"

    def test_body_text_becomes_section_blocks(self):
        text = "Just some plain text\nand more text."
        blocks = _markdown_to_blocks(text)
        section_blocks = [b for b in blocks if b["type"] == "section"]
        assert len(section_blocks) >= 1
        assert "plain text" in section_blocks[0]["text"]["text"]

    def test_dividers_inserted_before_headers(self):
        text = "## Header One\nBody\n### Header Two\nMore body"
        blocks = _markdown_to_blocks(text)
        types = [b["type"] for b in blocks]
        # Each header should be preceded by a divider
        for i, t in enumerate(types):
            if t == "header":
                assert i > 0 and types[i - 1] == "divider"


# ---------------------------------------------------------------------------
# post_digest
# ---------------------------------------------------------------------------

class TestPostDigest:
    def test_posts_message_and_pins(self):
        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ts": "123.456"}

        with patch("agent.slack_bot.WebClient", return_value=mock_client):
            ts = post_digest("## Digest\nBody text", channel_id="C123", bot_token="xoxb-test")

        assert ts == "123.456"
        mock_client.chat_postMessage.assert_called_once()
        mock_client.pins_add.assert_called_once_with(channel="C123", timestamp="123.456")

    def test_sets_current_digest(self):
        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ts": "1"}

        with patch("agent.slack_bot.WebClient", return_value=mock_client):
            post_digest("New digest content", channel_id="C1", bot_token="xoxb-x")

        assert slack_bot_module._current_digest == "New digest content"

    def test_clears_thread_conversations(self):
        slack_bot_module._thread_conversations["old_thread"] = [{"role": "user", "content": "hi"}]
        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ts": "1"}

        with patch("agent.slack_bot.WebClient", return_value=mock_client):
            post_digest("Fresh digest", channel_id="C1", bot_token="xoxb-x")

        assert slack_bot_module._thread_conversations == {}

    def test_returns_none_when_missing_config(self):
        result = post_digest("text", channel_id=None, bot_token=None)
        assert result is None


# ---------------------------------------------------------------------------
# Conversation handling (_get_reply)
# ---------------------------------------------------------------------------

class TestGetReply:
    def test_returns_no_digest_message_when_empty(self):
        slack_bot_module._current_digest = None
        reply = _get_reply("What about lead scoring?", "thread_1")
        assert "No digest" in reply

    def test_calls_claude_with_conversation_history(self):
        slack_bot_module._current_digest = "## Digest\nSome content here"
        slack_bot_module._thread_conversations.clear()

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="Here's more detail on that.")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("agent.slack_bot.anthropic.Anthropic", return_value=mock_client):
            reply = _get_reply("Tell me more about recommendation 1", "thread_A")

        assert reply == "Here's more detail on that."
        create_kwargs = mock_client.messages.create.call_args.kwargs
        assert "Digest" in create_kwargs["system"]
        # History is mutated after the call, so check the stored thread history
        history = slack_bot_module._thread_conversations["thread_A"]
        assert len(history) == 2  # user + assistant
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

    def test_maintains_conversation_across_turns(self):
        slack_bot_module._current_digest = "## Digest\nContent"
        slack_bot_module._thread_conversations.clear()

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="Reply 1")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("agent.slack_bot.anthropic.Anthropic", return_value=mock_client):
            _get_reply("First question", "thread_B")

        # Second turn
        mock_message.content = [MagicMock(text="Reply 2")]
        with patch("agent.slack_bot.anthropic.Anthropic", return_value=mock_client):
            _get_reply("Follow up", "thread_B")

        # After two turns, thread history should have 4 entries
        history = slack_bot_module._thread_conversations["thread_B"]
        assert len(history) == 4  # user1, assistant1, user2, assistant2
        assert history[0]["content"] == "First question"
        assert history[1]["role"] == "assistant"
        assert history[2]["content"] == "Follow up"
        assert history[3]["role"] == "assistant"

    def test_separate_threads_have_separate_history(self):
        slack_bot_module._current_digest = "## Digest"
        slack_bot_module._thread_conversations.clear()

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="Reply")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("agent.slack_bot.anthropic.Anthropic", return_value=mock_client):
            _get_reply("Thread 1 question", "thread_X")
            _get_reply("Thread 2 question", "thread_Y")

        assert len(slack_bot_module._thread_conversations["thread_X"]) == 2  # user + assistant
        assert len(slack_bot_module._thread_conversations["thread_Y"]) == 2
        assert slack_bot_module._thread_conversations["thread_X"][0]["content"] == "Thread 1 question"
        assert slack_bot_module._thread_conversations["thread_Y"][0]["content"] == "Thread 2 question"


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

class TestHandleProjectDone:
    def test_adds_project_to_json(self, tmp_path):
        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps({"last_updated": "", "projects": []}))

        with patch.object(slack_bot_module, "MEMORY_PATH", str(projects_file)):
            result = _handle_project_done("Lead Router: Routes leads based on territory")

        assert "Lead Router" in result
        data = json.loads(projects_file.read_text())
        assert len(data["projects"]) == 1
        assert data["projects"][0]["name"] == "Lead Router"
        assert data["projects"][0]["description"] == "Routes leads based on territory"
        assert data["projects"][0]["completed"] is True

    def test_handles_name_only(self, tmp_path):
        projects_file = tmp_path / "projects.json"
        projects_file.write_text(json.dumps({"last_updated": "", "projects": []}))

        with patch.object(slack_bot_module, "MEMORY_PATH", str(projects_file)):
            result = _handle_project_done("Simple Tool")

        data = json.loads(projects_file.read_text())
        assert data["projects"][0]["name"] == "Simple Tool"
        assert data["projects"][0]["description"] == "Simple Tool"

    def test_returns_usage_when_empty(self):
        result = _handle_project_done("")
        assert "Usage" in result


class TestHandleSources:
    def test_lists_configured_sources(self, tmp_path):
        sources_file = tmp_path / "sources.json"
        sources_file.write_text(json.dumps([
            {"id": "test", "name": "Test Source", "type": "substack_rss"},
        ]))
        with patch.object(slack_bot_module, "CONFIG_PATH", str(sources_file)):
            result = _handle_sources()

        assert "Test Source" in result
        assert "substack_rss" in result

    def test_handles_missing_file(self):
        with patch.object(slack_bot_module, "CONFIG_PATH", "/nonexistent/sources.json"):
            result = _handle_sources()
        assert "No sources" in result


class TestHandleDigestNow:
    def test_runs_pipeline_and_posts(self):
        with patch("agent.slack_bot.run_full_pipeline", return_value="## Digest"), \
             patch("agent.slack_bot.post_digest") as mock_post:
            result = _handle_digest_now()

        mock_post.assert_called_once_with("## Digest")
        assert "posted" in result.lower()

    def test_handles_no_content(self):
        with patch("agent.slack_bot.run_full_pipeline", return_value=None):
            result = _handle_digest_now()
        assert "No content" in result

    def test_handles_pipeline_failure(self):
        with patch("agent.slack_bot.run_full_pipeline", side_effect=Exception("API down")):
            result = _handle_digest_now()
        assert "failed" in result.lower()


# ---------------------------------------------------------------------------
# Socket Mode event processing
# ---------------------------------------------------------------------------

class TestProcessEvent:
    def _make_event_req(self, event, envelope_id="env_1"):
        req = MagicMock()
        req.type = "events_api"
        req.envelope_id = envelope_id
        req.payload = {"event": event}
        return req

    def _make_slash_req(self, command, text="", envelope_id="env_2"):
        req = MagicMock()
        req.type = "slash_commands"
        req.envelope_id = envelope_id
        req.payload = {"command": command, "text": text}
        return req

    def test_app_mention_triggers_reply(self):
        slack_bot_module._current_digest = "## Digest"
        slack_bot_module._thread_conversations.clear()

        mock_socket_client = MagicMock()
        mock_web_client = MagicMock()

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="Bot reply")]
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create.return_value = mock_message

        req = self._make_event_req({
            "type": "app_mention",
            "text": "<@U123BOT> What about lead scoring?",
            "channel": "C123",
            "ts": "100.1",
        })

        with patch("agent.slack_bot.anthropic.Anthropic", return_value=mock_anthropic), \
             patch("agent.slack_bot.WebClient", return_value=mock_web_client):
            _process_event(mock_socket_client, req)

        # Should ack the event
        mock_socket_client.send_socket_mode_response.assert_called_once()

        # Should post a reply in the thread
        mock_web_client.chat_postMessage.assert_called_once()
        post_kwargs = mock_web_client.chat_postMessage.call_args.kwargs
        assert post_kwargs["channel"] == "C123"
        assert post_kwargs["thread_ts"] == "100.1"
        assert post_kwargs["text"] == "Bot reply"

    def test_skips_bot_messages(self):
        mock_socket_client = MagicMock()
        req = self._make_event_req({
            "type": "message",
            "bot_id": "B123",
            "text": "I am a bot",
            "channel": "C123",
            "ts": "100.2",
        })

        with patch("agent.slack_bot.WebClient") as mock_wc:
            _process_event(mock_socket_client, req)

        # Should ack but not post any reply
        mock_socket_client.send_socket_mode_response.assert_called_once()
        mock_wc.return_value.chat_postMessage.assert_not_called()

    def test_thread_reply_uses_thread_ts(self):
        slack_bot_module._current_digest = "## Digest"
        slack_bot_module._thread_conversations.clear()

        mock_socket_client = MagicMock()
        mock_web_client = MagicMock()

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="Threaded reply")]
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create.return_value = mock_message

        req = self._make_event_req({
            "type": "message",
            "text": "Follow up question",
            "channel": "C123",
            "thread_ts": "100.1",
            "ts": "100.5",
        })

        with patch("agent.slack_bot.anthropic.Anthropic", return_value=mock_anthropic), \
             patch("agent.slack_bot.WebClient", return_value=mock_web_client):
            _process_event(mock_socket_client, req)

        post_kwargs = mock_web_client.chat_postMessage.call_args.kwargs
        assert post_kwargs["thread_ts"] == "100.1"

    def test_slash_command_project_done(self):
        mock_socket_client = MagicMock()
        req = self._make_slash_req("/project-done", "New Tool: does stuff")

        with patch("agent.slack_bot._handle_project_done", return_value="Added *New Tool*") as mock_handler:
            _process_event(mock_socket_client, req)

        mock_handler.assert_called_once_with("New Tool: does stuff")
        response = mock_socket_client.send_socket_mode_response.call_args[0][0]
        assert response.payload["text"] == "Added *New Tool*"

    def test_slash_command_sources(self):
        mock_socket_client = MagicMock()
        req = self._make_slash_req("/sources")

        with patch("agent.slack_bot._handle_sources", return_value="- *Source A*") as mock_handler:
            _process_event(mock_socket_client, req)

        mock_handler.assert_called_once()

    def test_unknown_request_type_acks(self):
        mock_socket_client = MagicMock()
        req = MagicMock()
        req.type = "unknown_type"
        req.envelope_id = "env_99"

        _process_event(mock_socket_client, req)
        mock_socket_client.send_socket_mode_response.assert_called_once()
