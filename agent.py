#!/usr/bin/env python3
"""
SPX Inclusion + Momentum Backtest Agent
========================================
A Claude-powered agent that backtests the strategy of buying stocks
announced for S&P 500 inclusion when they also have positive momentum.

Usage
-----
    python agent.py                          # interactive mode
    python agent.py --start 2021-01-01 --end 2024-01-01
    python agent.py --lookback 20 --threshold 0.02 --hold 5

Environment
-----------
    ANTHROPIC_API_KEY   Required — your Anthropic API key
"""

import argparse
import datetime
import json
import os
import sys

import anthropic

from tools.spx_data import get_spx_changes
from tools.market_data import get_price_data, get_ticker_info
from tools.momentum import calculate_momentum, score_candidates
from tools.backtest import run_backtest, generate_report


# ─── Tool definitions (sent to Claude) ───────────────────────────────────────

TOOLS: list[dict] = [
    {
        "name": "get_spx_changes",
        "description": (
            "Fetch historical S&P 500 additions or removals. "
            "Returns a list of events with ticker, company, announcement date, "
            "and effective date."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start of period, YYYY-MM-DD. Defaults to 3 years ago.",
                },
                "end_date": {
                    "type": "string",
                    "description": "End of period, YYYY-MM-DD. Defaults to today.",
                },
                "event_type": {
                    "type": "string",
                    "enum": ["additions", "removals", "both"],
                    "description": "Which type of changes to return.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_price_data",
        "description": (
            "Download daily closing prices for a list of tickers between two dates. "
            "Returns {ticker: {date: price}}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tickers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of ticker symbols.",
                },
                "start_date": {
                    "type": "string",
                    "description": "Start date YYYY-MM-DD.",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date YYYY-MM-DD.",
                },
            },
            "required": ["tickers", "start_date", "end_date"],
        },
    },
    {
        "name": "score_candidates",
        "description": (
            "Score SPX inclusion candidates by price momentum and filter by threshold. "
            "Returns 'scored' (all) and 'filtered' (passing threshold) lists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of event dicts from get_spx_changes.",
                },
                "price_data": {
                    "type": "object",
                    "description": "Price data from get_price_data.",
                },
                "lookback_days": {
                    "type": "integer",
                    "description": "Trading days to look back for momentum calculation.",
                    "default": 20,
                },
                "momentum_threshold": {
                    "type": "number",
                    "description": "Minimum momentum (fractional) to pass filter. E.g. 0.02 = 2%.",
                    "default": 0.0,
                },
            },
            "required": ["candidates", "price_data"],
        },
    },
    {
        "name": "run_backtest",
        "description": (
            "Simulate the SPX inclusion + momentum strategy on filtered candidates. "
            "Returns per-trade results, performance metrics, and equity curve."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Filtered candidates from score_candidates.",
                },
                "price_data": {
                    "type": "object",
                    "description": "Price data from get_price_data.",
                },
                "hold_days": {
                    "type": "integer",
                    "description": "Calendar days to hold after effective date.",
                    "default": 5,
                },
                "initial_capital": {
                    "type": "number",
                    "description": "Starting capital in USD.",
                    "default": 100000,
                },
                "slippage_bps": {
                    "type": "number",
                    "description": "One-way slippage in basis points.",
                    "default": 5,
                },
            },
            "required": ["candidates", "price_data"],
        },
    },
    {
        "name": "generate_report",
        "description": "Format a human-readable backtest summary report.",
        "input_schema": {
            "type": "object",
            "properties": {
                "backtest_result": {
                    "type": "object",
                    "description": "Output from run_backtest.",
                },
                "strategy_params": {
                    "type": "object",
                    "description": "Dict of strategy parameters to include in header.",
                },
            },
            "required": ["backtest_result", "strategy_params"],
        },
    },
]


# ─── Tool dispatcher ──────────────────────────────────────────────────────────

def execute_tool(name: str, inputs: dict) -> str:
    """Route a tool call to the correct implementation and return JSON string."""
    if name == "get_spx_changes":
        result = get_spx_changes(**inputs)
    elif name == "get_price_data":
        result = get_price_data(**inputs)
    elif name == "score_candidates":
        result = score_candidates(**inputs)
    elif name == "run_backtest":
        result = run_backtest(**inputs)
    elif name == "generate_report":
        result = generate_report(**inputs)
    else:
        result = {"error": f"Unknown tool: {name}"}

    return json.dumps(result, default=str)


