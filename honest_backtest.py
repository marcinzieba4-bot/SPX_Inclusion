"""
SPX Inclusion Momentum — Honest Backtest
=========================================

A from-scratch replacement for the simulated engine in
`spx_inclusion_momentum.py`, built on REAL data and explicit assumptions.

What is real (measured, not assumed)
------------------------------------
  * Addition events: all S&P 500 additions 2012-2025 from Wikipedia's
    "List of S&P 500 companies" changes table (Jan-2026 revision),
    data/verified/sp500_additions_2012_2025.csv
  * Exact announcement dates for 34 events 2020-2025 (curated, see
    data/verified/announcement_dates_known.csv)
  * Daily adjusted closes from Yahoo Finance chart API, cached in
    data/verified/prices/ (fetched 2026-08; adjusted for splits/dividends)
  * SPY as the market benchmark

What is honestly simulated (Part C only) and why
------------------------------------------------
  The full pre-announcement strategy needs a point-in-time eligibility
  universe (every non-member large cap with PIT fundamentals) which is not
  available without a commercial PIT database. Part C therefore runs a
  Monte Carlo of the strategy where:
    * market returns are bootstrapped from REAL SPY quarterly returns
    * captured-addition payoffs are bootstrapped from the REAL event-study
      distribution measured in Part A (era-matched, post-2020 by default)
    * the capture rate and the momentum tilt of non-added holdings are
      explicit SCENARIO PARAMETERS, spanning pessimistic -> optimistic,
      with the base case set to "no free alpha" (efficient-market default)
  Nothing in Part C bakes in positive idiosyncratic alpha.

Known limitations (disclosed, not hidden)
-----------------------------------------
  * Yahoo has dropped most delisted tickers -> data coverage is ~55% in
    2012-2015 rising to ~95% in 2022-2025. Missing names are listed and
    counted; era statistics are computed only within covered names.
  * Where the exact announcement date is unknown, the announcement is
    proxied as effective_date - 10 calendar days (the standard quarterly
    cycle gap is 10-17 days); the proxy is validated against the 34
    exact-date events.
  * No intraday data: entries/exits are at daily closes, one day after
    the announcement (announcements are published after market close).
"""

import json
import math
import os
import statistics
import random
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "verified")
PRICES = os.path.join(DATA, "prices")

TRADING_DAYS_12M = 252
SKIP_DAYS = 21          # skip most recent month in momentum (reversal)
TRADING_DAYS_3M = 63
MIN_HISTORY = TRADING_DAYS_12M + SKIP_DAYS   # strategy's 12-month listing screen
ANN_PROXY_CAL_DAYS = 10     # proxy: announcement ~10 cal days before effective
ENTRY_CAL_DAYS_BEFORE_ANN = 30
COST_PER_SIDE = 0.0015      # 15 bps per side, liquid large caps
ERAS = [(2012, 2015), (2016, 2019), (2020, 2021), (2022, 2025)]


# ── Price store ──────────────────────────────────────────────────────────────

class PriceSeries:
    def __init__(self, dates: list[date], closes: list[float]):
        self.dates = dates
        self.closes = closes

    @classmethod
    def load(cls, ticker: str):
        path = os.path.join(PRICES, ticker.replace(".", "_") + ".csv")
        if not os.path.exists(path):
            return None
        df = pd.read_csv(path, parse_dates=["date"])
        return cls([d.date() for d in df["date"]], list(df["adjclose"]))

    def idx_on_or_before(self, d: date):
        """Index of last trading day <= d, or None."""
        i = bisect_right(self.dates, d) - 1
        return i if i >= 0 else None

    def ret(self, d1: date, d2: date):
        """Close-to-close return from last close <= d1 to last close <= d2."""
        i, j = self.idx_on_or_before(d1), self.idx_on_or_before(d2)
        if i is None or j is None or j <= i:
            return None
        return self.closes[j] / self.closes[i] - 1

    def ret_idx(self, i: int, j: int):
        if i < 0 or j >= len(self.closes) or j <= i:
            return None
        return self.closes[j] / self.closes[i] - 1


def era_of(y: int):
    for lo, hi in ERAS:
        if lo <= y <= hi:
            return f"{lo}-{hi}"
    return "other"


