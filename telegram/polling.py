"""
SPX Inclusion Momentum — Telegram Long-Polling Bot
===================================================

Runs continuously: polls Telegram getUpdates, dispatches slash-commands
to the existing handler functions, and routes free-form questions to
Claude (claude-opus-4-6) with the full SPX agent tool-set.

Per-chat conversation history is kept in memory so follow-up questions
work naturally within a session. Update offset is persisted to S3 so
restarts don't re-process old messages.

Usage
-----
    python telegram/polling.py          # direct
    python -m telegram.polling          # module
    python telegram/watchdog.py         # recommended (with watchdog)

Required env vars
-----------------
    TELEGRAM_BOT_TOKEN   — from @BotFather
    ANTHROPIC_API_KEY    — Anthropic API key
    ALLOWED_CHAT_ID      — your personal chat ID (integer)
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION
"""

import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request

import anthropic
import boto3
from pathlib import Path

# ── Path: allow imports from project root ─────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load_env_file() -> None:
    """Load .env from project root if present, without overwriting existing vars."""
    env_path = Path(_ROOT) / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
    if "ALLOWED_CHAT_ID" not in os.environ and "TELEGRAM_CHAT_ID" in os.environ:
        os.environ["ALLOWED_CHAT_ID"] = os.environ["TELEGRAM_CHAT_ID"]
    if "AWS_DEFAULT_REGION" not in os.environ and "AWS_REGION" in os.environ:
        os.environ["AWS_DEFAULT_REGION"] = os.environ["AWS_REGION"]

_load_env_file()  # must run before importing handler (which reads env at import time)

from telegram.handler import (   # noqa: E402
    BOT_TOKEN,
    ALLOWED_CHAT_ID,
    _TG_API,
    _post,
    cmd_help,
    cmd_run,
    cmd_latest,
    cmd_next,
    cmd_additions,
    send,
)
from agent import SYSTEM_PROMPT, TOOLS, execute_tool  # noqa: E402

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [polling] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
S3_BUCKET    = "s3bucketmz"
S3_STATE_KEY = "telegram-agent-state.json"
POLL_TIMEOUT = 30        # seconds for Telegram long-poll
HEARTBEAT_INTERVAL = 60  # seconds between heartbeat log lines

# ── Graceful shutdown ─────────────────────────────────────────────────────────
_shutdown = threading.Event()

def _on_signal(sig, _frame):
    log.info("Signal %s received — shutting down cleanly.", sig)
    _shutdown.set()

signal.signal(signal.SIGTERM, _on_signal)
signal.signal(signal.SIGINT,  _on_signal)

# ── S3 state (update offset) ─────────────────────────────────────────────────
_s3_client = None

def _s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=os.environ.get("AWS_DEFAULT_REGION", "eu-north-1"))
    return _s3_client


def load_offset() -> int:
    try:
        obj  = _s3().get_object(Bucket=S3_BUCKET, Key=S3_STATE_KEY)
        data = json.loads(obj["Body"].read())
        offset = int(data.get("next_offset", 0))
        log.info("Loaded offset %d from S3.", offset)
        return offset
    except Exception as exc:
        log.warning("Could not load offset from S3 (%s) — starting from 0.", exc)
        return 0


def save_offset(offset: int) -> None:
    try:
        _s3().put_object(
            Bucket=S3_BUCKET,
            Key=S3_STATE_KEY,
            Body=json.dumps({"next_offset": offset}).encode(),
            ContentType="application/json",
        )
    except Exception as exc:
        log.warning("Could not save offset to S3: %s", exc)

# ── Telegram API ──────────────────────────────────────────────────────────────

def _tg_get(method: str, params: dict | None = None, timeout: int = 35) -> dict:
    url = f"{_TG_API}/{method}"
    if params:
        qs  = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_updates(offset: int) -> list:
    try:
        resp = _tg_get(
            "getUpdates",
            {
                "offset":           offset,
                "timeout":          POLL_TIMEOUT,
                "allowed_updates":  "message",
            },
            timeout=POLL_TIMEOUT + 5,
        )
        return resp.get("result", [])
    except urllib.error.URLError as exc:
        log.warning("getUpdates network error: %s", exc)
        return []
    except Exception as exc:
        log.error("getUpdates unexpected error: %s", exc)
        return []

# ── Claude chat ───────────────────────────────────────────────────────────────
# Per-chat conversation history (in-memory; reset on process restart)
_histories: dict[int, list] = {}

_EDIT_INTERVAL = 1.2   # minimum seconds between editMessageText calls


