# Backtest Reliability Report — SPX Inclusion Momentum

**Scope:** `spx_inclusion_momentum.py` (backtest engine), `generate_report.py` (PDF/JSON report generator), `data/sp500_changes.csv.example`, `agent.py` / Telegram distribution layer.
**Question:** Are the reported results (CAGR +17.6% vs S&P 500 +10.8%, Sharpe 1.34, alpha +6.8pp/yr, 72.6% win rate) a reliable backtest?

## Verdict

**The backtest is not reliable. It is not a backtest at all — it is a simulation of its own assumptions.** No market price data is used anywhere in the pipeline. Every return in the system is either hand-typed into the source file or drawn from a random-number generator whose parameters *assume* the alpha the backtest then "finds." The reported outperformance is an input, not a result, and the code's prominent "survivorship-bias avoidance" and "look-ahead bias policy" claims are not implemented in any meaningful sense.

None of the headline numbers (CAGR, Sharpe, alpha, win rate, drawdown) should be used for investment decisions, capital allocation, or external communication as if they were empirical results.

---

## 1. There is no market data

- The 87 "historical additions" in `_ADDITIONS_RAW` (spx_inclusion_momentum.py:109) carry **hand-coded** momentum values and three hand-coded return legs each. The file itself admits they are "simulated" and "calibrated" (lines 95–98).
- The other ~3,400 candidates — **519 of the 540 trades (96%)** — are synthetic tickers (`CAND_2015Q2_017`-style) whose returns are Gaussian draws (lines 325–342).
- Nothing loads prices: no data files, no API calls, no price series. `requirements.txt` contains no data library.

A backtest's reliability rests on its data. Here the "data" layer is authored, so every downstream statistic is authored too.

## 2. The hand-coded "historical" returns are formulaic, not empirical

Probing the 87 addition rows statistically:

| Check | Result | What real data looks like |
|---|---|---|
| `ret_entry_to_announce / mom_12_1` | mean 0.063, stdev 0.007 (CV ≈ 11%) | essentially a fixed formula: entry return ≈ 6.3% × momentum |
| `mom_3m / mom_12_1` | 0.337 ± 0.016 | 3-month momentum is exactly ⅓ of 12-month momentum in every row — impossible in real prices |
| corr(momentum, announcement premium) | **+0.89** | empirically weak/noisy |
| Additions with a non-positive announcement→effective return | **0 of 87** | real inclusion events regularly fizzle; post-2015 literature (e.g. Greenwood & Sammon) finds the index-inclusion effect has shrunk toward zero |
| Additions with a non-positive total (entry→exit) return | **0 of 87** | zero losing inclusion trades in 14 years is not a plausible sample |

Every one of the 21 real-addition trades the strategy "selects" is profitable — a 100% win rate baked into the table.

## 3. The alpha is assumed in the synthetic return generator

For the 96% of trades that are synthetic, holding returns are generated (lines 317–334) as:

```
holding = 1.10 × SPX_quarterly + N(+0.008, 0.07)  [+ N(+0.008, 0.025) if mom12 > 0.30]
```

That is: **beta 1.1 to the index, plus +0.8pp/quarter of free idiosyncratic alpha, plus another +0.8pp/quarter "momentum bonus"** for high-momentum names. 75% of selected trades qualify for the bonus. Measured against the mean SPX quarter (+3.28%):

- Built-in expected edge for a selected synthetic name: **≈ +1.9pp per quarter** before the stop-loss.
- The stop-loss adds another **+0.32pp/quarter** for free (see §4).
- Total built-in edge ≈ **+2.25pp/quarter ≈ +9.3pp/yr — more than the entire reported alpha of +6.8pp/yr.**

The generator's momentum bonus also *is* the momentum effect the strategy claims to discover. The printed "insight" — "pure-momentum candidates contribute positive returns, confirming the momentum signal has standalone value" — merely reads the generator's parameter back out.

Seed sensitivity confirms the rest is noise: rerunning with seeds {7, 42, 99, 1337, 2024} gives annual alpha between **+6.0pp and +9.4pp** — roughly a third of the "edge" moves with the RNG seed.

## 4. Methodological defects (would invalidate it even with real data)

