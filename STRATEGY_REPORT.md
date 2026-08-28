# SPX Inclusion Momentum — Strategy Report (Honest Backtest)

**Date:** 2026-08-28 · **Engine:** `honest_backtest.py` · **Data:** real S&P 500 addition events (Wikipedia changes table) + real daily adjusted prices (Yahoo Finance) + SPY benchmark
**Replaces:** the simulated results of `spx_inclusion_momentum.py` (see `BACKTEST_RELIABILITY_REPORT.md` for why those were invalid)

---

## Executive summary

Rebuilt from scratch on **205 real, analyzable S&P 500 additions (2012–2025)** with real prices, the strategy's core ideas survive — but at a fraction of the previously claimed size, and with the honest uncertainty made visible:

| Claim (old simulated engine) | Measured reality (this backtest) |
|---|---|
| Additions are high-momentum stocks | **Confirmed.** Median 12-1 momentum of added names at entry: **+35%** vs SPY +15% over the same windows (2022–25: +49% vs +16%) |
| Big, reliable inclusion premium (~6–12% per event, never negative) | **Partly real, regime-dependent.** Full-window abnormal return (entry 30d pre-announcement → effective +3td): mean **+4.9%** (t=5.0), but **63% hit rate, not 100%** — and it was ≈0 in 2016–2019 |
| +6.8pp/yr alpha, Sharpe 1.34 | **Base-case estimate: +2.6pp/yr alpha** (5–95%: −0.8 to +6.1pp), Sharpe ≈ 0.94 vs SPY's 0.92. The old numbers required assumptions the data does not support |
| Premium decaying since 2016 | **Backwards.** The premium *collapsed* in 2016–2019 and **revived strongly in 2022–2025** (mean abnormal +11.3%, t=5.1, 82% hit rate) |

**Bottom line:** this is a plausible small-alpha satellite strategy, not a Sharpe-1.3 machine. Its economics hinge on one number no backtest can fully pin down: the **capture rate** — how often the pre-positioned portfolio actually holds the names the committee adds. At realistic capture (~30%), expected alpha is ~+2–3pp/yr with meaningful downside scenarios; the edge is real but small, recent, and concentrated in the pre-announcement + overnight windows.

---

## 1. What was measured, on what data

- **Events:** all 289 S&P 500 additions 2012–2025 from Wikipedia's *List of S&P 500 companies* changes table (Jan-2026 revision) — `data/verified/sp500_additions_2012_2025.csv`. 49 dropped (no price data — mostly delisted/acquired), 35 dropped by the strategy's own ≥12-months-listed screen (spin-offs, fresh IPOs) → **205 analyzable events**.
- **Announcement dates:** exact for 34 events 2020–2025 (curated, `data/verified/announcement_dates_known.csv`); proxied as effective − 10 calendar days otherwise (standard quarterly gap is 10–17 days; proxy validated against the exact subsample).
- **Prices:** daily split/dividend-adjusted closes, cached in `data/verified/prices/` (238 tickers + SPY).
- **Windows** (all SPY-adjusted): **pre** = entry (30d before announcement) → announcement · **ann** = announcement → effective · **post** = effective → +3 trading days · **full** = entry → exit.

## 2. Part A — Event study: what additions actually do

| Sample | n | med. mom 12-1 | pre | ann | post | full | t(full) | hit% |
|---|---|---|---|---|---|---|---|---|
| **All 2012–2025** | 205 | +35% | +2.6% | +2.2% | +0.1% | **+4.9%** | 5.0 | 63% |
| 2012–2015 | 45 | +32% | +1.3% | +1.3% | +0.5% | +3.1% | 2.2 | 60% |
| 2016–2019 | 75 | +28% | +1.5% | −0.1% | +0.1% | **+1.3%** | 1.3 | 52% |
| 2020–2021 | 29 | +70% | +2.5% | +3.8% | −1.5% | +4.5% | 1.2 | 59% |
| 2022–2025 | 56 | +49% | +5.1% | +5.3% | +0.5% | **+11.3%** | 5.1 | 82% |
| Exact-announce only (2020–25) | 33 | +79% | +5.6% | +7.4% | −0.3% | +12.9% | 3.5 | 76% |

Key readings:

