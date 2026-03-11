"""
SPX Inclusion Momentum — Telegram Long-Polling Bot
===================================================

Mirrors the pattern used by daily-digest-telegram-webhook Lambda:
  • timeout=0 polls (non-blocking, sleep between cycles)
  • skip messages older than MAX_MSG_AGE seconds
  • plain client.messages.create() — no streaming, no editMessageText
  • sequential processing — one message fully handled before next poll

Usage
-----
    python telegram/polling.py
    python telegram/watchdog.py   (recommended — with auto-restart)

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

_load_env_file()  # must run before importing handler (reads env at import time)

from telegram.handler import (   # noqa: E402
    BOT_TOKEN, ALLOWED_CHAT_ID, _TG_API, _post,
    cmd_help, cmd_run, cmd_latest, cmd_next, cmd_additions, send,
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
S3_BUCKET     = "s3bucketmz"
S3_STATE_KEY  = "telegram-polling-offset.json"  # dedicated key, no Lambda conflict
POLL_INTERVAL = 2     # seconds between polls (timeout=0 non-blocking style)
MAX_MSG_AGE   = 0     # 0 = disabled; Lambda uses 180s but we're persistent so never skip
HEARTBEAT_INTERVAL = 60

# ── Graceful shutdown ─────────────────────────────────────────────────────────
_shutdown = False

def _on_signal(sig, _frame):
    global _shutdown
    log.info("Signal %s received — shutting down.", sig)
    _shutdown = True

signal.signal(signal.SIGTERM, _on_signal)
signal.signal(signal.SIGINT,  _on_signal)

# ── S3 offset ─────────────────────────────────────────────────────────────────
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
        if offset:
            log.info("Loaded offset %d from S3.", offset)
            return offset
    except Exception as exc:
        log.warning("Could not load offset from S3 (%s).", exc)
    # No saved offset — skip the backlog, start from now (same as Lambda get_fresh_offset)
    return _get_fresh_offset()


def _get_fresh_offset() -> int:
    """Return offset just past the latest existing update to skip the backlog."""
    try:
        result = tg_post("getUpdates", {"limit": 1, "timeout": 0})
        updates = result.get("result", [])
        if updates:
            offset = updates[-1]["update_id"] + 1
            log.info("Fresh offset from Telegram: %d (skipping backlog).", offset)
            return offset
    except Exception as exc:
        log.warning("Could not fetch fresh offset: %s", exc)
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


# ── Telegram helpers ──────────────────────────────────────────────────────────

def tg_post(method: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"{_TG_API}/{method}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def get_updates(offset: int) -> list:
    try:
        result = tg_post("getUpdates", {
            "offset":          offset,
            "limit":           10,
            "timeout":         0,         # non-blocking, same as Lambda
            "allowed_updates": ["message"],
        })
        return result.get("result", [])
    except Exception as exc:
        log.warning("getUpdates error: %s", exc)
        return []


# ── Claude chat ───────────────────────────────────────────────────────────────
_histories: dict[int, list] = {}


def _claude_reply(chat_id: int, user_text: str) -> None:
    """
    Call Claude with the full SPX tool-set and send the reply to Telegram.

    Matches the Lambda pattern: plain client.messages.create() (no streaming),
    send 'thinking…' first, then send the final reply as a new message.
    Conversation history kept in memory for follow-up questions.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        send(chat_id, "⚠️ ANTHROPIC_API_KEY not set on the server.")
        return

    client  = anthropic.Anthropic(api_key=api_key)
    history = _histories.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})
    messages = list(history)

    send(chat_id, "_(thinking…)_")

    reply_parts: list[str] = []

    try:
        while True:
            response = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=8192,
                thinking={"type": "adaptive"},
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            for block in response.content:
                if block.type == "text" and block.text.strip():
                    reply_parts.append(block.text.strip())

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
                result = execute_tool(block.name, block.input)
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     result,
                })
            messages.append({"role": "user", "content": tool_results})

    except Exception as exc:
        log.error("Claude error (chat %d): %s", chat_id, exc, exc_info=True)
        send(chat_id, f"❌ Error: {exc}")
        return

    _histories[chat_id] = messages

    full_reply = "\n\n".join(reply_parts) or "_(no response)_"
    log.info("Reply %d chars to chat %d.", len(full_reply), chat_id)

    for i in range(0, len(full_reply), 4000):
        send(chat_id, full_reply[i : i + 4000])


# ── Command dispatch ──────────────────────────────────────────────────────────
_COMMANDS = {
    "help":      cmd_help,
    "start":     cmd_help,
    "run":       cmd_run,
    "latest":    cmd_latest,
    "next":      cmd_next,
    "additions": cmd_additions,
}


def handle_message(message: dict) -> None:
    chat_id  = message["chat"]["id"]
    msg_date = message.get("date", 0)

    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
        log.warning("Ignored message from unauthorised chat_id=%d", chat_id)
        return

    age = int(time.time()) - msg_date
    text = (message.get("text") or "").strip()
    if not text:
        return

    log.info("Message from chat_id=%d (age=%ds): %.80s", chat_id, age, text)
    if age > 300:
        log.warning("Processing old message (age=%ds) — bot was likely down.", age)

    if text.startswith("/"):
        cmd = text.split()[0].lstrip("/").lower().split("@")[0]
        if cmd == "reset":
            _histories.pop(chat_id, None)
            send(chat_id, "Conversation history cleared.")
        elif cmd in _COMMANDS:
            send(chat_id, _COMMANDS[cmd]())
        else:
            send(chat_id, f"Unknown command: /{cmd}\nUse /help.")
    else:
        _claude_reply(chat_id, text)


# ── Main loop ─────────────────────────────────────────────────────────────────

def run() -> None:
    if not BOT_TOKEN:
        sys.exit("ERROR: TELEGRAM_BOT_TOKEN is not set.")

    log.info("Bot started. Poll interval=%ds, max_msg_age=%ds.", POLL_INTERVAL, MAX_MSG_AGE)

    offset            = load_offset()
    last_heartbeat    = time.monotonic()
    updates_processed = 0

    while not _shutdown:
        now = time.monotonic()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            log.info("Heartbeat — offset=%d, processed=%d.", offset, updates_processed)
            last_heartbeat = now

        updates = get_updates(offset)

        for update in updates:
            update_id = update["update_id"]
            offset    = update_id + 1
            save_offset(offset)

            msg = update.get("message") or update.get("edited_message")
            if not msg:
                continue
            try:
                handle_message(msg)
                updates_processed += 1
            except Exception as exc:
                log.error("Error handling update %d: %s", update_id, exc, exc_info=True)

        if not updates:
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
