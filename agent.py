#!/usr/bin/env python3
"""
SPX Inclusion + Momentum Backtest Agent
========================================
A Claude-powered agent (claude-opus-4-6) that runs and explains the SPX
Inclusion Momentum strategy using the full backtest engine.

Usage
-----
    python agent.py                 # full backtest + analysis
    python agent.py --cmd run       # backtest summary only
    python agent.py --cmd next      # next cycle window
    python agent.py --cmd latest    # most recent addition
    python agent.py --cmd sector    # sector attribution

Environment
-----------
    ANTHROPIC_API_KEY   Required
"""

import argparse
import json
import os
import sys

import anthropic

from spx_inclusion_momentum import (
    BacktestConfig,
    _parse_additions,
    _build_candidate_universe,
    compute_cycle_performance,
    compute_cumulative_returns,
    compute_annual_returns,
    compute_max_drawdown,
    compute_sharpe,
    compute_sector_attribution,
    inclusion_vs_momentum_split,
    run_backtest,
)
from telegram.handler import (
    cmd_run,
    cmd_latest,
    cmd_next,
    cmd_additions,
    _next_cycle,
    _run_full,
)
from datetime import date


# ─── Tool implementations ─────────────────────────────────────────────────────

def tool_run_backtest(config_overrides: dict | None = None) -> dict:
    """Full backtest with optional config overrides."""
    cfg = BacktestConfig(**(config_overrides or {}))
    additions = _parse_additions()
    candidates = _build_candidate_universe(additions)
    trades = run_backtest(candidates, cfg)
    cycles = compute_cycle_performance(trades)
    _, strat_cum, bench_cum = compute_cumulative_returns(cycles)
    annual = compute_annual_returns(cycles)
    all_rets = [t.gross_return for t in trades]
    n_years = len(annual)

    return {
        "strat_cagr": round((strat_cum[-1] ** (1 / n_years) - 1), 4),
        "bench_cagr": round((bench_cum[-1] ** (1 / n_years) - 1), 4),
        "strat_total_return": round(strat_cum[-1] - 1, 4),
        "bench_total_return": round(bench_cum[-1] - 1, 4),
        "strat_sharpe": round(compute_sharpe([c.strategy_quarterly for c in cycles]), 3),
        "bench_sharpe": round(compute_sharpe([c.benchmark_quarterly for c in cycles]), 3),
        "strat_max_drawdown": round(compute_max_drawdown(strat_cum), 4),
        "bench_max_drawdown": round(compute_max_drawdown(bench_cum), 4),
        "n_trades": len(all_rets),
        "n_cycles": len(cycles),
        "win_rate": round(sum(1 for r in all_rets if r > 0) / len(all_rets), 4),
        "annual": {str(y): {k: round(v, 4) if isinstance(v, float) else v
                             for k, v in d.items()}
                   for y, d in annual.items()},
        "first_year": min(annual),
        "last_year": max(annual),
    }


def tool_sector_attribution() -> dict:
    additions = _parse_additions()
    candidates = _build_candidate_universe(additions)
    trades = run_backtest(candidates, BacktestConfig())
    attr = compute_sector_attribution(trades)
    return {s: {k: round(v, 4) if isinstance(v, float) else v
                for k, v in d.items()}
            for s, d in attr.items()}


def tool_inclusion_split() -> dict:
    additions = _parse_additions()
    candidates = _build_candidate_universe(additions)
    trades = run_backtest(candidates, BacktestConfig())
    split = inclusion_vs_momentum_split(trades)
    return {k: {kk: round(vv, 4) if isinstance(vv, float) else vv
                for kk, vv in v.items()}
            for k, v in split.items()}


def tool_next_cycle() -> dict:
    today = date.today()
    nxt = _next_cycle(today)
    return {
        "label": nxt["label"],
        "announce_approx": str(nxt["announce_approx"]),
        "entry_approx": str(nxt["entry_approx"]),
        "days_to_announce": nxt["days_to_announce"],
        "entry_window_open": nxt["days_to_announce"] <= 30,
    }


def tool_latest_addition() -> dict:
    additions = _parse_additions()
    a = additions[-1]
    total = a.ret_entry_to_announce + a.ret_announce_to_eff + a.ret_eff_to_exit
    return {
        "ticker": a.ticker,
        "company": a.company,
        "sector": a.sector,
        "announce_date": str(a.announce_date),
        "effective_date": str(a.effective_date),
        "mcap_at_add_bn": a.mcap_at_add_bn,
        "mom_12_1": round(a.mom_12_1, 4),
        "mom_3m": round(a.mom_3m, 4),
        "ret_entry_to_announce": round(a.ret_entry_to_announce, 4),
        "ret_announce_to_eff": round(a.ret_announce_to_eff, 4),
        "ret_eff_to_exit": round(a.ret_eff_to_exit, 4),
        "total_return": round(total, 4),
    }


