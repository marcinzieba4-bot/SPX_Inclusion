"""
Announcement-Arb PnL Simulator
==============================

Simulates the implementable slice of the inclusion trade on real prices:

    ENTRY:  close of the first trading day AFTER the addition announcement
            (S&P announcements are published after the close)
    EXIT:   swept across trading-day offsets around the effective date to
            find the best exit; portfolio sim then uses the chosen rule
    HEDGE:  two versions - long-only (cash idle), and market-neutral
            (each position paired with an equal-notional short in SPY,
            i.e. "playing it vs SPX")

Data: the 205 real 2012-2025 addition events from honest_backtest.py
(data/verified/honest_backtest_results.json) and cached daily adjusted
closes (data/verified/prices/). Announcement dates are exact for 34
events (2020-2025) and proxied as effective-10 calendar days otherwise.

Costs: 15 bps per side on the stock; +2 bps per side for the SPY hedge.
Position size: 10% of NAV per event (daily-rebalanced fractional weight),
several events can overlap. No leverage; uninvested capital earns 0.
"""

import json
import math
import os
import statistics
from datetime import date, timedelta

from honest_backtest import PriceSeries, DATA

COST_STOCK_SIDE = 0.0015
COST_HEDGE_SIDE = 0.0002
POS_WEIGHT = 0.10
EXIT_OFFSETS = [-5, -3, -1, 0, 1, 2, 3, 5, 8, 12, 15, 20]   # trading days vs effective
ERAS = [(2012, 2015), (2016, 2019), (2020, 2021), (2022, 2025)]


def load_events():
    with open(os.path.join(DATA, "honest_backtest_results.json")) as f:
        payload = json.load(f)
    evs = []
    for e in payload["events"]:
        evs.append({
            "ticker": e["ticker"],
            "effective": date.fromisoformat(e["effective"]),
            "announce": date.fromisoformat(e["announce"]),
            "exact": e["exact"],
        })
    return evs


def tstat(xs):
    n = len(xs)
    if n < 3:
        return float("nan")
    sd = statistics.stdev(xs)
    return statistics.mean(xs) / (sd / math.sqrt(n)) if sd > 0 else float("nan")


# ── Per-event trade legs ─────────────────────────────────────────────────────

def build_trades(events, spy: PriceSeries):
    """For each event: entry index and per-exit-offset stock/SPY returns."""
    trades = []
    for e in events:
        ps = PriceSeries.load(e["ticker"])
        if ps is None:
            continue
        i_ann = ps.idx_on_or_before(e["announce"])
        i_eff = ps.idx_on_or_before(e["effective"])
        if i_ann is None or i_eff is None:
            continue
        i_in = i_ann + 1                      # first close after announcement
        if i_in >= len(ps.closes) or i_in > i_eff + min(EXIT_OFFSETS):
            pass
        legs = {}
        for off in EXIT_OFFSETS:
            j = i_eff + off
            if j <= i_in or j >= len(ps.closes):
                continue
            d_in, d_out = ps.dates[i_in], ps.dates[j]
            r_s = ps.closes[j] / ps.closes[i_in] - 1
            r_m = spy.ret(d_in, d_out)
            if r_m is None:
                continue
            net_long = (1 + r_s) * (1 - COST_STOCK_SIDE) ** 2 - 1
            net_hedged = ((1 + r_s) * (1 - COST_STOCK_SIDE) ** 2
                          / ((1 + r_m) * (1 + COST_HEDGE_SIDE) ** 2) - 1)
            legs[off] = {"raw": r_s, "spy": r_m,
                         "net_long": net_long, "net_hedged": net_hedged,
                         "d_in": d_in, "d_out": d_out}
        if legs:
            trades.append({**e, "i_in": i_in, "i_eff": i_eff, "legs": legs, "ps": ps})
    return trades


def exit_sweep(trades):
    """Mean/median/t/hit of the hedged net return per exit offset, all + by era."""
    sweep = {}
    for off in EXIT_OFFSETS:
        xs = [t["legs"][off]["net_hedged"] for t in trades if off in t["legs"]]
        if len(xs) < 10:
            continue
        row = {"n": len(xs), "mean": statistics.mean(xs),
               "median": statistics.median(xs), "t": tstat(xs),
               "hit": sum(1 for x in xs if x > 0) / len(xs)}
        for lo, hi in ERAS:
            era = [t["legs"][off]["net_hedged"] for t in trades
                   if off in t["legs"] and lo <= t["effective"].year <= hi]
            row[f"{lo}-{hi}"] = statistics.mean(era) if len(era) >= 5 else None
        sweep[off] = row
    return sweep