# ── Part A: event study on real additions ────────────────────────────────────

@dataclass
class EventResult:
    ticker: str
    effective: date
    announce: date          # exact if known, else proxy
    announce_exact: bool
    entry: date
    mom_12_1: float
    mom_3m: float
    spy_12m: float          # market return over the same momentum window
    r_pre: float            # entry -> announce close (raw)
    r_ann: float            # announce close -> effective close (raw)
    r_post: float           # effective close -> +3 trading days (raw)
    r_full: float           # entry -> +3 trading days (raw)
    ab_pre: float           # same windows, SPY-adjusted (abnormal)
    ab_ann: float
    ab_post: float
    ab_full: float


def run_event_study(spy: PriceSeries, verbose=True):
    events = pd.read_csv(os.path.join(DATA, "sp500_additions_2012_2025.csv"),
                         parse_dates=["effective_date"])
    known = pd.read_csv(os.path.join(DATA, "announcement_dates_known.csv"),
                        parse_dates=["announce_date", "effective_date"])
    known_map = {(r.ticker, r.effective_date.date()): r.announce_date.date()
                 for r in known.itertuples()}

    results: list[EventResult] = []
    n_missing_price, n_short_history = 0, 0
    missing_by_era: dict[str, int] = {}
    total_by_era: dict[str, int] = {}

    for row in events.itertuples():
        eff = row.effective_date.date()
        era = era_of(eff.year)
        total_by_era[era] = total_by_era.get(era, 0) + 1

        ps = PriceSeries.load(row.ticker)
        if ps is None:
            n_missing_price += 1
            missing_by_era[era] = missing_by_era.get(era, 0) + 1
            continue

        ann = known_map.get((row.ticker, eff))
        exact = ann is not None
        if ann is None:
            ann = eff - timedelta(days=ANN_PROXY_CAL_DAYS)
        entry = ann - timedelta(days=ENTRY_CAL_DAYS_BEFORE_ANN)

        i_entry = ps.idx_on_or_before(entry)
        if i_entry is None or i_entry < MIN_HISTORY:
            n_short_history += 1      # the strategy's own listing screen
            continue

        i_eff = ps.idx_on_or_before(eff)
        if i_eff is None or i_eff + 3 >= len(ps.closes):
            continue
        exit_date = ps.dates[i_eff + 3]

        mom_12_1 = ps.ret_idx(i_entry - TRADING_DAYS_12M - SKIP_DAYS, i_entry - SKIP_DAYS)
        mom_3m = ps.ret_idx(i_entry - TRADING_DAYS_3M - SKIP_DAYS, i_entry - SKIP_DAYS)
        spy_12m = spy.ret(ps.dates[i_entry - TRADING_DAYS_12M - SKIP_DAYS],
                          ps.dates[i_entry - SKIP_DAYS])

        r_pre = ps.ret(entry, ann)
        r_ann = ps.ret(ann, eff)
        r_post = ps.ret(eff, exit_date)
        r_full = ps.ret(entry, exit_date)
        m_pre = spy.ret(entry, ann)
        m_ann = spy.ret(ann, eff)
        m_post = spy.ret(eff, exit_date)
        m_full = spy.ret(entry, exit_date)
        if None in (mom_12_1, mom_3m, r_pre, r_ann, r_post, r_full,
                    m_pre, m_ann, m_post, m_full, spy_12m):
            continue

        def ab(r, m):
            return (1 + r) / (1 + m) - 1

        results.append(EventResult(
            ticker=row.ticker, effective=eff, announce=ann, announce_exact=exact,
            entry=entry, mom_12_1=mom_12_1, mom_3m=mom_3m, spy_12m=spy_12m,
            r_pre=r_pre, r_ann=r_ann, r_post=r_post, r_full=r_full,
            ab_pre=ab(r_pre, m_pre), ab_ann=ab(r_ann, m_ann),
            ab_post=ab(r_post, m_post), ab_full=ab(r_full, m_full),
        ))

    if verbose:
        print(f"\nAdditions 2012-2025 (Wikipedia): {len(events)}")
        print(f"  dropped, no price data (delisted etc.):  {n_missing_price}")
        print(f"  dropped, <12m trading history (screen):  {n_short_history}")
        print(f"  analyzable events:                       {len(results)}")
        print("  coverage by era (events with price data / total):")
        for lo, hi in ERAS:
            era = f"{lo}-{hi}"
            tot = total_by_era.get(era, 0)
            miss = missing_by_era.get(era, 0)
            print(f"    {era}: {tot - miss}/{tot}")
    return results


