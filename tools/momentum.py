"""
Momentum signal calculations for the SPX inclusion strategy.

A stock passes the momentum filter if its recent price return
exceeds the threshold — we only ride stocks already moving up.
"""

import datetime
import json
from typing import Any


def calculate_momentum(
    price_series: dict[str, float],
    reference_date: str,
    lookback_days: int = 20,
) -> dict:
    """
    Compute the lookback-day price momentum ending on (or before) reference_date.

    Args:
        price_series:  {date_str: close_price} mapping
        reference_date: YYYY-MM-DD string — the announcement / signal date
        lookback_days:  Number of trading days to look back

    Returns:
        dict with:
            "momentum"      -> float (fractional return, e.g. 0.05 = +5 %)
            "start_date"    -> str
            "end_date"      -> str (actual date used, <= reference_date)
            "start_price"   -> float
            "end_price"     -> float
            "signal"        -> "bullish" | "bearish" | "neutral"
            "error"         -> str | None
    """
    try:
        ref = datetime.date.fromisoformat(reference_date)
        # Sort available dates
        sorted_dates = sorted(price_series.keys())
        # Keep only dates up to and including reference_date
        eligible = [d for d in sorted_dates if d <= reference_date]
        if len(eligible) < 2:
            return {
                "momentum": None,
                "error": f"Fewer than 2 data points on or before {reference_date}",
            }

        end_date = eligible[-1]
        # lookback_days trading sessions back
        start_idx = max(0, len(eligible) - lookback_days - 1)
        start_date = eligible[start_idx]

        start_price = price_series[start_date]
        end_price = price_series[end_date]

        if start_price == 0:
            return {"momentum": None, "error": "start_price is zero"}

        momentum = (end_price - start_price) / start_price

        if momentum > 0.02:
            signal = "bullish"
        elif momentum < -0.02:
            signal = "bearish"
        else:
            signal = "neutral"

        return {
            "momentum": round(momentum, 6),
            "start_date": start_date,
            "end_date": end_date,
            "start_price": start_price,
            "end_price": end_price,
            "signal": signal,
            "error": None,
        }

    except Exception as exc:
        return {"momentum": None, "error": str(exc)}


def score_candidates(
    candidates: list[dict[str, Any]],
    price_data: dict[str, dict[str, float]],
    lookback_days: int = 20,
    momentum_threshold: float = 0.0,
) -> dict:
    """
    Score a list of SPX inclusion candidates by momentum.

    Args:
        candidates:         List of event dicts (from get_spx_changes)
        price_data:         {ticker: {date: price}}
        lookback_days:      Trading-day lookback for momentum
        momentum_threshold: Minimum momentum to include in "filtered" list

    Returns:
        dict with:
            "scored"   -> list of candidate dicts enriched with momentum data
            "filtered" -> only those passing the threshold
            "error"    -> str | None
    """
    try:
        scored = []
        for event in candidates:
            ticker = event["ticker"]
            ann_date = event["date"]
            series = price_data.get(ticker, {})
            mom = calculate_momentum(series, ann_date, lookback_days)
            scored.append({**event, **mom})

        filtered = [
            c for c in scored
            if c.get("momentum") is not None
            and c["momentum"] >= momentum_threshold
        ]
        filtered.sort(key=lambda x: x["momentum"], reverse=True)

        return {"scored": scored, "filtered": filtered, "error": None}

    except Exception as exc:
        return {"scored": [], "filtered": [], "error": str(exc)}