def event_path(trades, spy: PriceSeries, lo=-12, hi=20):
    """Mean cumulative SPY-adjusted return from entry, aligned on the
    effective date (day 0). Only counts an event on day d if entry <= d."""
    out = []
    for d in range(lo, hi + 1):
        vals = []
        for t in trades:
            ps, j = t["ps"], t["i_eff"] + d
            if j < t["i_in"] or j >= len(ps.closes):
                continue
            r_s = ps.closes[j] / ps.closes[t["i_in"]] - 1
            r_m = spy.ret(ps.dates[t["i_in"]], ps.dates[j])
            if r_m is None:
                continue
            vals.append((1 + r_s) / (1 + r_m) - 1)
        if len(vals) >= 20:
            out.append({"day": d, "mean": statistics.mean(vals),
                        "median": statistics.median(vals), "n": len(vals)})
    return out


# ── Daily portfolio simulation ───────────────────────────────────────────────

def daily_sim(trades, spy: PriceSeries, exit_off: int):
    """Daily NAV curves. Weight = 10%/event of NAV, daily-rebalanced.
    long  : events long, idle capital in cash (0%)
    neutral: events long vs equal-notional SPY short (net-of-costs)
    overlay: SPY 100% + the neutral book on top (portable alpha)
    spy   : benchmark"""
    d0, d1 = date(2012, 1, 1), date(2025, 12, 31)
    days = [d for d in spy.dates if d0 <= d <= d1]
    idx_of = {d: i for i, d in enumerate(days)}

    # per-day stock returns for active trades
    active = [[] for _ in days]   # list of (daily stock ret, daily spy ret)
    trade_log = []
    for t in trades:
        if exit_off not in t["legs"]:
            continue
        leg = t["legs"][exit_off]
        ps = t["ps"]
        j_out = t["i_eff"] + exit_off
        # entry cost on day i_in, exit cost on day j_out
        for j in range(t["i_in"] + 1, j_out + 1):
            d = ps.dates[j]
            if d not in idx_of:
                continue
            r_day = ps.closes[j] / ps.closes[j - 1] - 1
            k = idx_of[d]
            i_spy = spy.idx_on_or_before(d)
            r_spy_day = spy.closes[i_spy] / spy.closes[i_spy - 1] - 1
            cost = 0.0
            if j == t["i_in"] + 1:
                cost += COST_STOCK_SIDE + COST_HEDGE_SIDE
            if j == j_out:
                cost += COST_STOCK_SIDE + COST_HEDGE_SIDE
            active[k].append((r_day - cost, r_spy_day))
        trade_log.append({"ticker": t["ticker"], "in": str(leg["d_in"]),
                          "out": str(leg["d_out"]), "net_long": leg["net_long"],
                          "net_hedged": leg["net_hedged"], "exact": t["exact"],
                          "year": t["effective"].year})

    nav = {"long": [1.0], "neutral": [1.0], "overlay": [1.0], "spy": [1.0]}
    rets = {k: [] for k in nav}
    for k in range(1, len(days)):
        i_spy = spy.idx_on_or_before(days[k])
        r_spy = spy.closes[i_spy] / spy.closes[i_spy - 1] - 1
        acts = active[k]
        w = min(POS_WEIGHT, 0.5 / max(1, len(acts)))   # cap gross at 50%
        r_long = sum(w * rs for rs, _ in acts)
        r_neut = sum(w * (rs - rm) for rs, rm in acts)
        day_r = {"long": r_long, "neutral": r_neut,
                 "overlay": r_spy + r_neut, "spy": r_spy}
        for key in nav:
            rets[key].append(day_r[key])
            nav[key].append(nav[key][-1] * (1 + day_r[key]))
    return days, nav, rets, trade_log