def tool_recent_additions(n: int = 5) -> list[dict]:
    additions = _parse_additions()
    result = []
    for a in additions[-n:]:
        total = a.ret_entry_to_announce + a.ret_announce_to_eff + a.ret_eff_to_exit
        result.append({
            "ticker": a.ticker,
            "company": a.company,
            "sector": a.sector,
            "announce_date": str(a.announce_date),
            "total_return": round(total, 4),
            "mom_12_1": round(a.mom_12_1, 4),
        })
    return result


# ─── Claude tool definitions ──────────────────────────────────────────────────

TOOLS = [
    {
        "name": "run_backtest",
        "description": (
            "Run the full SPX Inclusion Momentum backtest (2012–2026). "
            "Returns CAGR, Sharpe, max drawdown, win rate, and annual returns "
            "vs S&P 500 benchmark. Optionally accepts config overrides."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "config_overrides": {
                    "type": "object",
                    "description": (
                        "Optional BacktestConfig overrides. Keys: "
                        "top_quintile_threshold (float, default 0.85), "
                        "max_positions (int, default 20), "
                        "position_size_pct (float, default 0.05), "
                        "stop_loss (float, default -0.15)."
                    ),
                }
            },
            "required": [],
        },
    },
    {
        "name": "sector_attribution",
        "description": "Break down strategy returns by GICS sector (count, mean return, win rate).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "inclusion_split",
        "description": (
            "Split performance: stocks actually added to S&P 500 vs pure momentum "
            "candidates that were never added. Shows whether inclusion premium or "
            "momentum is the primary driver."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "next_cycle",
        "description": "Return info about the next S&P 500 rebalance cycle and whether the entry window is open.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "latest_addition",
        "description": "Details of the most recent S&P 500 addition in the dataset.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "recent_additions",
        "description": "List the N most recent S&P 500 additions with momentum and return data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "Number of additions to return (default 5)."}
            },
            "required": [],
        },
    },
]


def execute_tool(name: str, inputs: dict) -> str:
    if name == "run_backtest":
        result = tool_run_backtest(inputs.get("config_overrides"))
    elif name == "sector_attribution":
        result = tool_sector_attribution()
    elif name == "inclusion_split":
        result = tool_inclusion_split()
    elif name == "next_cycle":
        result = tool_next_cycle()
    elif name == "latest_addition":
        result = tool_latest_addition()
    elif name == "recent_additions":
        result = tool_recent_additions(inputs.get("n", 5))
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result, default=str)


# ─── Agent ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a quantitative research analyst specialising in the
SPX Inclusion Momentum strategy.

The strategy buys top-quintile momentum stocks from the S&P 500 eligibility pool
30 days before each quarterly index rebalance, holds through the effective date
(capturing forced-buying from index funds), then exits.

You have tools to run the full backtest, analyse sector attribution, split
performance between included vs non-included candidates, and get cycle timing.

When asked for analysis:
1. Call the relevant tools to gather data
2. Interpret the numbers — don't just repeat them
3. Comment on: alpha vs benchmark, Sharpe improvement, where the edge comes from
   (inclusion premium vs momentum), decay over time, and current cycle timing
4. Be concise and precise — this is a quant audience"""

QUICK_PROMPTS = {
    "run":       "Run the full backtest and give me a concise performance summary vs S&P 500.",
    "sector":    "Show sector attribution and explain which sectors drive most of the alpha.",
    "split":     "Analyse the inclusion vs pure-momentum performance split. Where does the edge come from?",
    "next":      "What is the next S&P 500 cycle? Is the entry window open?",
    "latest":    "Tell me about the most recent S&P 500 addition — momentum score, returns, sector.",
    "full":      (
        "Run the full backtest, sector attribution, inclusion split, and next cycle timing. "
        "Give me a comprehensive analysis: performance vs benchmark, primary edge drivers, "
        "sector concentration, premium decay, and current actionable signal."
    ),
}


def run_agent(prompt: str) -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ERROR: ANTHROPIC_API_KEY is not set.")

    client = anthropic.Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": prompt}]

    print(f"\n{'═'*64}")
    print("  SPX INCLUSION MOMENTUM AGENT")
    print(f"{'═'*64}\n")

    while True:
        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=8192,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(block.text)

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"\n[→ {block.name}]")
            result = execute_tool(block.name, block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SPX Inclusion Momentum Agent")
    parser.add_argument(
        "--cmd",
        choices=list(QUICK_PROMPTS.keys()),
        default="full",
        help="Preset command (default: full)",
    )
    parser.add_argument("--prompt", help="Custom prompt (overrides --cmd)")
    args = parser.parse_args()

    prompt = args.prompt if args.prompt else QUICK_PROMPTS[args.cmd]
    run_agent(prompt)


if __name__ == "__main__":
    main()
