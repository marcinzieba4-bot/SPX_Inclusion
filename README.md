# SPX Inclusion + Momentum — Backtest Agent

A Claude-powered agent that backtests the strategy of buying stocks
**announced for S&P 500 inclusion** when they also show **positive price momentum**.

## Strategy Logic

| Step | Action |
|------|--------|
| 1 | Collect S&P 500 addition announcements |
| 2 | Filter: only buy if 20-day momentum > threshold |
| 3 | Buy at next-day open after announcement |
| 4 | Sell at close on `effective_date + hold_days` |
| 5 | Report: total return, Sharpe, win rate, drawdown |

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your API key
cp .env.example .env
# edit .env and add ANTHROPIC_API_KEY=sk-ant-...

# 3. Run (defaults: last 3 years, no momentum filter, 5-day hold)
python agent.py

# Custom run
python agent.py \
  --start 2021-01-01 \
  --end   2024-01-01 \
  --lookback   20   \   # trading days for momentum
  --threshold  0.02 \   # min 2% momentum
  --hold       5    \   # days past effective date
  --capital 100000
```

## What the Agent Does

The agent uses Claude (claude-opus-4-6 with adaptive thinking) and five tools:

| Tool | Purpose |
|------|---------|
| `get_spx_changes` | Fetch addition/removal events (Wikipedia or local CSV) |
| `get_price_data` | Download OHLCV via yfinance |
| `score_candidates` | Compute momentum, apply filter |
| `run_backtest` | Simulate trades, build equity curve, compute metrics |
| `generate_report` | Format human-readable summary |

Claude calls these tools autonomously in order, then writes a qualitative analysis.

## Custom Data Source

By default, S&P 500 changes are scraped from Wikipedia. To use your own data:

```bash
cp data/sp500_changes.csv.example data/sp500_changes.csv
# Edit the CSV — required columns: ticker, date, event_type
# Optional columns: company, effective_date
```

## What You Need to Provide

| Item | How to get it |
|------|---------------|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |

That's it — price data comes from Yahoo Finance (free, no key needed) and
SPX change events come from Wikipedia (also free).

## Output

```
============================================================
  SPX INCLUSION + MOMENTUM  —  BACKTEST REPORT
============================================================

STRATEGY PARAMETERS
  Lookback (momentum) : 20 trading days
  Momentum threshold  : 2.0%
  Hold period         : 5 calendar days post-effective
  Date range          : 2021-01-01 → 2024-01-01

PERFORMANCE METRICS
  Total return        : 14.32%
  Number of trades    : 38
  Win rate            : 60.53%
  Avg return / trade  : 0.4871%
  ...
```