def tstat(xs):
    n = len(xs)
    if n < 3:
        return float("nan")
    sd = statistics.stdev(xs)
    return statistics.mean(xs) / (sd / math.sqrt(n)) if sd > 0 else float("nan")


def summarize_events(results: list[EventResult]):
    def block(label, evs):
        if len(evs) < 3:
            return None
        out = {"label": label, "n": len(evs)}
        for f in ("mom_12_1", "spy_12m", "ab_pre", "ab_ann", "ab_post", "ab_full"):
            xs = [getattr(e, f) for e in evs]
            out[f] = {"mean": statistics.mean(xs), "median": statistics.median(xs),
                      "t": tstat(xs), "pos": sum(1 for x in xs if x > 0) / len(xs)}
        return out

    blocks = [block("ALL 2012-2025", results)]
    for lo, hi in ERAS:
        era = f"{lo}-{hi}"
        blocks.append(block(era, [e for e in results if era_of(e.effective.year) == era]))
    blocks.append(block("exact-announce only (2020-2025)",
                        [e for e in results if e.announce_exact]))
    return [b for b in blocks if b]


def print_event_summary(blocks):
    print("\n" + "─" * 100)
    print("  PART A · EVENT STUDY ON REAL S&P 500 ADDITIONS (SPY-adjusted abnormal returns)")
    print("─" * 100)
    print("  windows: pre = entry(T-30d before announce) -> announce | ann = announce -> effective"
          "\n           post = effective -> +3 trading days           | full = entry -> +3td after effective\n")
    hdr = f"  {'sample':<34}{'n':>4} {'mom12-1':>9} {'SPY 12m':>9} {'pre':>8} {'ann':>8} {'post':>8} {'full':>8} {'t(full)':>8} {'hit%':>6}"
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for b in blocks:
        print(f"  {b['label']:<34}{b['n']:>4} "
              f"{b['mom_12_1']['median'] * 100:>8.1f}% {b['spy_12m']['median'] * 100:>8.1f}% "
              f"{b['ab_pre']['mean'] * 100:>7.2f}% {b['ab_ann']['mean'] * 100:>7.2f}% "
              f"{b['ab_post']['mean'] * 100:>7.2f}% {b['ab_full']['mean'] * 100:>7.2f}% "
              f"{b['ab_full']['t']:>8.2f} {b['ab_full']['pos'] * 100:>5.0f}%")
    print()


# ── Part B: implementable announcement-arb backtest ──────────────────────────

def run_announcement_arb(results: list[EventResult], spy: PriceSeries):
    """
    Fully implementable rule, no foresight needed:
      * On announcement (public info), buy at the NEXT day's close
      * Sell at the close 3 trading days after the effective date
      * 15 bps costs per side
    Reported per event and aggregated per quarter vs SPY over the same days.
    """
    trades = []
    for e in results:
        ps = PriceSeries.load(e.ticker)
        i_ann = ps.idx_on_or_before(e.announce)
        i_eff = ps.idx_on_or_before(e.effective)
        if i_ann is None or i_eff is None or i_ann + 1 >= len(ps.closes) or i_eff + 3 >= len(ps.closes):
            continue
        d_in, d_out = ps.dates[i_ann + 1], ps.dates[i_eff + 3]
        r = ps.ret(d_in, d_out)
        m = spy.ret(d_in, d_out)
        if r is None or m is None:
            continue
        net = (1 + r) * (1 - COST_PER_SIDE) ** 2 - 1
        trades.append({"ticker": e.ticker, "in": d_in, "out": d_out,
                       "gross": r, "net": net, "spy": m,
                       "ab_net": (1 + net) / (1 + m) - 1,
                       "exact": e.announce_exact,
                       "year": e.effective.year})
    return trades