1. **Stop-loss on terminal return (look-ahead).** `max(adjusted_ret, -0.15)` (line 487) truncates the *end-of-quarter* return. A real stop triggers on the intra-period path and locks in losses on positions that later recover; this one only ever deletes left-tail outcomes after the fact. Untradeable, and worth +0.32pp/quarter here.
2. **Label leakage in the candidate pool.** The pool is constructed *from the known additions list*: real additions are injected each cycle with `eventually_added=True`, a guaranteed-positive announcement premium, exactly `eligibility_streak=2`, and (deflated) announcement-date market caps. There are no false positives (candidates the market expected but weren't added with a negative surprise), no deletions, and no added-then-crashed names. The "survivorship-bias avoidance" section describes controls the code does not have.
3. **Return legs summed, not compounded** (line 371) — small but systematically flattering at these magnitudes.
4. **Benchmark misalignment.** Strategy holding window (announcement −30d to +75d) is compared to calendar-quarter SPX returns; cycles are grouped by the *first* addition's announcement quarter; cash drag and position caps are ignored (cycle return = simple mean of trade returns).
5. **No costs.** Zero commissions, spread, slippage, market impact or borrow — for a strategy the code itself says has $50–200M capacity in less-liquid near-threshold names.
6. **Premium "decay" is a fudge factor**, an assumed 8%/yr erosion applied only to the inclusion leg (lines 467–471), not estimated from anything.
7. **Docs contradict the code.** Docstring/README say top *quintile* (20%), 4% × 25 positions, 2-factor signal, period "2012–2023"; the code uses top 15% (threshold 0.85), 5% × 20 positions, a 4-factor signal, and data through 2026Q1. `run()` uses seed 1337 while `agent.py` tools use the default 42 — the agent and the CLI report different numbers from the "same" backtest.

## 5. Even the factual scaffolding is wrong

Several "historical additions" are verifiably incorrect — some tickers were long-standing S&P 500 members on their claimed addition dates, others have the wrong period:

| Row claims | Reality (high confidence) |
|---|---|
| NFLX added Dec 2015 | S&P 500 member since Dec 2010 |
| TWTR added Nov 2013 | Joined June 2018; 2013-11-07 was Twitter's IPO date |
| CELG added Mar 2018 | Member since 2006 |
| EFX added Mar 2020 | Member since 1997 |
| SYMC added Jun 2014 | Member since 2003 |
| VIAB added Dec 2012 | Long-time member well before 2012 |
| CHK added Mar 2015 | Was a member being *removed* in 2018 |
| SMCI added Mar 2023 | Added March 2024 |
| VST added Sep 2025 | Added May 2024 |
| COIN added Dec 2025 | Added May 2025 |
| APO added Mar 2025 | Added December 2024 |
| DASH added Jun 2025 | Added March 2025 |
| HOOD added Mar 2026 | Added September 2025 |

`data/sp500_changes.csv.example` has the same problem (NVDA joined in 2001, TGT decades ago — not November 2020).

## 6. The reporting layer fabricates evidence and mislabels it

`generate_report.py` is the most serious reliability issue:

- `generate_trades()` **reverse-engineers 540 individual trade records from pre-set annual performance targets** — its own comment: *"Per-year calibration … Calibrated so portfolio return ≈ ANNUAL targets."* Win rates, win/loss sizes, tickers (randomly drawn per sector, deduplicated per quarter) and a capped "stop-loss budget" of exactly 25 are all chosen to reproduce the headline table.
- Each fabricated trade is given a **"PIT rationale"** and a "look-ahead bias policy" preamble asserting the entries used only point-in-time data — describing rigor that was never performed.
- The resulting investor-styled PDF and JSON are **uploaded to S3** (`s3://s3bucketmz/Strategies/`), and the Telegram bot broadcasts `/run` performance tables and entry-window alerts built on the same numbers, without a simulation disclaimer.

Whatever the intent (the engine's own risk notes do say "price paths use calibrated parameters"), the output artifacts present simulated, target-calibrated numbers with the trappings of an audited empirical backtest. If any of this reaches other people as decision material, that framing is materially misleading and should be corrected first.

## 7. What the results actually are — and are not

- ✅ A self-consistent **illustration** of how the strategy *would* perform **if** the announcement premium, momentum alpha and factor structure assumed in the generator were true.
- ❌ Evidence that the strategy has alpha, a Sharpe of 1.34, a 72.6% win rate, or any of the reported statistics.
- ❌ A basis for the Telegram entry-window alerts ("entry window open") — the timing logic is fine as a calendar, but the expected-return claims behind it are unsupported.

## 8. Recommendations

**Immediate (honesty of presentation)**
1. Relabel every output — CLI tables, PDF, JSON, Telegram messages, README — as *"simulation with assumed parameters"*; remove "look-ahead bias policy"/"survivorship-bias control" claims until controls actually exist; stop distributing the PDF as a backtest.
2. Delete or clearly quarantine `generate_trades()`; fabricated per-trade records with PIT rationales should not exist in any shareable artifact.

**To build a real backtest**
3. Source actual data: daily prices/total returns (e.g. CRSP, or at minimum a survivorship-aware vendor feed), point-in-time S&P 500 constituent lists and change announcements (S&P press releases), point-in-time fundamentals for the profitability/float screens.
4. Build the candidate universe from PIT screens only — never from the additions list — and let real additions/non-additions emerge from history, including negative-surprise cases and deletions.
5. Compute returns from prices over the actual holding windows; implement the stop-loss on the daily path; compound, don't sum.
6. Charge realistic costs (spread + impact scaled to ADV) and model cash. Compare against a beta-matched benchmark over identical windows.
7. Validate: walk-forward or at least strict in-sample/out-of-sample split, sensitivity to the 4 factor weights and the 0.85 threshold (currently unjustified point choices), and confidence intervals on alpha (57 quarterly observations is a small sample even when real).
8. Sanity-check against the literature: post-2015 index-inclusion effects are estimated near zero, so a persistent +7–20pp annual alpha should be treated as a red flag, not a feature.

---

*Analysis performed on branch `claude/backtest-reliability-report-hwc29b`; all figures reproduced by executing the repository code as-is (Python 3, no modifications) plus read-only diagnostic scripts.*
