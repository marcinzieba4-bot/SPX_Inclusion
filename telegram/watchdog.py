"""
SPX Inclusion Momentum — Telegram Bot Watchdog
================================================

Starts telegram/polling.py as a subprocess and automatically restarts
it if the process exits unexpectedly. Uses exponential backoff (2 → 60 s)
to avoid hammering on persistent failures, and resets the delay when the
child has been running for more than STABLE_SECS seconds.

Watchdog also writes a heartbeat file (/tmp/spx_bot_watchdog.hb) every
HEARTBEAT_INTERVAL seconds so an external monitor (cron, systemd, etc.)
can check liveness independently.

Usage
-----
    python telegram/watchdog.py         # run directly
    nohup python telegram/watchdog.py & # background

Signals
-------
    SIGTERM / SIGINT: gracefully forwards to child, then exits watchdog.

Environment
-----------
    Same as polling.py — all vars are inherited by the child process.
"""

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watchdog] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
_HERE         = os.path.dirname(os.path.abspath(__file__))
_ROOT         = os.path.join(_HERE, "..")
_POLLING      = os.path.join(_HERE, "polling.py")
_ENV_FILE     = os.path.join(_ROOT, ".env")


def _load_env_file() -> None:
    """Load .env from project root if it exists, without overwriting existing vars."""
    env_path = Path(_ENV_FILE)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:   # don't overwrite already-set vars
            os.environ[key] = val
    # ALLOWED_CHAT_ID alias
    if "ALLOWED_CHAT_ID" not in os.environ and "TELEGRAM_CHAT_ID" in os.environ:
        os.environ["ALLOWED_CHAT_ID"] = os.environ["TELEGRAM_CHAT_ID"]
    if "AWS_DEFAULT_REGION" not in os.environ and "AWS_REGION" in os.environ:
        os.environ["AWS_DEFAULT_REGION"] = os.environ["AWS_REGION"]

INITIAL_DELAY    = 2.0    # seconds before first restart
MAX_DELAY        = 60.0   # cap for exponential backoff
STABLE_SECS      = 60.0   # running longer than this resets the backoff delay
HEARTBEAT_FILE   = "/tmp/spx_bot_watchdog.hb"
HEARTBEAT_INTERVAL = 30   # seconds between heartbeat writes

# ── Shutdown coordination ─────────────────────────────────────────────────────
_shutdown  = threading.Event()
_child_ref: subprocess.Popen | None = None
_child_lock = threading.Lock()


def _on_signal(sig, _frame):
    log.info("Signal %s received — stopping watchdog.", sig)
    _shutdown.set()
    with _child_lock:
        if _child_ref is not None:
            try:
                log.info("Forwarding signal to child PID %d.", _child_ref.pid)
                _child_ref.terminate()
            except ProcessLookupError:
                pass


signal.signal(signal.SIGTERM, _on_signal)
signal.signal(signal.SIGINT,  _on_signal)

# ── Heartbeat thread ──────────────────────────────────────────────────────────

def _heartbeat_loop() -> None:
    """Write timestamp to heartbeat file every HEARTBEAT_INTERVAL seconds."""
    while not _shutdown.is_set():
        try:
            with open(HEARTBEAT_FILE, "w") as f:
                f.write(str(time.time()))
        except Exception as exc:
            log.warning("Heartbeat write failed: %s", exc)
        _shutdown.wait(timeout=HEARTBEAT_INTERVAL)


# ── Main watchdog loop ────────────────────────────────────────────────────────

def run() -> None:
    global _child_ref

    _load_env_file()
    log.info("Watchdog started. Managing: %s", _POLLING)
    log.info("Heartbeat file: %s (interval %ds)", HEARTBEAT_FILE, HEARTBEAT_INTERVAL)

    # Start heartbeat writer in background
    hb_thread = threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat")
    hb_thread.start()

    delay         = INITIAL_DELAY
    restart_count = 0

    while not _shutdown.is_set():
        log.info("Starting polling process (attempt #%d).", restart_count + 1)

        proc = subprocess.Popen(
            [sys.executable, _POLLING],
            env=os.environ.copy(),
            stdout=sys.stdout,   # inherit so logs are visible
            stderr=sys.stderr,
        )

        with _child_lock:
            _child_ref = proc

        log.info("Child PID %d started.", proc.pid)
        start_time = time.monotonic()

        # Block until child exits (or shutdown signal arrives)
        while True:
            try:
                proc.wait(timeout=1.0)
                break   # process finished
            except subprocess.TimeoutExpired:
                if _shutdown.is_set():
                    log.info("Shutdown: terminating child PID %d.", proc.pid)
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break

        with _child_lock:
            _child_ref = None

        if _shutdown.is_set():
            break

        elapsed   = time.monotonic() - start_time
        exit_code = proc.returncode
        restart_count += 1

        log.warning(
            "Child exited (code=%d) after %.1fs. Restart #%d.",
            exit_code, elapsed, restart_count,
        )

        if elapsed >= STABLE_SECS:
            # Process ran long enough — treat as "clean" and reset backoff
            delay = INITIAL_DELAY
            log.info("Process was stable (%.0fs ≥ %.0fs) — backoff reset to %.1fs.",
                     elapsed, STABLE_SECS, delay)
        else:
            log.info("Restarting in %.1fs (backoff).", delay)

        # Interruptible sleep so SIGTERM wakes us immediately
        _shutdown.wait(timeout=delay)

        if not _shutdown.is_set():
            delay = min(delay * 2, MAX_DELAY)

    log.info("Watchdog exiting after %d restarts.", restart_count)
    try:
        os.remove(HEARTBEAT_FILE)
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    run()
