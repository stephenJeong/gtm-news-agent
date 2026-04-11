"""Tests for deployment configuration and CLI entry points."""

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest


class TestDockerfile:
    def test_dockerfile_exists(self):
        with open("Dockerfile") as f:
            content = f.read()
        assert "python:3.11" in content
        assert "--listen" in content

    def test_dockerfile_copies_requirements_first(self):
        """Requirements are copied before the rest of the code for layer caching."""
        with open("Dockerfile") as f:
            lines = f.readlines()
        req_line = next(i for i, l in enumerate(lines) if "COPY requirements.txt" in l)
        copy_all_line = next(i for i, l in enumerate(lines) if "COPY . ." in l)
        assert req_line < copy_all_line


class TestRenderConfig:
    def test_render_yaml_exists(self):
        with open("render.yaml") as f:
            content = f.read()
        assert "worker" in content
        assert "SLACK_BOT_TOKEN" in content
        assert "SLACK_APP_TOKEN" in content
        assert "ANTHROPIC_API_KEY" in content

    def test_render_uses_free_plan(self):
        with open("render.yaml") as f:
            content = f.read()
        assert "plan: free" in content


class TestCLIEntryPoints:
    def test_slack_bot_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent.slack_bot", "--help"],
            capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0
        assert "--deliver" in result.stdout
        assert "--listen" in result.stdout

    def test_collector_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent.collector", "--help"],
            capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0
        assert "--dry-run" in result.stdout

    def test_synthesizer_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "agent.synthesizer", "--help"],
            capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0
        assert "--fixture" in result.stdout


class TestSocketModeStart:
    def test_start_socket_mode_requires_tokens(self, capsys):
        """Should log error and return if tokens are missing."""
        import agent.slack_bot as bot
        with patch.dict("os.environ", {"SLACK_APP_TOKEN": "", "SLACK_BOT_TOKEN": ""}, clear=False):
            bot.start_socket_mode()
        # Function returns without blocking when tokens are missing
