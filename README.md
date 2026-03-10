# SPX Inclusion + Momentum Agent

Claude-powered agent (`claude-opus-4-6`) that runs and analyses the **SPX Inclusion Momentum** strategy using the full production backtest engine.

## Strategy

| Step | Action |
|------|--------|
| 1 | Build eligibility pool: US large-caps not yet in S&P 500, ≥$12B mcap, 4Q profitable |
| 2 | Score by **4-factor composite**: 12-1 mom (35%) + 3m mom (20%) + mcap rank (30%) + eligibility streak (15%) |
| 3 | Buy **top quintile** 30 days before quarterly announcement |
| 4 | Hold through effective date + 3 days (captures forced index-fund buying) |
| 5 | Hard stop-loss at −15%; rotate at each quarterly rebalance |

## Structure

```
spx_inclusion_momentum.py   Core backtest engine (2012–2026 dataset, ~964 lines)
telegram/handler.py         Telegram bot + Lambda handler
agent.py                    Claude agent — runs backtest, answers questions
requirements.txt
.env.example
```

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env        # add ANTHROPIC_API_KEY

python agent.py             # full analysis
python agent.py --cmd run       # backtest summary
python agent.py --cmd sector    # sector attribution
python agent.py --cmd split     # inclusion vs momentum split
python agent.py --cmd next      # next cycle / entry window
python agent.py --cmd latest    # most recent addition
python agent.py --prompt "Is the momentum edge decaying post-2020?"
```

## Telegram Bot

The Lambda (`spx-momentum-telegram-alert`, `eu-north-1`) exposes:

| Command | Description |
|---------|-------------|
| `/run` | Full backtest table (CAGR, Sharpe, drawdown vs S&P 500) |
| `/latest` | Most recent addition with momentum + return breakdown |
| `/next` | Next cycle date + days until announcement |
| `/additions` | Last 5 confirmed additions |
| `/help` | Command list |

EventBridge triggers daily alerts at T−30, T−7, and T=0 days before each cycle.

## What You Need

| Item | Value |
|------|-------|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `TELEGRAM_BOT_TOKEN` | Already configured in Lambda |
| `ALLOWED_CHAT_ID` | Already configured in Lambda |
| AWS creds | Already configured in Lambda (`eu-north-1`) |