def print_arb_summary(trades):
    print("─" * 100)
    print("  PART B · IMPLEMENTABLE BACKTEST: buy close after announcement, sell effective +3td (net of 30bps)")
    print("─" * 100)

    def line(label, ts):
        if len(ts) < 3:
            return
        ab = [t["ab_net"] for t in ts]
        print(f"  {label:<34}{len(ts):>4} trades   mean net-of-SPY {statistics.mean(ab) * 100:+6.2f}%   "
              f"median {statistics.median(ab) * 100:+6.2f}%   t {tstat(ab):5.2f}   hit {sum(1 for x in ab if x > 0) / len(ab) * 100:3.0f}%")

    line("ALL events (proxy+exact dates)", trades)
    for lo, hi in ERAS:
        line(f"{lo}-{hi}", [t for t in trades if lo <= t["year"] <= hi])
    line("exact announce dates only", [t for t in trades if t["exact"]])
    print()


# ── Part C: scenario Monte Carlo of the full pre-positioning strategy ────────

@dataclass
class Scenario:
    name: str
    capture_rate: float        # P(an addition that occurs is already held)
    mom_tilt_q: float          # quarterly idiosyncratic drift of non-added holdings
    note: str


SCENARIOS = [
    Scenario("pessimistic", 0.10, -0.0050,
             "committee unpredictable; long-only momentum tilt loses to costs/crowding"),
    Scenario("base", 0.30, 0.0000,
             "capture at pool-share odds (20 of ~60 pool) + small screen edge; zero free alpha"),
    Scenario("optimistic", 0.50, +0.0050,
             "momentum/mcap screens genuinely predict committee choices; mild positive tilt"),
]

N_POSITIONS = 20
BETA = 1.15                    # high-momentum large/mid caps
IDIO_VOL_Q = 0.14              # quarterly idiosyncratic vol per stock
COST_DRAG_Q = 0.0035           # 20 pos, ~85% quarterly turnover, 30bps round trip + slippage
N_PATHS = 5000
N_QUARTERS = 56                # 14 years


def spy_quarterly_returns(spy: PriceSeries):
    rets = []
    for y in range(2012, 2026):
        for q in range(4):
            d1 = date(y, q * 3 + 1, 1) - timedelta(days=1)
            d2 = (date(y + 1, 1, 1) if q == 3 else date(y, q * 3 + 4, 1)) - timedelta(days=1)
            r = spy.ret(d1, d2)
            if r is not None:
                rets.append(r)
    return rets


def run_monte_carlo(results: list[EventResult], spy: PriceSeries,
                    organic_adds_per_q: float, seed=20260828):
    """Scenario simulation. All payoff distributions are bootstrapped from
    measured data; only capture_rate and mom_tilt_q are assumptions."""
    mkt_pool = spy_quarterly_returns(spy)
    # addition payoff pool: post-2020 SPY-adjusted full-window returns (today's regime)
    add_pool = [e.ab_full for e in results if e.effective.year >= 2020]
    rng = random.Random(seed)
    out = {}
    for sc in SCENARIOS:
        cagrs, alphas, sharpes, maxdds = [], [], [], []
        for _ in range(N_PATHS):
            nav, bench = 1.0, 1.0
            peak, maxdd = 1.0, 0.0
            q_excess = []
            for _q in range(N_QUARTERS):
                mkt = rng.choice(mkt_pool)
                n_adds = min(rng_poisson(rng, organic_adds_per_q), N_POSITIONS)
                n_cap = sum(1 for _ in range(n_adds) if rng.random() < sc.capture_rate)
                rets = []
                for _k in range(n_cap):
                    # captured addition: market + measured abnormal event return
                    rets.append(mkt * BETA + rng.choice(add_pool))
                for _k in range(N_POSITIONS - n_cap):
                    rets.append(mkt * BETA + sc.mom_tilt_q + rng.gauss(0, IDIO_VOL_Q))
                port = statistics.mean(rets) - COST_DRAG_Q
                nav *= (1 + port)
                bench *= (1 + mkt)
                q_excess.append(port - 0.005)
                peak = max(peak, nav)
                maxdd = min(maxdd, nav / peak - 1)
            yrs = N_QUARTERS / 4
            cagr = nav ** (1 / yrs) - 1
            cagrs.append(cagr)
            alphas.append(cagr - (bench ** (1 / yrs) - 1))
            mu, sd = statistics.mean(q_excess), statistics.pstdev(q_excess)
            sharpes.append(mu / sd * 2 if sd > 0 else 0)
            maxdds.append(maxdd)

        def pct(xs, p):
            xs = sorted(xs)
            return xs[int(p * (len(xs) - 1))]

        out[sc.name] = {
            "capture_rate": sc.capture_rate, "mom_tilt_q": sc.mom_tilt_q, "note": sc.note,
            "cagr_med": statistics.median(cagrs),
            "alpha_med": statistics.median(alphas),
            "alpha_p5": pct(alphas, 0.05), "alpha_p95": pct(alphas, 0.95),
            "p_alpha_pos": sum(1 for a in alphas if a > 0) / len(alphas),
            "sharpe_med": statistics.median(sharpes),
            "maxdd_med": statistics.median(maxdds),
        }
    return out