def _send_and_get_id(chat_id: int, text: str) -> int | None:
    """Send a message and return its message_id (for later editing)."""
    try:
        resp = _post("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        return resp.get("result", {}).get("message_id")
    except Exception:
        return None


def _edit(chat_id: int, message_id: int, text: str) -> None:
    """Edit an existing message. Silently ignores 'not modified' errors."""
    if not message_id:
        return
    try:
        _post("editMessageText", {
            "chat_id":    chat_id,
            "message_id": message_id,
            "text":       text[:4096],
        })
    except Exception as exc:
        if "message is not modified" not in str(exc):
            log.debug("editMessageText failed: %s", exc)


def _claude_reply(chat_id: int, user_text: str) -> None:
    """
    Stream Claude's response live into Telegram by editing the placeholder
    message as text tokens arrive — mirrors agent._agent_turn() but sends
    to Telegram instead of stdout.

    Uses stream.text_stream so the user sees text immediately instead of
    waiting for the full response (which can include long thinking phases).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        send(chat_id, "⚠️ ANTHROPIC_API_KEY not set on the server.")
        return

    client  = anthropic.Anthropic(api_key=api_key)
    history = _histories.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})

    messages    = list(history)
    reply_parts: list[str] = []

    # Send the placeholder and grab its message_id so we can edit it live
    msg_id = _send_and_get_id(chat_id, "_(thinking…)_")

    while True:
        accumulated = ""
        last_edit   = 0.0   # force first edit as soon as text arrives

        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=8192,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            for token in stream.text_stream:
                accumulated += token
                now = time.monotonic()
                if now - last_edit >= _EDIT_INTERVAL:
                    prefix = "\n\n".join(reply_parts)
                    live   = (prefix + "\n\n" + accumulated).strip() + " ▌"
                    _edit(chat_id, msg_id, live)
                    last_edit = now
            response = stream.get_final_message()

        if accumulated.strip():
            reply_parts.append(accumulated.strip())

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break
        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            log.info("Tool call: %s (chat %d)", block.name, chat_id)
            # Show which tool is running
            status = "\n\n".join(reply_parts)
            status = (status + f"\n\n_(running {block.name}…)_").strip()
            _edit(chat_id, msg_id, status)
            result = execute_tool(block.name, block.input)
            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": block.id,
                "content":     result,
            })
        messages.append({"role": "user", "content": tool_results})

    # Persist the completed conversation turn
    _histories[chat_id] = messages

    full_reply = "\n\n".join(reply_parts) or "_(no response)_"

    if len(full_reply) <= 4000:
        _edit(chat_id, msg_id, full_reply)
    else:
        # First chunk replaces the placeholder; subsequent chunks are new messages
        _edit(chat_id, msg_id, full_reply[:4000])
        for i in range(4000, len(full_reply), 4000):
            send(chat_id, full_reply[i : i + 4000])


# ── Command dispatch ──────────────────────────────────────────────────────────
_COMMANDS: dict[str, callable] = {
    "help":      cmd_help,
    "start":     cmd_help,
    "run":       cmd_run,
    "latest":    cmd_latest,
    "next":      cmd_next,
    "additions": cmd_additions,
    # /reset clears conversation history for this chat
}


def _handle_reset(chat_id: int) -> None:
    _histories.pop(chat_id, None)
    send(chat_id, "Conversation history cleared.")


def handle_message(message: dict) -> None:
    chat_id = message["chat"]["id"]

    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
        log.warning("Ignored message from unauthorised chat_id=%d", chat_id)
        return

    text = (message.get("text") or "").strip()
    if not text:
        return

    log.info("Message from chat_id=%d: %.80s", chat_id, text)

    if text.startswith("/"):
        cmd = text.split()[0].lstrip("/").lower().split("@")[0]
        if cmd == "reset":
            _handle_reset(chat_id)
        elif cmd in _COMMANDS:
            reply = _COMMANDS[cmd]()
            send(chat_id, reply)
        else:
            send(chat_id, f"Unknown command: /{cmd}\nUse /help.")
    else:
        # Free-form question → Claude
        send(chat_id, "_(thinking…)_")
        _claude_reply(chat_id, text)


# ── Main loop ─────────────────────────────────────────────────────────────────

def run() -> None:
    if not BOT_TOKEN:
        sys.exit("ERROR: TELEGRAM_BOT_TOKEN is not set.")

    log.info("Bot started. Poll timeout=%ds.", POLL_TIMEOUT)

    offset            = load_offset()
    last_heartbeat    = time.monotonic()
    updates_processed = 0

    while not _shutdown.is_set():

        # Heartbeat log
        now = time.monotonic()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            log.info("Heartbeat — offset=%d, updates_processed=%d", offset, updates_processed)
            last_heartbeat = now

        updates = get_updates(offset)

        for update in updates:
            update_id = update["update_id"]
            msg = update.get("message") or update.get("edited_message")
            if msg:
                try:
                    handle_message(msg)
                    updates_processed += 1
                except Exception as exc:
                    log.error("Error handling update %d: %s", update_id, exc, exc_info=True)
            offset = update_id + 1
            save_offset(offset)

        # If long-poll returned nothing, a tiny guard prevents tight-looping
        # on network errors; on success the 30s poll already provides backpressure.
        if not updates and _shutdown.wait(timeout=0.5):
            break

    log.info("Polling stopped. Total updates processed: %d", updates_processed)


if __name__ == "__main__":
    run()
