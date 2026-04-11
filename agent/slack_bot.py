"""
Slack bot module: delivers weekly digest and handles conversation replies.

Delivery: posts digest to a channel using Block Kit formatting.
Conversation: listens via Socket Mode for thread replies and app mentions,
passes context back to Claude for follow-up answers.
Slash commands: /project-done, /digest-now, /sources.
"""

import json
import logging
import os
import re

import anthropic
from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from agent.prompts import CONVERSATION_SYSTEM_PROMPT
from agent.synthesizer import run_full_pipeline

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MEMORY_PATH = os.path.join(os.path.dirname(__file__), "..", "memory", "projects.json")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "sources.json")
MODEL = "claude-sonnet-4-6"

# Stores the latest digest text for conversation context. Resets each week.
_current_digest = None
# Conversation history per thread, keyed by thread_ts.
_thread_conversations = {}


# ---------------------------------------------------------------------------
# Block Kit formatting
# ---------------------------------------------------------------------------

def _markdown_to_blocks(digest_text):
    """Convert the markdown digest into Slack Block Kit blocks."""
    blocks = []
    lines = digest_text.split("\n")
    buffer = []

    def flush_buffer():
        if buffer:
            text = "\n".join(buffer).strip()
            if not text:
                buffer.clear()
                return
            # Slack section blocks have a 3000 char limit; split on blank lines
            if len(text) <= 3000:
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": text},
                })
            else:
                paragraphs = text.split("\n\n")
                chunk = ""
                for para in paragraphs:
                    if chunk and len(chunk) + len(para) + 2 > 3000:
                        blocks.append({
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": chunk.strip()},
                        })
                        chunk = ""
                    chunk += para + "\n\n"
                if chunk.strip():
                    blocks.append({
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": chunk.strip()},
                    })
            buffer.clear()

    for line in lines:
        if line.startswith("## ") or line.startswith("### "):
            flush_buffer()
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "header",
                "text": {"type": "plain_text", "text": line.lstrip("#").strip(), "emoji": False},
            })
        else:
            buffer.append(line)

    flush_buffer()
    return blocks


def post_digest(digest_text, channel_id=None, bot_token=None):
    """Post the digest to a Slack channel using Block Kit and pin it."""
    global _current_digest
    _current_digest = digest_text

    channel_id = channel_id or os.environ.get("SLACK_CHANNEL_ID")
    bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN")

    if not channel_id or not bot_token:
        logger.error("SLACK_CHANNEL_ID and SLACK_BOT_TOKEN must be set")
        return None

    client = WebClient(token=bot_token)
    blocks = _markdown_to_blocks(digest_text)

    response = client.chat_postMessage(
        channel=channel_id,
        text="Weekly GTM & MOPS AI Digest",
        blocks=blocks,
    )

    ts = response["ts"]
    logger.info("Digest posted to %s (ts=%s)", channel_id, ts)

    try:
        client.pins_add(channel=channel_id, timestamp=ts)
        logger.info("Message pinned")
    except Exception as e:
        logger.warning("Failed to pin message: %s", e)

    # Reset conversation history for the new week
    _thread_conversations.clear()

    return ts


# ---------------------------------------------------------------------------
# Conversation handling
# ---------------------------------------------------------------------------

def _get_reply(user_text, thread_ts):
    """Generate a reply to a follow-up question using Claude."""
    if not _current_digest:
        return "No digest has been posted yet this week. Try `/digest-now` first."

    history = _thread_conversations.get(thread_ts, [])
    history.append({"role": "user", "content": user_text})

    system_prompt = CONVERSATION_SYSTEM_PROMPT.format(digest_text=_current_digest)

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=system_prompt,
        messages=history,
    )

    reply = message.content[0].text
    history.append({"role": "assistant", "content": reply})
    _thread_conversations[thread_ts] = history

    return reply


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