def rng_poisson(rng: random.Random, lam: float) -> int:
    # Knuth
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1


def print_mc_summary(mc, organic_adds_per_q):
    print("─" * 100)
    print("  PART C · SCENARIO MONTE CARLO OF THE FULL PRE-POSITIONING STRATEGY (14y horizon)")
    print("─" * 100)
    print(f"  bootstrapped from real SPY quarters and real post-2020 addition payoffs;"
          f" ~{organic_adds_per_q:.1f} screenable adds/quarter")
    print(f"  {'scenario':<13}{'capture':>8}{'tilt/q':>8}{'med CAGR':>10}{'med alpha':>10}"
          f"{'alpha 5-95%':>16}{'P(alpha>0)':>11}{'Sharpe':>8}{'maxDD':>8}")
    for name, d in mc.items():
        print(f"  {name:<13}{d['capture_rate'] * 100:>7.0f}%{d['mom_tilt_q'] * 100:>+7.2f}%"
              f"{d['cagr_med'] * 100:>9.1f}%{d['alpha_med'] * 100:>+9.1f}pp"
              f"   {d['alpha_p5'] * 100:>+5.1f}..{d['alpha_p95'] * 100:>+5.1f}pp"
              f"{d['p_alpha_pos'] * 100:>10.0f}%{d['sharpe_med']:>8.2f}{d['maxdd_med'] * 100:>7.1f}%")
    print()


# ── Entry point ──────────────────────────────────────────────────────────────

def run():
    spy = PriceSeries.load("SPY")
    if spy is None:
        raise SystemExit("SPY prices missing - run the fetcher first")

    print("═" * 100)
    print("  SPX INCLUSION MOMENTUM — HONEST BACKTEST (real events, real prices, explicit assumptions)")
    print("═" * 100)

    results = run_event_study(spy)
    blocks = summarize_events(results)
    print_event_summary(blocks)

    trades = run_announcement_arb(results, spy)
    print_arb_summary(trades)

    # organic screenable additions/quarter measured from the analyzable sample
    organic_adds_per_q = len(results) / N_QUARTERS
    mc = run_monte_carlo(results, spy, organic_adds_per_q)
    print_mc_summary(mc, organic_adds_per_q)

    payload = {
        "event_blocks": blocks,
        "arb_trades": [{**t, "in": str(t["in"]), "out": str(t["out"])} for t in trades],
        "monte_carlo": mc,
        "n_events": len(results),
        "events": [{"ticker": e.ticker, "effective": str(e.effective),
                    "announce": str(e.announce), "exact": e.announce_exact,
                    "mom_12_1": e.mom_12_1, "spy_12m": e.spy_12m,
                    "ab_pre": e.ab_pre, "ab_ann": e.ab_ann,
                    "ab_post": e.ab_post, "ab_full": e.ab_full} for e in results],
    }
    out_path = os.path.join(DATA, "honest_backtest_results.json")
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=1, default=str)
    print(f"  results written -> {out_path}")
    print("═" * 100)


if __name__ == "__main__":
    run()