1. **The momentum premise is genuinely true.** Added names carry roughly 2–3× the market's trailing 12-month return at entry. A momentum screen on the eligible pool is pointing in the right direction.
2. **The "index effect is dead" literature is visible in the data** — 2016–2019 additions earned nothing abnormal. What the old engine modeled as gentle 8%/yr decay was actually a collapse…
3. **…followed by a revival.** 2022–2025 additions outperformed SPY by +11.3% on average over a ~7-week window, with an 82% hit rate — driven by the mega-momentum adds (SMCI +69%, APP +57%, COIN +42%, TPL +36% abnormal) but positive well beyond them. Losers exist too (WSM −17%, WBD −15%): the 100%-win-rate world of the old engine is fiction.
4. **The pop is front-loaded.** On the 34 exact-date events, the mean **overnight move on the announcement is +4.5%** (COIN +24%, SMCI +19%, HOOD +16%). Whoever is not positioned before the press release misses the largest single slice. The post-effective window contributes ≈0 — exit timing (+3d) costs nothing but adds nothing.
5. **Important honesty note:** the pre/full windows are measured *conditional on the stock being added* — they are the payoff **if captured**, not an implementable return. Implementability is exactly what Parts B and C price in.

## 3. Part B — The implementable slice (no foresight needed)

Rule: buy at the close **after** the public announcement, sell at effective +3 trading days, 30bps round trip.

| Sample | n | mean net-of-SPY | median | t | hit% |
|---|---|---|---|---|---|
| All 2012–2025 | 205 | **+1.17%** | +0.47% | 2.3 | 54% |
| 2016–2019 | 75 | −0.15% | −0.64% | −0.3 | 43% |
| 2022–2025 | 56 | **+2.66%** | +1.82% | 2.8 | 66% |
| Exact dates only | 33 | +1.79% | +1.44% | 0.9 | 61% |

The pure announcement-arb is worth roughly **+1.2% per event net** — real but thin, dead for four years mid-sample, and currently (2022–25) worth ~+2.7%/event. With ~4 events/quarter at, say, 5% position sizes, that's ~**+0.2–0.5pp/quarter** of portfolio alpha in the recent regime. This is the floor the strategy earns *without* any predictive skill.

## 4. Part C — The full pre-positioning strategy, priced honestly

The full strategy (hold 20 momentum-screened eligibles a month before each cycle) cannot be backtested exactly without a point-in-time eligibility universe, so it is priced as a **scenario Monte Carlo where everything measurable is bootstrapped from real data** (real SPY quarters 2012–2025; real post-2020 addition payoffs from Part A) and the two unknowables are explicit dials:

- **Capture rate** — P(a given addition is already in the book). Pool ≈ 60 names, book = 20 → blind odds ≈ 33%; the momentum profile in Part A justifies at-or-slightly-above blind odds.
- **Momentum tilt** — idiosyncratic drift of the ~19 holdings/quarter that are *not* added. Base case **zero** (no free alpha), which the old engine set to +1.6pp/quarter.

14-year horizon, 5,000 paths, 20 positions, beta 1.15, 14% idio vol, 35bps/quarter cost drag, ~3.7 screenable additions/quarter (measured):

| Scenario | Capture | Tilt/q | Med CAGR | Med alpha | Alpha 5–95% | P(alpha>0) | Sharpe | MaxDD |
|---|---|---|---|---|---|---|---|---|
| Pessimistic | 10% | −0.50% | 14.0% | **−1.1pp** | −4.3 … +2.4 | 31% | 0.75 | −29% |
| **Base** | 30% | 0.00% | 17.7% | **+2.6pp** | −0.8 … +6.1 | **89%** | 0.94 | −26% |
| Optimistic | 50% | +0.50% | 21.2% | **+6.2pp** | +2.6 … +9.9 | 100% | 1.13 | −25% |

For reference, SPY itself did **14.9% CAGR, Sharpe 0.92** over the same period. Read that table plainly:

- The old engine's +6.8pp/yr is this framework's **optimistic-scenario median** — it requires the screens to catch *half* of all additions *and* a free momentum tilt.
- The defensible base case is **+2.6pp/yr with an 11% chance of no alpha at all**, and a risk profile (vol, drawdown) slightly worse than the index.
- Even the base case leans on the **post-2020 payoff regime persisting**. Feed it 2016–2019 payoffs and the strategy is an index tracker with extra costs.