def curve_stats(days, navs, rets):
    yrs = (days[-1] - days[0]).days / 365.25
    out = {}
    for key in navs:
        v = navs[key]
        r = rets[key]
        mu, sd = statistics.mean(r), statistics.pstdev(r)
        peak, mdd = v[0], 0.0
        for x in v:
            peak = max(peak, x)
            mdd = min(mdd, x / peak - 1)
        out[key] = {
            "total": v[-1] - 1,
            "cagr": v[-1] ** (1 / yrs) - 1,
            "vol": sd * math.sqrt(252),
            "sharpe": (mu / sd * math.sqrt(252)) if sd > 0 else 0.0,
            "maxdd": mdd,
        }
    return out


def run():
    spy = PriceSeries.load("SPY")
    events = load_events()
    trades = build_trades(events, spy)
    print(f"events with tradeable legs: {len(trades)}")

    # 1) exit sweep
    sweep = exit_sweep(trades)
    print("\nEXIT SWEEP - hedged net return per trade (long stock / short SPY)")
    print(f"  {'exit (td vs eff)':<18}{'n':>5}{'mean':>9}{'median':>9}{'t':>7}{'hit':>7}"
          f"{'12-15':>8}{'16-19':>8}{'20-21':>8}{'22-25':>8}")
    for off, r in sweep.items():
        def f(x): return f"{x * 100:+.2f}" if x is not None else "    -"
        print(f"  eff{off:+d}{'':<12}{r['n']:>5}{f(r['mean']):>9}{f(r['median']):>9}"
              f"{r['t']:>7.2f}{r['hit'] * 100:>6.0f}%"
              f"{f(r['2012-2015']):>8}{f(r['2016-2019']):>8}{f(r['2020-2021']):>8}{f(r['2022-2025']):>8}")

    best_off = max(sweep, key=lambda o: sweep[o]["mean"])
    print(f"\nbest exit by mean hedged net: effective{best_off:+d} trading days")

    # 2) event path
    path = event_path(trades, spy)

    # 3) daily portfolio sims (best exit and the report's default +3 for reference)
    days, nav, rets, trade_log = daily_sim(trades, spy, best_off)
    stats = curve_stats(days, nav, rets)
    print(f"\nDAILY PORTFOLIO SIM  (10% per event, gross cap 50%, exit eff{best_off:+d})")
    print(f"  {'book':<10}{'total':>9}{'CAGR':>8}{'vol':>7}{'Sharpe':>8}{'maxDD':>8}")
    for k in ("long", "neutral", "overlay", "spy"):
        s = stats[k]
        print(f"  {k:<10}{s['total'] * 100:>8.1f}%{s['cagr'] * 100:>7.2f}%"
              f"{s['vol'] * 100:>6.1f}%{s['sharpe']:>8.2f}{s['maxdd'] * 100:>7.1f}%")

    hh = [t["net_hedged"] for t in trade_log]
    gains = sum(x for x in hh if x > 0)
    losses = -sum(x for x in hh if x < 0)
    print(f"\n  trades {len(trade_log)}, hit {sum(1 for x in hh if x > 0) / len(hh) * 100:.0f}%, "
          f"avg {statistics.mean(hh) * 100:+.2f}%, best {max(hh) * 100:+.1f}%, worst {min(hh) * 100:+.1f}%, "
          f"profit factor {gains / losses:.2f}")

    # weekly downsample of curves for the report
    weekly = []
    for i, d in enumerate(days):
        if i % 5 == 0 or i == len(days) - 1:
            weekly.append({"date": str(d), "long": nav["long"][i],
                           "neutral": nav["neutral"][i],
                           "overlay": nav["overlay"][i], "spy": nav["spy"][i]})

    # yearly table for the neutral book
    yearly = {}
    for i in range(1, len(days)):
        y = days[i].year
        yearly.setdefault(y, {"neutral": 1.0, "overlay": 1.0, "spy": 1.0})
        for k in ("neutral", "overlay", "spy"):
            yearly[y][k] *= (1 + rets[k][i - 1])
    yearly = {y: {k: v - 1 for k, v in d.items()} for y, d in sorted(yearly.items())}

    payload = {
        "exit_sweep": sweep, "best_exit": best_off, "path": path,
        "curve_stats": stats, "weekly_nav": weekly, "yearly": yearly,
        "trade_log": trade_log, "n_trades": len(trade_log),
    }
    out = os.path.join(DATA, "announcement_arb_results.json")
    with open(out, "w") as f:
        json.dump(payload, f, indent=1, default=str)
    print(f"\nresults -> {out}")


if __name__ == "__main__":
    run()
