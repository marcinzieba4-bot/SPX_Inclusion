"""
Fetch OHLCV price data for a list of tickers.

Uses yfinance (free, no API key required).
"""

import datetime
import json
import yfinance as yf
import pandas as pd


def get_price_data(
    tickers: list[str],
    start_date: str,
    end_date: str,
    field: str = "Close",
) -> dict:
    """
    Download daily price data for the given tickers.

    Args:
        tickers:    List of ticker symbols, e.g. ["AAPL", "MSFT"]
        start_date: ISO date (YYYY-MM-DD)
        end_date:   ISO date (YYYY-MM-DD)
        field:      OHLCV field to return: Open, High, Low, Close, Volume

    Returns:
        dict with:
            "data"  -> {ticker: {date_str: value, ...}, ...}
            "error" -> str | None
    """
    try:
        end_dt = (
            datetime.date.fromisoformat(end_date) + datetime.timedelta(days=1)
        ).isoformat()

        raw = yf.download(
            tickers,
            start=start_date,
            end=end_dt,
            auto_adjust=True,
            progress=False,
        )

        if raw.empty:
            return {"data": {}, "error": "No data returned from yfinance"}

        # Normalise to {ticker: {date: price}}
        if isinstance(tickers, list) and len(tickers) > 1:
            prices = raw[field] if field in raw.columns.get_level_values(0) else raw
            if isinstance(prices.columns, pd.MultiIndex):
                prices = prices.droplevel(0, axis=1)
        else:
            prices = raw[[field]] if field in raw.columns else raw
            prices.columns = [tickers[0] if isinstance(tickers, list) else tickers]

        result = {}
        for col in prices.columns:
            series = prices[col].dropna()
            result[str(col)] = {str(d.date()): round(float(v), 4) for d, v in series.items()}

        return {"data": result, "error": None}

    except Exception as exc:
        return {"data": {}, "error": str(exc)}


def get_ticker_info(ticker: str) -> dict:
    """
    Return basic metadata for a ticker (sector, market cap, name).

    Returns:
        dict with fields: name, sector, industry, market_cap, error
    """
    try:
        info = yf.Ticker(ticker).info
        return {
            "name": info.get("longName", ticker),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "market_cap": info.get("marketCap", None),
            "error": None,
        }
    except Exception as exc:
        return {"name": ticker, "sector": "Unknown", "industry": "Unknown",
                "market_cap": None, "error": str(exc)}