# ─── Agent loop ───────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a quantitative research agent specialising in index-inclusion strategies.

Your job: run a backtest of the **SPX Inclusion + Momentum** strategy:
1. Fetch S&P 500 addition events for the requested period.
2. Download price data for each candidate (extend the range 30 days before the
   announcement date to capture momentum, and enough days after the effective date).
3. Score candidates by momentum and filter by the provided threshold.
4. Run the backtest with the provided hold-period.
5. Generate a concise report summarising performance.

Always finish with a clear written analysis of:
- Whether the strategy added alpha over the period
- Which sectors / dates performed best
- Any caveats (small sample size, look-ahead bias risks, etc.)

Use the tools in order. Be methodical and do not skip steps."""


def run_agent(
    start_date: str,
    end_date: str,
    lookback_days: int = 20,
    momentum_threshold: float = 0.0,
    hold_days: int = 5,
    initial_capital: float = 100_000.0,
) -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ERROR: ANTHROPIC_API_KEY environment variable is not set.")

    client = anthropic.Anthropic(api_key=api_key)

    user_prompt = (
        f"Please backtest the SPX inclusion + momentum strategy with these parameters:\n"
        f"- Date range       : {start_date} to {end_date}\n"
        f"- Momentum lookback: {lookback_days} trading days\n"
        f"- Momentum filter  : {momentum_threshold:.1%} minimum\n"
        f"- Hold period      : {hold_days} calendar days after effective date\n"
        f"- Initial capital  : ${initial_capital:,.0f}\n\n"
        f"Use the available tools step-by-step and end with a written analysis."
    )

    messages: list[dict] = [{"role": "user", "content": user_prompt}]

    print(f"\n{'='*60}")
    print("  SPX INCLUSION + MOMENTUM AGENT")
    print(f"{'='*60}")
    print(f"  Period   : {start_date} → {end_date}")
    print(f"  Lookback : {lookback_days}d  |  Threshold : {momentum_threshold:.1%}  |  Hold : {hold_days}d")
    print(f"{'='*60}\n")

    turn = 0
    while True:
        turn += 1
        print(f"[Turn {turn}] Calling Claude...")

        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=8192,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        # Print any text Claude produced
        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"\n[Claude] {block.text}\n")

        # Append Claude's response
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            print("\n[Agent] Done.")
            break

        if response.stop_reason != "tool_use":
            print(f"[Agent] Unexpected stop reason: {response.stop_reason}. Stopping.")
            break

        # Execute all tool calls
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"[Tool ] {block.name}({_summarise_inputs(block.input)})")
            result_str = execute_tool(block.name, block.input)
            result_preview = result_str[:200] + "..." if len(result_str) > 200 else result_str
            print(f"[Tool ] → {result_preview}\n")
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                }
            )

        messages.append({"role": "user", "content": tool_results})


def _summarise_inputs(inputs: dict) -> str:
    """One-line summary of tool inputs for logging."""
    parts = []
    for k, v in inputs.items():
        if isinstance(v, list):
            parts.append(f"{k}=[{len(v)} items]")
        elif isinstance(v, dict):
            parts.append(f"{k}={{...}}")
        else:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    today = datetime.date.today()
    default_start = (today - datetime.timedelta(days=3 * 365)).isoformat()
    default_end = today.isoformat()

    parser = argparse.ArgumentParser(
        description="SPX Inclusion + Momentum Backtest Agent"
    )
    parser.add_argument(
        "--start", default=default_start, help=f"Start date YYYY-MM-DD (default {default_start})"
    )
    parser.add_argument(
        "--end", default=default_end, help=f"End date YYYY-MM-DD (default {default_end})"
    )
    parser.add_argument(
        "--lookback", type=int, default=20, help="Momentum lookback in trading days (default 20)"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.0,
        help="Minimum momentum fraction to trade (default 0.0 = no filter)"
    )
    parser.add_argument(
        "--hold", type=int, default=5,
        help="Calendar days to hold past effective date (default 5)"
    )
    parser.add_argument(
        "--capital", type=float, default=100_000.0,
        help="Initial capital in USD (default 100000)"
    )
    args = parser.parse_args()

    run_agent(
        start_date=args.start,
        end_date=args.end,
        lookback_days=args.lookback,
        momentum_threshold=args.threshold,
        hold_days=args.hold,
        initial_capital=args.capital,
    )


if __name__ == "__main__":
    main()
