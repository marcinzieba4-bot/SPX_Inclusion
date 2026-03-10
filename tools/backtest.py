"""
Backtest engine for the SPX Inclusion + Momentum strategy.

Strategy rules
--------------
1. Universe : stocks announced for S&P 500 addition
2. Filter   : momentum over the last N trading days > threshold
3. Entry    : buy at next-day open after announcement date
4. Exit     : sell at close on (effective_date + hold_days)
5. Position : equal-weight across all active positions
6. Costs    : configurable slippage + commission per trade

Output metrics
--------------
- Total return, annualised return, Sharpe ratio, Sortino ratio
- Max drawdown, win rate, average trade return
- Per-trade breakdown
"""

import datetime
import math
from typing import Any


# ─── main entry point ─────────────────────────────────────────────────────────


def run_backtest(
    candidates: list[dict[str, Any]],
    price_data: dict[str, dict[str, float]],
    hold_days: int = 5,
    initial_capital: float = 100_000.0,
    slippage_bps: float = 5.0,
    commission_per_trade: float = 0.0,
) -> dict:
    """
    Simulate the strategy on a list of pre-filtered candidates.

    Args:
        candidates:           List of dicts from score_candidates["filtered"]
                              Each must have: ticker, date, effective_date
        price_data:           {ticker: {date_str: close_price}}
        hold_days:            Calendar days to hold past the effective date
        initial_capital:      Starting portfolio value in USD
        slippage_bps:         One-way slippage in basis points (5 bps = 0.05 %)
        commission_per_trade: Fixed commission per trade in USD

    Returns:
        dict with "trades", "metrics", "equity_curve", "error"
    """
    try:
        if not candidates:
            return {
                "trades": [],
                "metrics": {},
                "equity_curve": {},
                "error": "No candidates supplied",
            }

        slippage = slippage_bps / 10_000

        trades = []
        for c in candidates:
            ticker = event_ticker = c["ticker"]
            series = price_data.get(ticker, {})
            if not series:
                continue

            ann_date = c["date"]  # announcement date
            eff_date = c.get("effective_date", ann_date)

            # Entry: first trading day AFTER announcement
            entry_date = _next_date(series, ann_date)
            if entry_date is None:
                continue
            entry_price = series[entry_date] * (1 + slippage)

            # Exit: first trading day ON OR AFTER effective_date + hold_days
            exit_target = (
                datetime.date.fromisoformat(eff_date)
                + datetime.timedelta(days=hold_days)
            ).isoformat()
            exit_date = _on_or_after(series, exit_target)
            if exit_date is None:
                continue
            exit_price = series[exit_date] * (1 - slippage)

            gross_return = (exit_price - entry_price) / entry_price
            trades.append(
                {
                    "ticker": ticker,
                    "company": c.get("company", ticker),
                    "announcement_date": ann_date,
                    "effective_date": eff_date,
                    "entry_date": entry_date,
                    "entry_price": round(entry_price, 4),
                    "exit_date": exit_date,
                    "exit_price": round(exit_price, 4),
                    "gross_return": round(gross_return, 6),
                    "momentum_at_signal": c.get("momentum"),
                    "momentum_signal": c.get("signal"),
                }
            )

        if not trades:
            return {
                "trades": [],
                "metrics": {"error": "No complete trades could be simulated"},
                "equity_curve": {},
                "error": None,
            }

        # ── build equity curve (simplified: sequential, equal-weight) ──────
        trades.sort(key=lambda t: t["entry_date"])
        equity = initial_capital
        equity_curve: dict[str, float] = {}
        peak = equity

        for t in trades:
            position_size = equity / len(
                [x for x in trades if x["entry_date"] == t["entry_date"]]
            )
            pnl = position_size * t["gross_return"] - commission_per_trade
            equity += pnl
            t["pnl"] = round(pnl, 2)
            t["equity_after"] = round(equity, 2)
            equity_curve[t["exit_date"]] = round(equity, 2)
            peak = max(peak, equity)

        # ── metrics ────────────────────────────────────────────────────────
        returns = [t["gross_return"] for t in trades]
        n = len(returns)
        mean_r = sum(returns) / n
        variance = sum((r - mean_r) ** 2 for r in returns) / max(n - 1, 1)
        std_r = math.sqrt(variance)

        # Sharpe (annualised, assuming ~252 trades/year scaling isn't right for
        # this strategy — we use per-trade Sharpe instead)
        sharpe = (mean_r / std_r) if std_r > 0 else 0.0

        neg_returns = [r for r in returns if r < 0]
        downside_var = sum(r ** 2 for r in neg_returns) / max(len(neg_returns), 1)
        sortino = (mean_r / math.sqrt(downside_var)) if downside_var > 0 else 0.0

        win_rate = sum(1 for r in returns if r > 0) / n

        total_return = (equity - initial_capital) / initial_capital

        # Max drawdown from peak over equity curve
        max_dd = _max_drawdown(list(equity_curve.values()), initial_capital)

        metrics = {
            "total_return_pct": round(total_return * 100, 2),
            "final_equity": round(equity, 2),
            "number_of_trades": n,
            "win_rate_pct": round(win_rate * 100, 2),
            "avg_return_per_trade_pct": round(mean_r * 100, 4),
            "std_return_per_trade_pct": round(std_r * 100, 4),
            "per_trade_sharpe": round(sharpe, 4),
            "per_trade_sortino": round(sortino, 4),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "best_trade": round(max(returns) * 100, 2),
            "worst_trade": round(min(returns) * 100, 2),
        }

        return {
            "trades": trades,
            "metrics": metrics,
            "equity_curve": equity_curve,
            "error": None,
        }

    except Exception as exc:
        return {"trades": [], "metrics": {}, "equity_curve": {}, "error": str(exc)}


