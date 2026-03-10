"""
Fetch historical S&P 500 additions/removals.

Primary source: Wikipedia (free, no API key needed)
Fallback:       Local CSV in data/sp500_changes.csv
"""

import os
import json
import datetime
import pandas as pd


def get_spx_changes(
    start_date: str | None = None,
    end_date: str | None = None,
    event_type: str = "additions",
) -> dict:
    """
    Retrieve S&P 500 index changes (additions or removals).

    Args:
        start_date: ISO date string (YYYY-MM-DD), default 3 years ago
        end_date:   ISO date string (YYYY-MM-DD), default today
        event_type: "additions", "removals", or "both"

    Returns:
        dict with keys:
            "events" -> list of dicts with keys:
                ticker, company, date, effective_date, event_type
            "count"  -> int
            "error"  -> str | None
    """
    try:
        if start_date is None:
            start_date = (
                datetime.date.today() - datetime.timedelta(days=3 * 365)
            ).isoformat()
        if end_date is None:
            end_date = datetime.date.today().isoformat()

        dt_start = datetime.date.fromisoformat(start_date)
        dt_end = datetime.date.fromisoformat(end_date)

        # Try local CSV first (user-supplied or previously cached)
        local_path = os.path.join(
            os.path.dirname(__file__), "..", "data", "sp500_changes.csv"
        )
        if os.path.exists(local_path):
            df = _load_local_csv(local_path)
        else:
            df = _fetch_from_wikipedia()
            # Cache it
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            df.to_csv(local_path, index=False)

        # Filter by date range
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df[(df["date"] >= dt_start) & (df["date"] <= dt_end)]

        if event_type != "both":
            df = df[df["event_type"] == event_type]

        events = df.sort_values("date").to_dict("records")
        # Make dates JSON serialisable
        for e in events:
            e["date"] = str(e["date"])
            if "effective_date" in e and pd.notna(e.get("effective_date")):
                e["effective_date"] = str(e["effective_date"])
            else:
                # Assume effective = announced + 5 trading days (~7 calendar days)
                eff = datetime.date.fromisoformat(e["date"]) + datetime.timedelta(
                    days=7
                )
                e["effective_date"] = eff.isoformat()

        return {"events": events, "count": len(events), "error": None}

    except Exception as exc:
        return {"events": [], "count": 0, "error": str(exc)}


# ─── helpers ─────────────────────────────────────────────────────────────────


def _load_local_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"ticker", "date", "event_type"}
    if not required.issubset(df.columns):
        raise ValueError(
            f"CSV must have columns: {required}. Found: {list(df.columns)}"
        )
    if "company" not in df.columns:
        df["company"] = df["ticker"]
    if "effective_date" not in df.columns:
        df["effective_date"] = pd.NaT
    return df[["ticker", "company", "date", "effective_date", "event_type"]]


def _fetch_from_wikipedia() -> pd.DataFrame:
    """Scrape S&P 500 changes from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    tables = pd.read_html(url)
    # Second table is "Selected changes to the list of S&P 500 components"
    changes = tables[1]
    changes.columns = [
        "_".join(str(c).lower().split()) for c in changes.columns.tolist()
    ]

    rows = []
    # Wikipedia table has pairs of Added / Removed columns
    for _, row in changes.iterrows():
        date_raw = row.get("date", row.get("date_added", None))
        if pd.isna(date_raw):
            continue
        try:
            dt = pd.to_datetime(str(date_raw)).date()
        except Exception:
            continue

        # Added ticker
        added = row.get("added_ticker", row.get("added", None))
        if pd.notna(added) and str(added).strip():
            rows.append(
                {
                    "ticker": str(added).strip().replace(".", "-"),
                    "company": str(
                        row.get("added_security", row.get("security", added))
                    ).strip(),
                    "date": dt,
                    "effective_date": None,
                    "event_type": "additions",
                }
            )

        # Removed ticker
        removed = row.get("removed_ticker", row.get("removed", None))
        if pd.notna(removed) and str(removed).strip():
            rows.append(
                {
                    "ticker": str(removed).strip().replace(".", "-"),
                    "company": str(
                        row.get(
                            "removed_security", row.get("reason", removed)
                        )
                    ).strip(),
                    "date": dt,
                    "effective_date": None,
                    "event_type": "removals",
                }
            )

    if not rows:
        raise RuntimeError(
            "Wikipedia scrape returned no rows — table structure may have changed. "
            "Provide a local CSV at data/sp500_changes.csv instead."
        )

    return pd.DataFrame(rows)