## 5. Assumptions register (everything not measured)

| # | Assumption | Value | Basis |
|---|---|---|---|
| 1 | Announcement proxy (pre-2020 events) | effective − 10 cal days | S&P quarterly cycle gap 10–17d; validated on 34 exact events |
| 2 | Costs | 15bps/side per event; 35bps/quarter portfolio drag | liquid large/mid caps, quarterly ~85% turnover |
| 3 | Portfolio beta | 1.15 | high-momentum large/mid caps |
| 4 | Idiosyncratic vol | 14%/quarter per name | typical single-stock idio risk |
| 5 | Capture rate | 10% / 30% / 50% | blind odds ≈ 33% (20 of ~60); scenario dial |
| 6 | Momentum tilt of non-added names | −0.5% / 0 / +0.5% per quarter | zero = efficient-market default; ±0.5% spans published long-only momentum estimates net of costs |
| 7 | Addition payoff distribution | bootstrap of real post-2020 events | current regime; the key regime-risk assumption |
| 8 | Screenable additions/quarter | 3.7 (measured) | 205 events / 56 quarters |

## 6. Limitations

1. **Survivorship in the data source.** Yahoo no longer serves 49 delisted tickers (17% of events, concentrated 2012–2019). Names that were added and later failed (SIVB, FRC, SBNY…) are missing, which biases the early-era event stats **upward** — reinforcing, not weakening, the conclusion that the pre-2020 edge was thin.
2. **No point-in-time eligibility universe** — hence Part C is scenario pricing, not a trade-by-trade backtest. A CRSP/Compustat PIT build is the upgrade path.
3. **Proxy announcement dates pre-2020** blur the pre/ann split (not the full-window totals) for those eras.
4. **Closes only, no intraday**: the implementable backtest enters at the close ~18–24h after the announcement; a faster desk enters at the open and captures more.
5. **Quarterly-sampled drawdowns** understate true intra-quarter drawdowns for both strategy and benchmark.
6. **14 years ≈ 56 quarters** is a small sample for annual-alpha inference; the 2022–25 revival is 4 years of data and may itself be a regime.
7. Committee discretion, capacity (~$50–200M realistic), and crowding remain unmodeled — all push realized results toward the pessimistic column.

## 7. Practical conclusions

1. **Trade the revival, respect the regime.** The measurable edge today is: (a) pre-position momentum-screened eligibles into quarterly cycles to capture the +4.5% average overnight pop and the pre-announcement drift; (b) the announcement-arb tail (+2.7%/event net, 2022–25) as the implementable floor. Track era-rolling event stats — if the 2016–2019 pattern returns (hit rate → 50%, ann window → 0), the strategy has no engine.
2. **Size it as a satellite.** Base-case +2.6pp/yr alpha on index-like risk justifies a sleeve, not a flagship. The 5th percentile is negative.
3. **The capture rate is the research agenda.** Every point of capture between 10% and 50% is worth ~18bps/yr of alpha. Improving the screen (market-cap gap to smallest member, float, profitability streak — the committee's actual published criteria) matters more than anything else in the stack.
4. **Retire the simulated engine's numbers everywhere** (Telegram `/run`, PDF, README): report these measured figures with their uncertainty instead.

---

*Reproduce: `python3 honest_backtest.py` (offline — uses cached prices). Full per-event data in `data/verified/honest_backtest_results.json`.*

---

## Addendum — Announcement-trade PnL simulation (`announcement_arb.py`)

Simulated the implementable trade (entry: first close after the announcement) on all 205 events with an exit sweep and an SPX-hedged version. Headline results: best exit **effective +3 trading days** (eff+0…+3 is one plateau; the edge fully decays by eff+15); avg **+1.13% net per hedged trade** (54% hit, profit factor 1.56); market-neutral book **+25.4% total** at 2.9% vol, max DD −6.8%, positive 10/14 years incl. **+3.5% in 2022** (SPY −18%); as an overlay on SPY it adds **+1.9pp/yr** (16.6% vs 14.7% CAGR, Sharpe 0.99 vs 0.91). Full trade log and curves: `data/verified/announcement_arb_results.json`.