def generate_report(backtest_result: dict, strategy_params: dict) -> dict:
    """
    Format a human-readable summary of backtest results.

    Returns:
        dict with "summary" (str) and "error" (str | None)
    """
    try:
        m = backtest_result.get("metrics", {})
        trades = backtest_result.get("trades", [])

        lines = [
            "=" * 60,
            "  SPX INCLUSION + MOMENTUM  —  BACKTEST REPORT",
            "=" * 60,
            "",
            "STRATEGY PARAMETERS",
            f"  Lookback (momentum) : {strategy_params.get('lookback_days', 20)} trading days",
            f"  Momentum threshold  : {strategy_params.get('momentum_threshold', 0.0):.1%}",
            f"  Hold period         : {strategy_params.get('hold_days', 5)} calendar days post-effective",
            f"  Date range          : {strategy_params.get('start_date', 'N/A')} → {strategy_params.get('end_date', 'N/A')}",
            "",
            "PERFORMANCE METRICS",
            f"  Total return        : {m.get('total_return_pct', 'N/A')}%",
            f"  Number of trades    : {m.get('number_of_trades', 'N/A')}",
            f"  Win rate            : {m.get('win_rate_pct', 'N/A')}%",
            f"  Avg return / trade  : {m.get('avg_return_per_trade_pct', 'N/A')}%",
            f"  Std dev / trade     : {m.get('std_return_per_trade_pct', 'N/A')}%",
            f"  Per-trade Sharpe    : {m.get('per_trade_sharpe', 'N/A')}",
            f"  Per-trade Sortino   : {m.get('per_trade_sortino', 'N/A')}",
            f"  Max drawdown        : {m.get('max_drawdown_pct', 'N/A')}%",
            f"  Best trade          : +{m.get('best_trade', 'N/A')}%",
            f"  Worst trade         : {m.get('worst_trade', 'N/A')}%",
            "",
            "TOP 5 TRADES (by return)",
        ]

        top5 = sorted(trades, key=lambda t: t["gross_return"], reverse=True)[:5]
        for t in top5:
            lines.append(
                f"  {t['ticker']:<8} {t['entry_date']} → {t['exit_date']}  "
                f"{t['gross_return']*100:+.2f}%  (mom {t['momentum_at_signal']*100:+.1f}%)"
                if t.get("momentum_at_signal") is not None
                else f"  {t['ticker']:<8} {t['entry_date']} → {t['exit_date']}  "
                f"{t['gross_return']*100:+.2f}%"
            )

        lines += [
            "",
            "BOTTOM 5 TRADES (by return)",
        ]
        bot5 = sorted(trades, key=lambda t: t["gross_return"])[:5]
        for t in bot5:
            lines.append(
                f"  {t['ticker']:<8} {t['entry_date']} → {t['exit_date']}  "
                f"{t['gross_return']*100:+.2f}%"
            )

        lines.append("=" * 60)
        summary = "\n".join(lines)
        return {"summary": summary, "error": None}

    except Exception as exc:
        return {"summary": "", "error": str(exc)}


# ─── helpers ──────────────────────────────────────────────────────────────────


def _next_date(series: dict[str, float], after: str) -> str | None:
    """First date in series strictly after `after`."""
    candidates = sorted(d for d in series if d > after)
    return candidates[0] if candidates else None


def _on_or_after(series: dict[str, float], target: str) -> str | None:
    """First date in series >= target."""
    candidates = sorted(d for d in series if d >= target)
    return candidates[0] if candidates else None


def _max_drawdown(equity_values: list[float], initial: float) -> float:
    """Peak-to-trough max drawdown fraction."""
    all_vals = [initial] + equity_values
    peak = all_vals[0]
    max_dd = 0.0
    for v in all_vals:
        peak = max(peak, v)
        dd = (peak - v) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return max_dd