def _handle_project_done(text):
    """Add a new project to memory/projects.json."""
    if not text.strip():
        return "Usage: `/project-done Project Name: short description`"

    parts = text.split(":", 1)
    name = parts[0].strip()
    description = parts[1].strip() if len(parts) > 1 else name

    project_id = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

    try:
        with open(MEMORY_PATH) as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {"last_updated": "", "projects": []}

    data["projects"].append({
        "id": project_id,
        "name": name,
        "description": description,
        "completed": True,
        "tags": [],
    })
    data["last_updated"] = __import__("datetime").datetime.now().strftime("%Y-%m-%d")

    with open(MEMORY_PATH, "w") as f:
        json.dump(data, f, indent=2)

    return f"Added *{name}* to your project portfolio."


def _handle_sources():
    """List all configured sources."""
    try:
        with open(CONFIG_PATH) as f:
            sources = json.load(f)
    except FileNotFoundError:
        return "No sources.json found."

    lines = []
    for s in sources:
        lines.append(f"- *{s['name']}* ({s['type']}) — `{s['id']}`")
    return "\n".join(lines)


def _handle_digest_now():
    """Run an immediate digest, post it to the channel, and confirm."""
    try:
        digest = run_full_pipeline()
        if digest:
            post_digest(digest)
            return "Digest generated and posted to the channel."
        return "No content collected. Check source configuration and try again."
    except Exception as e:
        logger.error("Digest run failed: %s", e)
        return f"Digest run failed: {e}"


# ---------------------------------------------------------------------------
# Socket Mode listener
# ---------------------------------------------------------------------------

def _process_event(client, req):
    """Handle incoming Socket Mode events."""
    if req.type == "events_api":
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

        event = req.payload.get("event", {})
        event_type = event.get("type")

        if event_type in ("app_mention", "message"):
            # Skip bot's own messages
            if event.get("bot_id"):
                return

            text = event.get("text", "")
            # Strip the bot mention from the text
            text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
            channel = event.get("channel")
            thread_ts = event.get("thread_ts") or event.get("ts")

            reply = _get_reply(text, thread_ts)

            web_client = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))
            web_client.chat_postMessage(
                channel=channel,
                text=reply,
                thread_ts=thread_ts,
            )

    elif req.type == "slash_commands":
        command = req.payload.get("command")
        text = req.payload.get("text", "")

        if command == "/project-done":
            response_text = _handle_project_done(text)
        elif command == "/digest-now":
            response_text = _handle_digest_now()
        elif command == "/sources":
            response_text = _handle_sources()
        else:
            response_text = f"Unknown command: {command}"

        client.send_socket_mode_response(
            SocketModeResponse(envelope_id=req.envelope_id, payload={"text": response_text})
        )

    else:
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))


def start_socket_mode():
    """Start the Socket Mode listener for conversation and slash commands."""
    app_token = os.environ.get("SLACK_APP_TOKEN")
    bot_token = os.environ.get("SLACK_BOT_TOKEN")

    if not app_token or not bot_token:
        logger.error("SLACK_APP_TOKEN and SLACK_BOT_TOKEN must be set")
        return

    socket_client = SocketModeClient(
        app_token=app_token,
        web_client=WebClient(token=bot_token),
    )
    socket_client.socket_mode_request_listeners.append(_process_event)
    socket_client.connect()
    logger.info("Socket Mode listener started")

    from threading import Event
    Event().wait()


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def deliver_digest(fixture_path=None):
    """Full pipeline: collect -> synthesize -> post to Slack."""
    digest = run_full_pipeline(fixture_path=fixture_path)
    if digest:
        post_digest(digest)
        return digest
    logger.warning("No digest generated")
    return None


def main():
    import argparse
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="GTM News Agent Slack Bot")
    parser.add_argument("--deliver", action="store_true", help="Run collector+synthesizer and post digest")
    parser.add_argument("--listen", action="store_true", help="Start Socket Mode listener for conversations")
    parser.add_argument("--fixture", type=str, help="Use fixture data instead of live scraping")
    args = parser.parse_args()

    if args.deliver:
        deliver_digest(fixture_path=args.fixture)
    elif args.listen:
        start_socket_mode()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
