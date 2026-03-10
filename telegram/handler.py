"""
SPX Inclusion Momentum — Telegram Bot (AWS Lambda)
===================================================

Webhook Lambda: receives Telegram updates via API Gateway POST /webhook
Scheduler Lambda: same handler, triggered by EventBridge for daily alerts

Environment variables (set in SAM / Lambda console):
  TELEGRAM_BOT_TOKEN   — from @BotFather
  ALLOWED_CHAT_ID      — your personal chat ID (integer)
                         Get it: message @userinfobot or run setup.sh
"""

import json
import math
import os
import sys
import urllib.request
from datetime import date, timedelta

# ── Import backtest engine ────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "examples"))

from spx_inclusion_momentum import (  # noqa: E402
    BacktestConfig,
    _ADDITIONS_RAW,
    _SPX_QUARTERLY,
    _build_candidate_universe,
    _parse_additions,
    compute_cycle_performance,
    compute_cumulative_returns,
    compute_max_drawdown,
    compute_sharpe,
    run_backtest,
)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_ID = int(os.environ.get("ALLOWED_CHAT_ID", "0"))

_TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _post(endpoint: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{_TG_API}/{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def send(chat_id: int, text: str) -> None:
    """Send a Markdown message; fall back to plain text on parse error."""
    try:
        _post("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
    except Exception:
        _post("sendMessage", {"chat_id": chat_id, "text": text})


# ── Backtest helpers (cached at cold-start) ───────────────────────────────────

def _run_full() -> dict:
    """Run backtest and return key scalars."""
    additions = _parse_additions()
    candidates = _build_candidate_universe(additions)
    trades = run_backtest(candidates, BacktestConfig())
    cycles = compute_cycle_performance(trades)
    dates, strat_cum, bench_cum = compute_cumulative_returns(cycles)

    strat_rets = [c.strategy_ret for c in cycles]
    bench_rets = [c.benchmark_ret for c in cycles]
    all_trade_rets = [t.gross_return for t in trades]

    n_years = (cycles[-1].cycle_end - cycles[0].cycle_start).days / 365.25
    strat_cagr = strat_cum[-1] ** (1 / n_years) - 1
    bench_cagr = bench_cum[-1] ** (1 / n_years) - 1

    return {
        "strat_cagr": strat_cagr,
        "bench_cagr": bench_cagr,
        "strat_sharpe": compute_sharpe(strat_rets),
        "bench_sharpe": compute_sharpe(bench_rets),
        "strat_dd": compute_max_drawdown(strat_cum),
        "bench_dd": compute_max_drawdown(bench_cum),
        "win_rate": sum(1 for r in all_trade_rets if r > 0) / len(all_trade_rets),
        "n_trades": len(all_trade_rets),
        "n_cycles": len(cycles),
        "strat_cum_total": strat_cum[-1] - 1,
        "bench_cum_total": bench_cum[-1] - 1,
        "first_year": cycles[0].cycle_start.year,
        "last_year": cycles[-1].cycle_end.year,
    }


# ── Quarter helpers ───────────────────────────────────────────────────────────

_QUARTER_ANNOUNCE_MONTHS = {1: 3, 2: 6, 3: 9, 4: 12}  # Q→announce month (approx day 6)


def _next_cycle(today: date) -> dict:
    """Return info about the next S&P 500 rebalance announcement."""
    year, month = today.year, today.month
    # Find the next quarterly announcement month (Mar/Jun/Sep/Dec, ~day 6)
    announce_months = [3, 6, 9, 12]
    for am in announce_months:
        ann = date(year, am, 6)
        if ann > today:
            return {
                "label": f"{year} Q{announce_months.index(am) + 1}",
                "announce_approx": ann,
                "entry_approx": ann - timedelta(days=30),
                "days_to_announce": (ann - today).days,
            }
    # Roll to next year Q1
    ann = date(year + 1, 3, 6)
    return {
        "label": f"{year + 1} Q1",
        "announce_approx": ann,
        "entry_approx": ann - timedelta(days=30),
        "days_to_announce": (ann - today).days,
    }


def _fmt_pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v * 100:.1f}%"


# ── Command handlers ──────────────────────────────────────────────────────────

def cmd_help() -> str:
    return (
        "*SPX Inclusion Momentum Bot*\n\n"
        "/run — Full backtest summary (CAGR, Sharpe, drawdown)\n"
        "/latest — Most recent S\\&P 500 addition in the model\n"
        "/next — Next cycle window \\+ days until announcement\n"
        "/additions — Last 5 confirmed additions\n"
        "/help — This message"
    )


def cmd_run() -> str:
    try:
        r = _run_full()
        return (
            f"*Backtest: {r['first_year']}–{r['last_year']}*\n\n"
            f"```\n"
            f"{'Metric':<25} {'Strategy':>10} {'S&P 500':>10}\n"
            f"{'-'*45}\n"
            f"{'CAGR':<25} {_fmt_pct(r['strat_cagr']):>10} {_fmt_pct(r['bench_cagr']):>10}\n"
            f"{'Total Return':<25} {_fmt_pct(r['strat_cum_total']):>10} {_fmt_pct(r['bench_cum_total']):>10}\n"
            f"{'Sharpe Ratio':<25} {r['strat_sharpe']:>10.2f} {r['bench_sharpe']:>10.2f}\n"
            f"{'Max Drawdown':<25} {_fmt_pct(r['strat_dd']):>10} {_fmt_pct(r['bench_dd']):>10}\n"
            f"{'Win Rate':<25} {r['win_rate']*100:>9.1f}%{'':>10}\n"
            f"{'Trades / Cycles':<25} {r['n_trades']:>4} / {r['n_cycles']:<5}{'':>5}\n"
            f"```"
        )
    except Exception as exc:
        return f"Error running backtest: {exc}"


def cmd_latest() -> str:
    additions = _parse_additions()
    last = additions[-1]
    total = last.ret_entry_to_announce + last.ret_announce_to_eff + last.ret_eff_to_exit
    return (
        f"*Latest addition: {last.ticker} — {last.company}*\n\n"
        f"Sector: {last.sector}\n"
        f"Announced: {last.announce_date}\n"
        f"Effective: {last.effective_date}\n"
        f"Mcap at add: ${last.mcap_at_add_bn:.1f}B\n\n"
        f"*Returns (model)*\n"
        f"Entry→Announce: {_fmt_pct(last.ret_entry_to_announce)}\n"
        f"Announce→Eff:   {_fmt_pct(last.ret_announce_to_eff)}\n"
        f"Eff→Exit:       {_fmt_pct(last.ret_eff_to_exit)}\n"
        f"*Total:          {_fmt_pct(total)}*"
    )


def cmd_next() -> str:
    today = date.today()
    nxt = _next_cycle(today)
    days = nxt["days_to_announce"]
    urgency = "🔴 ENTRY WINDOW NOW" if days <= 30 else ("🟡 Approaching" if days <= 45 else "🟢 Watch")
    return (
        f"*Next S\\&P 500 cycle: {nxt['label']}*\n\n"
        f"Status: {urgency}\n"
        f"Entry (T\\-30): {nxt['entry_approx']}\n"
        f"Announcement: {nxt['announce_approx']} (~day 6)\n"
        f"Days to announce: *{days}*\n\n"
        f"_Strategy entry is 30 days before announcement._\n"
        f"_Top\\-quintile candidates selected at that date._"
    )


def cmd_additions() -> str:
    additions = _parse_additions()
    recent = additions[-5:]
    lines = ["*Last 5 S\\&P 500 additions:*\n"]
    for a in reversed(recent):
        total = a.ret_entry_to_announce + a.ret_announce_to_eff + a.ret_eff_to_exit
        lines.append(
            f"*{a.ticker}* {a.announce_date.strftime('%b %Y')} "
            f"— {_fmt_pct(total)} total ({a.sector})"
        )
    return "\n".join(lines)


# ── Alert (scheduler) ─────────────────────────────────────────────────────────

def handle_alert() -> None:
    """Sent daily by EventBridge. Alerts when entry window is open."""
    if not ALLOWED_CHAT_ID:
        return
    today = date.today()
    nxt = _next_cycle(today)
    days = nxt["days_to_announce"]

    if days == 30:
        send(ALLOWED_CHAT_ID,
             f"*SPX Momentum — ENTRY WINDOW OPEN*\n\n"
             f"Today is T\\-30 for {nxt['label']}.\n"
             f"Announcement expected: {nxt['announce_approx']}\n\n"
             f"Run /run and /next for details.")
    elif days == 7:
        send(ALLOWED_CHAT_ID,
             f"*SPX Momentum — Announcement in 7 days*\n\n"
             f"Cycle: {nxt['label']} | Expected: {nxt['announce_approx']}\n"
             f"Check positions and prepare to hold through effective date.")
    elif days == 0:
        send(ALLOWED_CHAT_ID,
             f"*SPX Momentum — ANNOUNCEMENT DAY*\n\n"
             f"Cycle: {nxt['label']}\n"
             f"Watch for the official S\\&P Dow Jones press release.\n"
             f"Use /latest to update the model after the announcement.")


# ── Lambda entry point ────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    # Scheduled EventBridge trigger
    if event.get("source") == "aws.events":
        handle_alert()
        return {"statusCode": 200, "body": "alert sent"}

    # Telegram webhook
    try:
        body = json.loads(event.get("body") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {"statusCode": 400, "body": "bad request"}

    message = body.get("message") or body.get("edited_message")
    if not message:
        return {"statusCode": 200, "body": "ok"}

    chat_id = message["chat"]["id"]

    # Security: ignore messages from anyone else
    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
        return {"statusCode": 200, "body": "ok"}

    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        return {"statusCode": 200, "body": "ok"}

    cmd = text.split()[0].lstrip("/").lower().split("@")[0]

    dispatch = {
        "help":      cmd_help,
        "start":     cmd_help,
        "run":       cmd_run,
        "latest":    cmd_latest,
        "next":      cmd_next,
        "additions": cmd_additions,
    }

    handler_fn = dispatch.get(cmd)
    reply = handler_fn() if handler_fn else f"Unknown command: /{cmd}\nUse /help."
    send(chat_id, reply)

    return {"statusCode": 200, "body": "ok"}
