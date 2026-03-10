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

# ── Path: allow imports from project root ─────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from telegram.handler import (   # noqa: E402
    BOT_TOKEN,
    ALLOWED_CHAT_ID,
    _TG_API,
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


def _claude_reply(chat_id: int, user_text: str) -> None:
    """
    Route a free-form message to Claude, run the full tool-use loop,
    and send the result back to Telegram in ≤4000-char chunks.

    Conversation history is preserved per chat_id so follow-up questions
    work naturally (e.g. "What about sector attribution?" after asking
    about CAGR doesn't need context repeated).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        send(chat_id, "⚠️ ANTHROPIC_API_KEY not set on the server.")
        return

    client   = anthropic.Anthropic(api_key=api_key)
    history  = _histories.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})

    messages = list(history)   # working copy for this turn
    reply_parts: list[str] = []

    # Tool-use loop — mirrors agent._agent_turn() but collects text
    # instead of printing, so we can send it to Telegram
    while True:
        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

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

    # Persist the completed turn into the conversation history
    _histories[chat_id] = messages

    full_reply = "\n\n".join(reply_parts) or "_(no response)_"

    # Split into 4000-char Telegram-safe chunks
    for i in range(0, len(full_reply), 4000):
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
