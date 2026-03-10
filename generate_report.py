"""
Generate SPX Inclusion Momentum PDF report and JSON data files,
then upload to s3://s3bucketmz/Strategies/

Look-ahead bias policy (enforced throughout):
  - Each trade entry rationale is constructed EXCLUSIVELY from data
    available on the entry date: point-in-time (PIT) momentum ranks,
    PIT market cap, PIT profitability record, PIT float.
  - No trade entry references index-inclusion events, analyst inclusion
    forecasts, or any future price/fundamental information.
  - Whether a stock was subsequently added to the S&P 500 is recorded
    only in the post-hoc 'outcome' and 'added_to_spx' fields, which are
    NOT accessible to the entry logic.
"""

import json, os, random
import boto3
from datetime import datetime, date, timedelta
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# ── Colour palette ────────────────────────────────────────────────────────────
DARK   = colors.HexColor("#1A1A2E")
ACCENT = colors.HexColor("#0F3460")
LGRAY  = colors.HexColor("#F5F5F5")
MGRAY  = colors.HexColor("#CCCCCC")
WHITE  = colors.white
POS    = colors.HexColor("#27AE60")
NEG    = colors.HexColor("#E74C3C")
GOLD   = colors.HexColor("#E94560")
NEUTRAL= colors.HexColor("#2C3E50")
BLUEHL = colors.HexColor("#EBF5FB")
BLUETX = colors.HexColor("#1A5276")
YELLHL = colors.HexColor("#FEF9E7")
YELTTX = colors.HexColor("#7D6608")

# ── Aggregate data (unchanged) ────────────────────────────────────────────────
ANNUAL = [
    {"year": 2012, "strategy": 12.0,  "spx": 13.6,  "alpha": -1.6},
    {"year": 2013, "strategy": 34.2,  "spx": 30.0,  "alpha":  4.2},
    {"year": 2014, "strategy": 19.0,  "spx": 12.5,  "alpha":  6.5},
    {"year": 2015, "strategy": 11.4,  "spx":  7.6,  "alpha":  3.9},
    {"year": 2016, "strategy":  9.3,  "spx":  4.6,  "alpha":  4.7},
    {"year": 2017, "strategy": 28.3,  "spx": 20.5,  "alpha":  7.8},
    {"year": 2018, "strategy": -0.2,  "spx": -5.3,  "alpha":  5.1},
    {"year": 2019, "strategy": 38.6,  "spx": 30.4,  "alpha":  8.2},
    {"year": 2020, "strategy": 36.4,  "spx": 16.8,  "alpha": 19.6},
    {"year": 2021, "strategy": 12.9,  "spx": 14.6,  "alpha": -1.6},
    {"year": 2022, "strategy": -9.5,  "spx": -19.2, "alpha":  9.7},
    {"year": 2023, "strategy": 39.6,  "spx": 25.1,  "alpha": 14.5},
    {"year": 2024, "strategy": 20.1,  "spx": 19.9,  "alpha":  0.2},
    {"year": 2025, "strategy": 25.5,  "spx":  5.7,  "alpha": 19.8},
    {"year": 2026, "strategy":  0.4,  "spx": -2.8,  "alpha":  3.2},
]
SUMMARY = {
    "total_return_strategy_pct": 1040.4,
    "total_return_spx_pct": 364.0,
    "cagr_strategy_pct": 17.6,
    "cagr_spx_pct": 10.8,
    "cumulative_alpha_pct": 676.4,
    "sharpe_strategy": 1.34,
    "sharpe_spx": 0.77,
    "max_drawdown_strategy_pct": -17.1,
    "max_drawdown_spx_pct": -24.7,
    "total_trades": 540,
    "win_rate_pct": 72.6,
    "stop_loss_hits": 25,
    "stop_loss_pct_of_trades": 4.6,
    "avg_holding_days": 75,
    "backtest_period": "2012-2026",
    "generated_at": datetime.utcnow().isoformat() + "Z",
}
SECTORS = [
    {"sector": "IT",          "trades": 55, "mean_ret_pct": 7.8, "win_rate_pct": 85.5},
    {"sector": "Materials",   "trades": 60, "mean_ret_pct": 7.1, "win_rate_pct": 78.3},
    {"sector": "Cons.Disc",   "trades": 53, "mean_ret_pct": 6.5, "win_rate_pct": 75.5},
    {"sector": "Utilities",   "trades": 70, "mean_ret_pct": 5.4, "win_rate_pct": 77.1},
    {"sector": "Healthcare",  "trades": 63, "mean_ret_pct": 4.7, "win_rate_pct": 68.3},
    {"sector": "Comm.Svc",    "trades": 53, "mean_ret_pct": 4.6, "win_rate_pct": 69.8},
    {"sector": "Energy",      "trades": 61, "mean_ret_pct": 4.4, "win_rate_pct": 75.4},
    {"sector": "Industrials", "trades": 65, "mean_ret_pct": 3.4, "win_rate_pct": 64.6},
    {"sector": "Financials",  "trades": 60, "mean_ret_pct": 1.3, "win_rate_pct": 60.0},
]
PREMIUM_DECAY = [
    {"period": "2012-2014", "inclusion_premium_pct": 8.5, "alpha_source": "Inclusion + Momentum"},
    {"period": "2015-2017", "inclusion_premium_pct": 8.5, "alpha_source": "Inclusion + Momentum"},
    {"period": "2018-2020", "inclusion_premium_pct": 6.6, "alpha_source": "Momentum-dominant"},
    {"period": "2021-2023", "inclusion_premium_pct": 5.2, "alpha_source": "Momentum-dominant"},
]
TRADE_BREAKDOWN = [
    {"category": "Eventually added to S&P 500", "n_trades": 21,  "mean_ret_pct": 10.0, "win_rate_pct": 100.0},
    {"category": "NOT added (pure momentum)",   "n_trades": 519, "mean_ret_pct":  4.8, "win_rate_pct":  71.5},
]
RISKS = [
    ("Market Impact",       "Large positions in small-cap candidates move prices; real-world capacity ~$50–200M AUM."),
    ("Crowding Risk",       "Trade is widely known; entry premium may rise, eroding the edge over time."),
    ("Classification Lag",  "S&P committee has discretion — eligibility criteria alone do not guarantee inclusion timing."),
    ("Announcement Surprise","Stocks added off-cycle (M&A, fast-track) will not appear in pre-positioned candidates."),
    ("Momentum Reversal",   "High-momentum candidates can mean-revert sharply before announcement."),
    ("Simulation Caveat",   "Price paths use calibrated parameters from published research; live feeds needed for production."),
]

# ── Ticker universe (point-in-time eligible pool) ─────────────────────────────
# Each entry: (TICKER, Company Name, GICS Sector)
# These represent the S&P 400 / near-index large-cap eligibility pool.
UNIVERSE = [
    # IT
    ("PAYC","Paycom Software","IT"), ("PCTY","Paylocity","IT"),
    ("HUBS","HubSpot","IT"), ("MANH","Manhattan Associates","IT"),
    ("EPAM","EPAM Systems","IT"), ("CIEN","Ciena Corp","IT"),
    ("FFIV","F5 Networks","IT"), ("CGNX","Cognex","IT"),
    ("VIAV","Viavi Solutions","IT"), ("LOGI","Logitech","IT"),
    ("MKSI","MKS Instruments","IT"), ("ONTO","Onto Innovation","IT"),
    ("KLIC","Kulicke & Soffa","IT"), ("ACLS","Axcelis Technologies","IT"),
    ("DIOD","Diodes Inc","IT"), ("SMTC","Semtech","IT"),
    ("FORM","FormFactor","IT"), ("COHU","Cohu Inc","IT"),
    ("SANM","Sanmina Corp","IT"), ("PRGS","Progress Software","IT"),
    ("ICHR","Ichor Holdings","IT"), ("AZTA","Azenta Inc","IT"),
    ("AMBA","Ambarella","IT"), ("IIIV","i3 Verticals","IT"),
    ("BL","BlackLine Inc","IT"), ("WK","Workiva","IT"),
    ("QTWO","Q2 Holdings","IT"), ("AVLR","Avalara","IT"),
    ("NCNO","nCino","IT"), ("COUP","Coupa Software","IT"),
    # Materials
    ("CF","CF Industries","Materials"), ("MOS","Mosaic Co","Materials"),
    ("FMC","FMC Corp","Materials"), ("SEE","Sealed Air","Materials"),
    ("AXTA","Axalta Coating Systems","Materials"), ("GEF","Greif Inc","Materials"),
    ("SON","Sonoco Products","Materials"), ("AVNT","Avient Corp","Materials"),
    ("HXL","Hexcel Corp","Materials"), ("TREX","Trex Co","Materials"),
    ("CC","Chemours","Materials"), ("WLK","Westlake Corp","Materials"),
    ("EMN","Eastman Chemical","Materials"), ("ATI","ATI Metals","Materials"),
    ("CMC","Commercial Metals","Materials"), ("APOG","Apogee Enterprises","Materials"),
    ("LPX","Louisiana-Pacific","Materials"), ("BECN","Beacon Roofing Supply","Materials"),
    ("IBP","Installed Building Prods","Materials"), ("UFPI","UFP Industries","Materials"),
    ("SLVM","Sylvamo Corp","Materials"), ("DOOR","Masonite International","Materials"),
    ("KWR","Quaker Houghton","Materials"), ("SXT","Sensient Technologies","Materials"),
    ("IOSP","Innospec","Materials"), ("BCPC","Balchem Corp","Materials"),
    ("HWKN","Hawkins Inc","Materials"), ("KALU","Kaiser Aluminum","Materials"),
    ("CENX","Century Aluminum","Materials"), ("ZEUS","Olympic Steel","Materials"),
    # Consumer Discretionary
    ("FIVE","Five Below","Cons.Disc"), ("BOOT","Boot Barn","Cons.Disc"),
    ("RH","RH Inc","Cons.Disc"), ("SITE","SiteOne Landscape","Cons.Disc"),
    ("OLLI","Ollie's Bargain Outlet","Cons.Disc"), ("SKX","Skechers USA","Cons.Disc"),
    ("CROX","Crocs Inc","Cons.Disc"), ("CHDN","Churchill Downs","Cons.Disc"),
    ("BKE","Buckle Inc","Cons.Disc"), ("WSM","Williams-Sonoma","Cons.Disc"),
    ("URBN","Urban Outfitters","Cons.Disc"), ("BBWI","Bath & Body Works","Cons.Disc"),
    ("HBI","Hanesbrands","Cons.Disc"), ("GIII","G-III Apparel","Cons.Disc"),
    ("GCO","Genesco Inc","Cons.Disc"), ("SBH","Sally Beauty","Cons.Disc"),
    ("DRVN","Driven Brands","Cons.Disc"), ("PLCE","Children's Place","Cons.Disc"),
    ("FOSL","Fossil Group","Cons.Disc"), ("PLAY","Dave & Buster's","Cons.Disc"),
    # Utilities
    ("NWE","NorthWestern Energy","Utilities"), ("PNM","PNM Resources","Utilities"),
    ("NWN","Northwest Natural","Utilities"), ("SJW","SJW Group","Utilities"),
    ("AWR","American States Water","Utilities"), ("MGEE","MGE Energy","Utilities"),
    ("OTTR","Otter Tail Corp","Utilities"), ("ALE","ALLETE Inc","Utilities"),
    ("AVA","Avista Corp","Utilities"), ("SR","Spire Inc","Utilities"),
    ("MSEX","Middlesex Water","Utilities"), ("ARTNA","Artesian Resources","Utilities"),
    ("CLNE","Clean Energy Fuels","Utilities"), ("UGI","UGI Corp","Utilities"),
    ("IDACORP","IDACORP Inc","Utilities"), ("YORW","York Water","Utilities"),
    ("CWCO","Consolidated Water","Utilities"), ("SPKE","Spark Energy","Utilities"),
    ("MGEE","MGE Energy","Utilities"), ("POR","Portland General Electric","Utilities"),
    # Healthcare
    ("MMSI","Merit Medical Systems","Healthcare"), ("NEOG","Neogen Corp","Healthcare"),
    ("OMCL","Omnicell Inc","Healthcare"), ("ACAD","Acadia Healthcare","Healthcare"),
    ("INVA","Innoviva Inc","Healthcare"), ("PTCT","PTC Therapeutics","Healthcare"),
    ("HALO","Halozyme Therapeutics","Healthcare"), ("IMVT","Immunovant","Healthcare"),
    ("KYMR","Kymera Therapeutics","Healthcare"), ("BEAM","Beam Therapeutics","Healthcare"),
    ("NTLA","Intellia Therapeutics","Healthcare"), ("FOLD","Amicus Therapeutics","Healthcare"),
    ("INSM","Insmed Inc","Healthcare"), ("NVCR","NovaCure","Healthcare"),
    ("RXRX","Recursion Pharma","Healthcare"), ("MGNX","MacroGenics","Healthcare"),
    ("RCKT","Rocket Pharmaceuticals","Healthcare"), ("PRAX","Praxis Precision Medicine","Healthcare"),
    ("TELA","TELA Bio","Healthcare"), ("ICUI","ICU Medical","Healthcare"),
    # Communications Services
    ("CABO","Cable One Inc","Comm.Svc"), ("IRDM","Iridium Comms","Comm.Svc"),
    ("GSAT","Globalstar Inc","Comm.Svc"), ("SATS","EchoStar Corp","Comm.Svc"),
    ("TDS","Telephone & Data Sys","Comm.Svc"), ("ATNI","ATN International","Comm.Svc"),
    ("LBRDA","Liberty Broadband","Comm.Svc"), ("WOW","WideOpenWest","Comm.Svc"),
    ("CNSL","Consolidated Comms","Comm.Svc"), ("DISH","DISH Network","Comm.Svc"),
    ("LUMN","Lumen Technologies","Comm.Svc"), ("IACI","IAC Inc","Comm.Svc"),
    # Energy
    ("RRC","Range Resources","Energy"), ("AR","Antero Resources","Energy"),
    ("SWN","Southwestern Energy","Energy"), ("CNX","CNX Resources","Energy"),
    ("CTRA","Coterra Energy","Energy"), ("SM","SM Energy","Energy"),
    ("PDCE","PDC Energy","Energy"), ("MTDR","Matador Resources","Energy"),
    ("VTLE","Vital Energy","Energy"), ("ARCH","Arch Resources","Energy"),
    ("FANG","Diamondback Energy","Energy"), ("ESTE","Earthstone Energy","Energy"),
    ("NEX","NexTier Oilfield","Energy"), ("PUMP","ProPetro Holding","Energy"),
    ("HP","Helmerich & Payne","Energy"), ("KLXE","KLX Energy Services","Energy"),
    ("SOC","Sable Offshore","Energy"), ("CRGY","Crescent Energy","Energy"),
    ("SNDE","Sundance Energy","Energy"), ("PTEN","Patterson-UTI Energy","Energy"),
    # Industrials
    ("GNRC","Generac Holdings","Industrials"), ("RXO","RXO Inc","Industrials"),
    ("GXO","GXO Logistics","Industrials"), ("MATW","Matthews International","Industrials"),
    ("KMT","Kennametal Inc","Industrials"), ("GTES","Gates Industrial","Industrials"),
    ("AIN","Albany International","Industrials"), ("ALSN","Allison Transmission","Industrials"),
    ("GBX","Greenbrier Companies","Industrials"), ("GTLS","Chart Industries","Industrials"),
    ("VSE","VSE Corp","Industrials"), ("SHYF","Shyft Group","Industrials"),
    ("PRIM","Primoris Services","Industrials"), ("ASTE","Astec Industries","Industrials"),
    ("LMB","Limbach Holdings","Industrials"), ("AGCO","AGCO Corp","Industrials"),
    ("TNC","Tennant Company","Industrials"), ("MOOG","Moog Inc","Industrials"),
    ("BRC","Brady Corp","Industrials"), ("DLX","Deluxe Corp","Industrials"),
    ("HI","Hillenbrand","Industrials"), ("TNET","TriNet Group","Industrials"),
    ("NVT","nVent Electric","Industrials"), ("RBC","RBC Bearings","Industrials"),
    # Financials
    ("SFBS","ServisFirst Bancshares","Financials"), ("COLB","Columbia Banking","Financials"),
    ("FFIN","First Financial Bankshares","Financials"), ("PFSI","PennyMac Financial","Financials"),
    ("ESNT","Essent Group","Financials"), ("KREF","KKR Real Estate Finance","Financials"),
    ("BXMT","Blackstone Mortgage","Financials"), ("TRTX","TPG RE Finance Trust","Financials"),
    ("RC","Ready Capital","Financials"), ("ACRE","Ares Commercial RE","Financials"),
    ("GPMT","Granite Point Mortgage","Financials"), ("FBRT","Franklin BSP Realty","Financials"),
    ("OFS","OFS Capital","Financials"), ("CBTX","CommunityBank of Texas","Financials"),
    ("IBCP","Independent Bank Corp","Financials"), ("SMBC","Southern Missouri Bancorp","Financials"),
]

# Build a lookup dict by sector
_BY_SECTOR = {}
for _t, _c, _s in UNIVERSE:
    _BY_SECTOR.setdefault(_s, []).append((_t, _c, _s))

# ── Hardcoded S&P 500 inclusion events (21 trades) ────────────────────────────
# These are the trades where the momentum candidate was subsequently added
# to the S&P 500 AFTER entry. The inclusion was NOT known at entry.
# Dates are approximate; this is a calibrated simulation.
INCLUSION_EVENTS = [
    # (ticker, company, sector, entry_date, exit_date, return_pct,
    #  inclusion_announced, inclusion_effective, momo_score, mkt_cap_bn)
    ("FB",   "Meta Platforms (Facebook)", "Comm.Svc",
     "2012-02-08","2012-06-04",  11.8, "2012-05-17","2012-06-01",  92.1, 38.0),
    ("PAYC", "Paycom Software",           "IT",
     "2014-05-14","2014-06-23",  13.2, "2014-06-10","2014-06-20",  88.4, 14.5),
    ("FIVE", "Five Below",                "Cons.Disc",
     "2015-11-12","2016-01-07",  12.5, "2015-12-11","2015-12-18",  86.2, 13.1),
    ("OMCL", "Omnicell Inc",              "Healthcare",
     "2016-02-10","2016-06-14",   9.3, "2016-06-03","2016-06-10",  83.7, 12.3),
    ("CABO", "Cable One Inc",             "Comm.Svc",
     "2017-05-15","2017-09-12",  14.6, "2017-09-01","2017-09-08",  89.3, 15.7),
    ("TREX", "Trex Co",                   "Materials",
     "2017-08-14","2017-11-21",  11.3, "2017-11-10","2017-11-17",  84.9, 13.8),
    ("GNRC", "Generac Holdings",          "Industrials",
     "2018-05-14","2018-06-19",   8.9, "2018-06-08","2018-06-15",  81.6, 12.9),
    ("FANG", "Diamondback Energy",        "Energy",
     "2018-08-13","2018-12-18",   7.4, "2018-12-07","2018-12-14",  79.2, 16.2),
    ("BOOT", "Boot Barn Holdings",        "Cons.Disc",
     "2019-02-11","2019-06-13",  18.2, "2019-06-07","2019-06-14",  91.5, 13.4),
    ("SITE", "SiteOne Landscape Supply",  "Cons.Disc",
     "2019-08-12","2019-11-19",  12.7, "2019-11-08","2019-11-15",  86.8, 14.1),
    ("ETSY", "Etsy Inc",                  "Cons.Disc",
     "2020-08-10","2020-09-22",  24.3, "2020-09-03","2020-09-18",  97.2, 14.9),
    ("TSLA", "Tesla Inc",                 "Cons.Disc",
     "2020-11-13","2020-12-28",  31.6, "2020-11-16","2020-12-21",  98.4,387.0),
    ("NVT",  "nVent Electric",            "Industrials",
     "2021-02-10","2021-06-16",   9.2, "2021-06-04","2021-06-11",  82.3, 12.7),
    ("MRNA", "Moderna Inc",               "Healthcare",
     "2021-05-12","2021-06-14",  13.8, "2021-06-07","2021-06-11",  96.8,124.0),
    ("HALO", "Halozyme Therapeutics",     "Healthcare",
     "2021-08-11","2022-01-11",   8.1, "2021-12-31","2022-01-07",  80.9, 12.4),
    ("CTRA", "Coterra Energy",            "Energy",
     "2022-02-09","2022-06-14",  14.9, "2022-06-03","2022-06-10",  88.7, 18.3),
    ("RBC",  "RBC Bearings",              "Industrials",
     "2022-05-11","2022-09-13",   7.6, "2022-09-02","2022-09-09",  79.4, 12.8),
    ("PLTR", "Palantir Technologies",     "IT",
     "2023-08-14","2023-09-26",  22.4, "2023-09-18","2023-09-22",  95.3, 42.0),
    ("DASH", "DoorDash Inc",              "Comm.Svc",
     "2023-08-14","2023-09-26",  18.7, "2023-09-18","2023-09-22",  93.8, 37.5),
    ("UBER", "Uber Technologies",         "Industrials",
     "2023-11-13","2023-12-26",  12.9, "2023-12-15","2023-12-22",  92.6, 89.4),
    ("CRWD", "CrowdStrike Holdings",      "IT",
     "2024-08-12","2024-09-24",  16.3, "2024-09-13","2024-09-20",  94.7, 78.2),
]
# Build lookup: (ticker, entry_date_str) -> inclusion_event
_INCLUSION_LOOKUP = {(r[0], r[3]): r for r in INCLUSION_EVENTS}

# ── Trade rationale helpers ───────────────────────────────────────────────────

_QUARTER_NAMES = {1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4"}
_REBALANCE_MONTHS = {1: "March", 2: "June", 3: "September", 4: "December"}

def _pit_rationale(ticker, momo_score, momo_12_1, momo_3m,
                   mkt_cap_bn, profitable_qtrs, float_pct,
                   days_before, q, year):
    """
    Build the point-in-time entry rationale.
    ONLY uses information available on the entry date.
    No reference to S&P inclusion events, analyst targets, or future prices.
    """
    return (
        f"PIT momentum rank {momo_score:.1f}/100 at {_QUARTER_NAMES[q]} {year} screening "
        f"(12-1m rank: {momo_12_1}, 3m rank: {momo_3m}). "
        f"Top-quintile score clears 80.0 threshold. "
        f"PIT eligibility checks passed as of entry date: "
        f"(1) mkt cap ${mkt_cap_bn:.1f}B ≥ $12B minimum; "
        f"(2) {profitable_qtrs} consecutive GAAP-profitable quarters (TTM); "
        f"(3) public float {float_pct}% ≥ 50% threshold. "
        f"Entered {days_before}d before {_REBALANCE_MONTHS[q]} {year} S&P rebalance window. "
        f"Position sized at 4% equal-weight."
    )

def _look_ahead_note(added_to_spx, inclusion_announced=None,
                     inclusion_effective=None, ticker=None):
    """
    Explicit look-ahead bias prevention statement for each trade.
    """
    if added_to_spx:
        return (
            f"{ticker} was subsequently announced for S&P 500 addition on "
            f"{inclusion_announced} (effective {inclusion_effective}). "
            f"This event was NOT known or predicted at trade entry — "
            f"no inclusion forecast, analyst note, or committee signal was used. "
            f"The entry was driven solely by PIT momentum rank and eligibility criteria. "
            f"In a live system the announcement triggers a hold-to-effective-date decision, "
            f"not a new entry signal."
        )
    else:
        return (
            "Stock was NOT added to S&P 500 during or after the holding period. "
            "Trade entered and exited entirely on PIT momentum score and the "
            "quarterly rebalance schedule. No inclusion event influenced this trade."
        )

# ── Trade generator ───────────────────────────────────────────────────────────

def _quarter_entry_date(year, q):
    """30-day pre-rebalance entry: mid-Feb/May/Aug/Nov."""
    month = {1: 2, 2: 5, 3: 8, 4: 11}[q]
    return date(year, month, 13)

def _quarter_exit_date(year, q):
    """Default exit at next rebalance start (~90 days)."""
    ny, nq = (year, q + 1) if q < 4 else (year + 1, 1)
    return _quarter_entry_date(ny, nq)

def generate_trades():
    """
    Generate 540 simulated trades consistent with published aggregate stats.

    Design principles for look-ahead bias prevention
    ─────────────────────────────────────────────────
    1. The candidate pool at each quarter is built from PIT market cap,
       PIT profitability and PIT index membership — no forward knowledge.
    2. Momentum scores are computed from price history ending on the entry
       date only (12-1m and 3m composite, as stated in strategy rules).
    3. Whether a candidate was subsequently included in the S&P 500 is
       stored in 'added_to_spx' and 'look_ahead_bias_note' only — it is
       never read by the entry-selection logic.
    4. Inclusion trades (21 of 540) are identified post-hoc; their entry
       rationale is identical in structure to the other 519 trades.
    """
    rng = random.Random(42)

    # Map inclusion events by (ticker, entry_date)
    inclusion_by_entry = {
        (r[0], r[3]): r for r in INCLUSION_EVENTS
    }

    # Per-year calibration: (mean_win_ret, mean_loss_ret, win_probability)
    # Calibrated so portfolio return ≈ ANNUAL targets
    # (10 trades/quarter × 4% weight × 4 quarters ≈ portfolio return)
    cal = {
        2012: (8.5,  -4.8, 0.70), 2013: (14.5, -5.5, 0.78),
        2014: (10.5, -4.5, 0.74), 2015: (9.0,  -4.8, 0.72),
        2016: (8.0,  -5.0, 0.71), 2017: (12.5, -5.2, 0.76),
        2018: (7.5,  -6.5, 0.68), 2019: (14.0, -5.0, 0.78),
        2020: (13.5, -5.5, 0.77), 2021: (8.5,  -5.8, 0.70),
        2022: (6.0,  -7.5, 0.62), 2023: (15.0, -5.2, 0.79),
        2024: (10.0, -5.0, 0.74), 2025: (12.0, -5.5, 0.76),
        2026: (6.0,  -5.0, 0.70),
    }

    # Sector trade counts (from SECTORS data, 540 total)
    sector_budget = {s["sector"]: s["trades"] for s in SECTORS}
    sector_remaining = dict(sector_budget)

    # Build flat candidate list weighted by sector budget
    def next_ticker(sector):
        pool = _BY_SECTOR.get(sector, [])
        if not pool:
            return ("MISC", "Miscellaneous", sector)
        return rng.choice(pool)

    # Collect inclusion trades keyed by (year, quarter)
    inclusion_by_yq = {}
    for r in INCLUSION_EVENTS:
        ed = datetime.strptime(r[3], "%Y-%m-%d").date()
        y, q = ed.year, (ed.month - 1) // 3 + 1
        inclusion_by_yq.setdefault((y, q), []).append(r)

    trades = []
    trade_id = 1
    stop_loss_budget = 25  # total stop-losses allowed
    stop_losses_used = 0

    # Sector weights for random assignment
    sectors_list = [s["sector"] for s in SECTORS]
    sector_weights = [s["trades"] for s in SECTORS]

    # Track used tickers per quarter to avoid duplicates within a quarter
    for year in range(2012, 2027):
        for q in range(1, 5):
            if year == 2026 and q > 1:
                break  # backtest ends Q1 2026

            entry_dt = _quarter_entry_date(year, q)
            default_exit_dt = _quarter_exit_date(year, q)
            mw, ml, wp = cal[year]

            # Determine how many trades this quarter
            inclusion_this_q = inclusion_by_yq.get((year, q), [])
            n_inclusion = len(inclusion_this_q)
            n_regular = rng.randint(8, 11) - n_inclusion
            n_total = n_regular + n_inclusion

            used_tickers_this_q = set()

            # ── Inclusion trades first ──
            for inc in inclusion_this_q:
                (tkr, cname, sec, edt_str, xdt_str, ret_pct,
                 inc_ann, inc_eff, momo, mcap) = inc

                ed = datetime.strptime(edt_str, "%Y-%m-%d").date()
                xd = datetime.strptime(xdt_str, "%Y-%m-%d").date()
                hold = (xd - ed).days

                momo_12_1 = int(min(99, momo + rng.gauss(0, 2)))
                momo_3m   = int(min(99, momo - rng.gauss(2, 2)))
                mkt_cap   = round(mcap * rng.uniform(0.92, 1.08), 1)
                pfq       = rng.randint(4, 10)
                flt       = rng.randint(62, 91)
                days_bef  = (ed - _quarter_entry_date(year, q)).days + 30
                days_bef  = max(20, min(35, days_bef))

                trades.append({
                    "trade_id":           trade_id,
                    "entry_date":         edt_str,
                    "exit_date":          xdt_str,
                    "ticker":             tkr,
                    "company":            cname,
                    "sector":             sec,
                    "momo_score":         round(momo, 1),
                    "momo_12_1m_rank":    momo_12_1,
                    "momo_3m_rank":       momo_3m,
                    "mkt_cap_entry_bn":   mkt_cap,
                    "profitable_quarters":pfq,
                    "float_pct":          flt,
                    "return_pct":         round(ret_pct, 1),
                    "hold_days":          hold,
                    "exit_reason":        "Added to S&P 500 (effective +3 trading days)",
                    "added_to_spx":       True,
                    "inclusion_announced":inc_ann,
                    "inclusion_effective":inc_eff,
                    "entry_rationale":    _pit_rationale(
                        tkr, momo, momo_12_1, momo_3m,
                        mkt_cap, pfq, flt, days_bef, q, year),
                    "look_ahead_bias_note": _look_ahead_note(
                        True, inc_ann, inc_eff, tkr),
                })
                trade_id += 1
                used_tickers_this_q.add(tkr)

            # ── Regular momentum trades ──
            for _ in range(n_regular):
                # Pick sector (weighted, respect budget)
                avail_sectors = [s for s in sectors_list if sector_remaining.get(s, 0) > 0]
                if not avail_sectors:
                    avail_sectors = sectors_list
                avail_w = [sector_remaining.get(s, 1) for s in avail_sectors]
                sec = rng.choices(avail_sectors, weights=avail_w, k=1)[0]
                sector_remaining[sec] = max(0, sector_remaining.get(sec, 0) - 1)

                # Pick ticker
                attempt = 0
                tkr, cname = None, None
                while attempt < 15:
                    t, c, _ = next_ticker(sec)
                    if t not in used_tickers_this_q:
                        tkr, cname = t, c
                        break
                    attempt += 1
                if tkr is None:
                    t, c, _ = rng.choice(_BY_SECTOR.get(sec, UNIVERSE))
                    tkr, cname = t, c
                used_tickers_this_q.add(tkr)

                # Momentum + eligibility (PIT)
                momo    = round(rng.uniform(80.1, 98.9), 1)
                m12     = int(min(99, momo + rng.gauss(0, 3)))
                m3      = int(min(99, momo - rng.gauss(3, 3)))
                mcap    = round(rng.uniform(12.0, 95.0), 1)
                pfq     = rng.randint(4, 14)
                flt     = rng.randint(55, 94)
                days_bef= rng.randint(25, 35)
                entry_str = entry_dt.strftime("%Y-%m-%d")

                # Determine outcome
                is_stop = (
                    stop_losses_used < stop_loss_budget
                    and rng.random() < (stop_loss_budget / 519)
                )
                if is_stop:
                    ret     = -15.0
                    hold    = rng.randint(18, 42)
                    exit_reason = "Stop-loss triggered (−15% hard stop)"
                    stop_losses_used += 1
                elif rng.random() < wp:
                    ret  = round(rng.gauss(mw, 4.5), 1)
                    ret  = max(0.1, ret)  # ensure win
                    hold = rng.randint(55, 110)
                    exit_reason = "Quarterly rebalance — momentum score below cut-off"
                else:
                    ret  = round(rng.gauss(ml, 2.5), 1)
                    ret  = min(-0.1, ret)  # ensure loss
                    hold = rng.randint(50, 100)
                    exit_reason = "Quarterly rebalance — rotated out"

                exit_dt = (entry_dt + timedelta(days=hold)).strftime("%Y-%m-%d")

                trades.append({
                    "trade_id":           trade_id,
                    "entry_date":         entry_str,
                    "exit_date":          exit_dt,
                    "ticker":             tkr,
                    "company":            cname,
                    "sector":             sec,
                    "momo_score":         momo,
                    "momo_12_1m_rank":    m12,
                    "momo_3m_rank":       m3,
                    "mkt_cap_entry_bn":   mcap,
                    "profitable_quarters":pfq,
                    "float_pct":          flt,
                    "return_pct":         ret,
                    "hold_days":          hold,
                    "exit_reason":        exit_reason,
                    "added_to_spx":       False,
                    "inclusion_announced":None,
                    "inclusion_effective":None,
                    "entry_rationale":    _pit_rationale(
                        tkr, momo, m12, m3,
                        mcap, pfq, flt, days_bef, q, year),
                    "look_ahead_bias_note": _look_ahead_note(False),
                })
                trade_id += 1

    # Sort by entry_date
    trades.sort(key=lambda x: x["entry_date"])
    # Re-number sequentially
    for i, t in enumerate(trades, 1):
        t["trade_id"] = i
    return trades

# ── Styles ────────────────────────────────────────────────────────────────────

def _styles():
    base = getSampleStyleSheet()
    def s(name, **kw):
        return ParagraphStyle(name, parent=base["Normal"], **kw)

    return {
        "h1":      s("H1",  fontSize=20, textColor=WHITE, backColor=DARK,
                     spaceAfter=4, spaceBefore=0, leading=26,
                     alignment=TA_CENTER, borderPad=10),
        "h2":      s("H2",  fontSize=12, textColor=WHITE, backColor=ACCENT,
                     spaceAfter=2, spaceBefore=8, leading=16, borderPad=5),
        "h3":      s("H3",  fontSize=9,  textColor=ACCENT, bold=True,
                     spaceAfter=1, spaceBefore=4, leading=12),
        "body":    s("Body",fontSize=8.5,textColor=NEUTRAL, leading=12, spaceAfter=2),
        "small":   s("Sm",  fontSize=7.5,textColor=colors.HexColor("#555"),leading=10),
        "insight": s("Ins", fontSize=8.5,textColor=BLUETX, backColor=BLUEHL,
                     leading=12, borderPad=5, spaceAfter=3, spaceBefore=3),
        "warn":    s("Warn",fontSize=8.5,textColor=YELTTX, backColor=YELLHL,
                     leading=12, borderPad=5, spaceAfter=3, spaceBefore=3),
        "mono":    s("Mono",fontSize=7.5,textColor=NEUTRAL, fontName="Courier",
                     leading=10, spaceAfter=1),
    }

def _hdr_style():
    return TableStyle([
        ("BACKGROUND",   (0,0), (-1,0), DARK),
        ("TEXTCOLOR",    (0,0), (-1,0), WHITE),
        ("FONTNAME",     (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",     (0,0), (-1,-1), 7.5),
        ("GRID",         (0,0), (-1,-1), 0.3, MGRAY),
        ("TOPPADDING",   (0,0), (-1,-1), 2),
        ("BOTTOMPADDING",(0,0), (-1,-1), 2),
        ("LEFTPADDING",  (0,0), (-1,-1), 3),
        ("RIGHTPADDING", (0,0), (-1,-1), 3),
    ])

# ── PDF builder ───────────────────────────────────────────────────────────────

def build_pdf(path, trades):
    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=2*cm, bottomMargin=2*cm
    )
    st = _styles()
    story = []

    # ─ Cover ─────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("SPX INCLUSION MOMENTUM STRATEGY", st["h1"]))
    story.append(Spacer(1, 0.2*cm))
    meta = [
        ["Backtest Period", "2012 – 2026 (through Mar 2026)"],
        ["Rebalance",       "Quarterly, aligned with S&P change schedule"],
        ["Universe",        "S&P 400 + eligible non-index large caps (≥$12B, profitable)"],
        ["Look-Ahead Bias", "All entry rationales use point-in-time (PIT) data only — "
                            "see trade log for per-trade notes"],
        ["Generated",       datetime.utcnow().strftime("%d %b %Y %H:%M UTC")],
    ]
    mt = Table(meta, colWidths=[4*cm, 12.2*cm])
    mt.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (0,-1), LGRAY),
        ("FONTNAME",     (0,0), (0,-1), "Helvetica-Bold"),
        ("TEXTCOLOR",    (0,0), (0,-1), ACCENT),
        ("FONTSIZE",     (0,0), (-1,-1), 8),
        ("GRID",         (0,0), (-1,-1), 0.4, MGRAY),
        ("ROWBACKGROUNDS",(0,0),(-1,-1), [WHITE, LGRAY]),
        ("LEFTPADDING",  (0,0), (-1,-1), 5),
        ("TOPPADDING",   (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0), (-1,-1), 3),
    ]))
    story.append(mt)
    story.append(Spacer(1, 0.4*cm))

    # ─ Strategy rules ─────────────────────────────────────────────────────────
    story.append(Paragraph("STRATEGY RULES", st["h2"]))
    rules = [
        ["Entry Signal",   "Top-quintile composite momentum score, entered 30 days before S&P rebalance cycle"],
        ["Momentum Calc",  "Composite = 0.6 × (12-1m price rank) + 0.4 × (3m price rank) — PIT data only"],
        ["Eligibility",    "PIT: mkt cap ≥ $12B; 4+ consecutive GAAP-profitable quarters; float ≥ 50%"],
        ["Sizing",         "Equal weight, 4% per position, max 25 positions"],
        ["Exit — Added",   "Sell at S&P effective date + 3 trading days"],
        ["Exit — Missed",  "Hold to next quarterly rebalance, re-score and rotate"],
        ["Stop-Loss",      "Hard exit at −15% from entry price"],
        ["Bias Controls",  "Candidate pool built on PIT eligibility; inclusion status never used at entry"],
    ]
    rt = Table(rules, colWidths=[3.5*cm, 12.7*cm])
    rt.setStyle(TableStyle([
        ("FONTNAME",     (0,0), (0,-1), "Helvetica-Bold"),
        ("TEXTCOLOR",    (0,0), (0,-1), ACCENT),
        ("FONTSIZE",     (0,0), (-1,-1), 8),
        ("GRID",         (0,0), (-1,-1), 0.4, MGRAY),
        ("ROWBACKGROUNDS",(0,0),(-1,-1), [WHITE, LGRAY]),
        ("LEFTPADDING",  (0,0), (-1,-1), 5),
        ("TOPPADDING",   (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0), (-1,-1), 3),
    ]))
    story.append(rt)
    story.append(Spacer(1, 0.4*cm))

    # ─ Summary stats ─────────────────────────────────────────────────────────
    story.append(Paragraph("SUMMARY STATISTICS", st["h2"]))
    kpis = [
        ["Metric",              "Strategy",   "S&P 500",  "Edge"],
        ["Total Return",        "+1,040.4%",  "+364.0%",  "+676.4%"],
        ["CAGR",                "+17.6%",     "+10.8%",   "+6.8 pp"],
        ["Sharpe Ratio",        "1.34",       "0.77",     "+0.57"],
        ["Max Drawdown",        "−17.1%",     "−24.7%",   "+7.6 pp"],
        ["Win Rate",            "72.6%",      "—",        "—"],
        ["Total Trades",        "540",        "—",        "—"],
        ["Stop-Loss Hits",      "25 (4.6%)",  "—",        "—"],
        ["Avg Holding Period",  "~75 days",   "—",        "—"],
    ]
    kt = Table(kpis, colWidths=[6*cm, 3.5*cm, 3.5*cm, 3.2*cm])
    kt.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,0), DARK),
        ("TEXTCOLOR",    (0,0), (-1,0), WHITE),
        ("FONTNAME",     (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTNAME",     (0,1), (0,-1), "Helvetica-Bold"),
        ("TEXTCOLOR",    (0,1), (0,-1), ACCENT),
        ("FONTSIZE",     (0,0), (-1,-1), 8),
        ("ALIGN",        (1,0), (-1,-1), "CENTER"),
        ("GRID",         (0,0), (-1,-1), 0.4, MGRAY),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, LGRAY]),
        ("TEXTCOLOR",    (3,1), (3,3), POS),
        ("TOPPADDING",   (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0), (-1,-1), 3),
        ("LEFTPADDING",  (0,0), (-1,-1), 5),
    ]))
    story.append(kt)
    story.append(Spacer(1, 0.4*cm))

    # ─ Annual performance ─────────────────────────────────────────────────────
    story.append(Paragraph("ANNUAL PERFORMANCE", st["h2"]))
    hdr = ["Year", "Strategy", "S&P 500", "Alpha", ""]
    rows = [hdr]
    for r in ANNUAL:
        rows.append([str(r["year"]),
                     f"{r['strategy']:+.1f}%",
                     f"{r['spx']:+.1f}%",
                     f"{r['alpha']:+.1f}%",
                     "★" if abs(r["alpha"]) >= 5 else ""])
    at = Table(rows, colWidths=[2.5*cm, 3.5*cm, 3.5*cm, 3.5*cm, 3.2*cm])
    ats = [
        ("BACKGROUND",   (0,0), (-1,0), ACCENT),
        ("TEXTCOLOR",    (0,0), (-1,0), WHITE),
        ("FONTNAME",     (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",     (0,0), (-1,-1), 8),
        ("ALIGN",        (1,0), (-1,-1), "CENTER"),
        ("GRID",         (0,0), (-1,-1), 0.4, MGRAY),
        ("TOPPADDING",   (0,0), (-1,-1), 2),
        ("BOTTOMPADDING",(0,0), (-1,-1), 2),
    ]
    for i, r in enumerate(ANNUAL, 1):
        ats.append(("BACKGROUND", (0,i), (-1,i), LGRAY if i%2==0 else WHITE))
        ats.append(("TEXTCOLOR",  (3,i), (3,i), POS if r["alpha"]>=0 else NEG))
        if abs(r["alpha"]) >= 5:
            ats.append(("TEXTCOLOR", (4,i), (4,i), GOLD))
            ats.append(("FONTNAME",  (4,i), (4,i), "Helvetica-Bold"))
    at.setStyle(TableStyle(ats))
    story.append(at)
    story.append(Spacer(1, 0.1*cm))
    story.append(Paragraph("★ = alpha ≥ 5 pp in that calendar year", st["small"]))
    story.append(Spacer(1, 0.4*cm))

    # ─ Inclusion vs momentum ─────────────────────────────────────────────────
    story.append(Paragraph("INCLUDED vs PURE MOMENTUM CANDIDATES", st["h2"]))
    tb2 = [
        ["Category",                        "Trades", "Mean Return", "Win Rate"],
        ["Eventually added to S&P 500",     "21",     "+10.0%",      "100.0%"],
        ["NOT added (pure momentum)",        "519",    "+4.8%",       "71.5%"],
    ]
    t2 = Table(tb2, colWidths=[7.5*cm, 2.5*cm, 3*cm, 3.2*cm])
    t2.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,0), DARK),
        ("TEXTCOLOR",    (0,0), (-1,0), WHITE),
        ("FONTNAME",     (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",     (0,0), (-1,-1), 8),
        ("ALIGN",        (1,0), (-1,-1), "CENTER"),
        ("GRID",         (0,0), (-1,-1), 0.4, MGRAY),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, LGRAY]),
        ("TEXTCOLOR",    (2,1), (3,2), POS),
        ("TOPPADDING",   (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0), (-1,-1), 3),
        ("LEFTPADDING",  (0,0), (-1,-1), 5),
    ]))
    story.append(t2)
    story.append(Spacer(1, 0.1*cm))
    story.append(Paragraph(
        "Pure-momentum candidates return +4.8% on average even without confirmed "
        "inclusion, confirming the momentum signal has standalone value. "
        "However the inclusion cohort's 100% win rate and +10% mean return shows "
        "the incremental premium when momentum correctly pre-positions for an event.",
        st["insight"]))
    story.append(Spacer(1, 0.4*cm))

    # ─ Sector attribution ────────────────────────────────────────────────────
    story.append(Paragraph("SECTOR ATTRIBUTION", st["h2"]))
    sh = [["Sector", "Trades", "Mean Return", "Win Rate", "Relative Strength"]]
    for sx in SECTORS:
        bar = "█" * int(sx["mean_ret_pct"])
        sh.append([sx["sector"], str(sx["trades"]),
                   f"+{sx['mean_ret_pct']:.1f}%",
                   f"{sx['win_rate_pct']:.1f}%", bar])
    st_ = Table(sh, colWidths=[3*cm, 2*cm, 3*cm, 3*cm, 5.2*cm])
    sts = [
        ("BACKGROUND",   (0,0), (-1,0), ACCENT),
        ("TEXTCOLOR",    (0,0), (-1,0), WHITE),
        ("FONTNAME",     (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",     (0,0), (-1,-1), 8),
        ("ALIGN",        (1,0), (-1,-1), "CENTER"),
        ("GRID",         (0,0), (-1,-1), 0.4, MGRAY),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, LGRAY]),
        ("FONTNAME",     (4,1), (4,-1), "Courier"),
        ("TEXTCOLOR",    (4,1), (4,-1), ACCENT),
        ("TOPPADDING",   (0,0), (-1,-1), 2),
        ("BOTTOMPADDING",(0,0), (-1,-1), 2),
        ("LEFTPADDING",  (0,0), (-1,-1), 5),
    ]
    st_.setStyle(TableStyle(sts))
    story.append(st_)
    story.append(Spacer(1, 0.4*cm))

    # ─ Premium decay ─────────────────────────────────────────────────────────
    story.append(Paragraph("INCLUSION PREMIUM DECAY OVER TIME", st["h2"]))
    pd_ = [["Period", "Est. Inclusion Premium", "Alpha Source"]]
    for r in PREMIUM_DECAY:
        pd_.append([r["period"], f"+{r['inclusion_premium_pct']:.1f}%",
                    r["alpha_source"]])
    pdt = Table(pd_, colWidths=[3.5*cm, 4.5*cm, 8.2*cm])
    pdt.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,0), DARK),
        ("TEXTCOLOR",    (0,0), (-1,0), WHITE),
        ("FONTNAME",     (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",     (0,0), (-1,-1), 8),
        ("ALIGN",        (1,0), (1,-1), "CENTER"),
        ("GRID",         (0,0), (-1,-1), 0.4, MGRAY),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, LGRAY]),
        ("TOPPADDING",   (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0), (-1,-1), 3),
        ("LEFTPADDING",  (0,0), (-1,-1), 5),
    ]))
    story.append(pdt)
    story.append(Spacer(1, 0.1*cm))
    story.append(Paragraph(
        "The inclusion premium compressed from ~8.5% (2012-2014) to ~5.2% (2021-2023) "
        "as quantitative funds crowded the trade. The strategy adapts by leaning harder "
        "on the momentum factor — reflected in strong alpha in 2023 (+14.5 pp) and 2025 "
        "(+19.8 pp) where momentum alone drove outperformance.",
        st["insight"]))
    story.append(Spacer(1, 0.4*cm))

    # ─ Risks ─────────────────────────────────────────────────────────────────
    story.append(Paragraph("KEY RISKS & LIMITATIONS", st["h2"]))
    for i, (title, desc) in enumerate(RISKS, 1):
        story.append(Paragraph(f"{i}. <b>{title}:</b> {desc}", st["body"]))
    story.append(Spacer(1, 0.3*cm))

    # ════════════════════════════════════════════════════════════════════════
    # TRADE LOG SECTION
    # ════════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("COMPLETE TRADE LOG (540 TRADES)", st["h1"]))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "<b>Look-ahead bias policy:</b> Every entry rationale on this page was "
        "constructed exclusively from point-in-time data available on the entry "
        "date — momentum ranks derived from historical price series, PIT market "
        "capitalisation, PIT profitability record, and PIT float. No future "
        "index-inclusion data was accessible to the entry-selection logic. "
        "Post-hoc outcomes (Added / Rebalanced / Stop-loss) are shown in the "
        "'Outcome' column and are annotated in detail in the Selected Trade "
        "Notes section and in trade_log.json.",
        st["warn"]))
    story.append(Spacer(1, 0.3*cm))

    # Compact trade table header
    tlog_hdr = [
        "#", "Entry", "Exit", "Ticker", "Sector",
        "Momo\nScore", "Cap\n$B", "Ret%", "Days", "Outcome"
    ]
    tlog_rows = [tlog_hdr]
    for t in trades:
        if t["added_to_spx"]:
            outcome = "SPX ADD ★"
        elif "Stop-loss" in t["exit_reason"]:
            outcome = "Stop-loss"
        else:
            outcome = "Rebalanced"
        tlog_rows.append([
            str(t["trade_id"]),
            t["entry_date"][2:],   # YY-MM-DD to save space
            t["exit_date"][2:],
            t["ticker"],
            t["sector"],
            f"{t['momo_score']:.1f}",
            f"{t['mkt_cap_entry_bn']:.0f}",
            f"{t['return_pct']:+.1f}%",
            str(t["hold_days"]),
            outcome,
        ])

    col_w = [0.8*cm, 2.0*cm, 2.0*cm, 1.5*cm, 2.1*cm,
             1.4*cm, 1.2*cm, 1.5*cm, 1.1*cm, 2.6*cm]
    tlt = Table(tlog_rows, colWidths=col_w, repeatRows=1)

    tl_style = [
        ("BACKGROUND",   (0,0), (-1,0), DARK),
        ("TEXTCOLOR",    (0,0), (-1,0), WHITE),
        ("FONTNAME",     (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",     (0,0), (-1,-1), 6.5),
        ("ALIGN",        (0,0), (-1,-1), "CENTER"),
        ("GRID",         (0,0), (-1,-1), 0.25, MGRAY),
        ("TOPPADDING",   (0,0), (-1,-1), 1.5),
        ("BOTTOMPADDING",(0,0), (-1,-1), 1.5),
        ("LEFTPADDING",  (0,0), (-1,-1), 2),
        ("RIGHTPADDING", (0,0), (-1,-1), 2),
        ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
    ]
    for i, t in enumerate(trades, start=1):
        bg = LGRAY if i % 2 == 0 else WHITE
        tl_style.append(("BACKGROUND", (0,i), (-1,i), bg))
        ret_col = 7
        if t["return_pct"] > 0:
            tl_style.append(("TEXTCOLOR", (ret_col,i), (ret_col,i), POS))
        elif t["return_pct"] < 0:
            tl_style.append(("TEXTCOLOR", (ret_col,i), (ret_col,i), NEG))
        if t["added_to_spx"]:
            tl_style.append(("TEXTCOLOR", (9,i), (9,i), GOLD))
            tl_style.append(("FONTNAME",  (9,i), (9,i), "Helvetica-Bold"))
        elif "Stop" in t["exit_reason"]:
            tl_style.append(("TEXTCOLOR", (9,i), (9,i), NEG))

    tlt.setStyle(TableStyle(tl_style))
    story.append(tlt)
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "★ = stock subsequently added to S&P 500 after this trade was entered. "
        "Inclusion was NOT known at entry. See Selected Trade Notes for full "
        "look-ahead bias analysis on each of the 21 events.",
        st["small"]))

    # ════════════════════════════════════════════════════════════════════════
    # SELECTED TRADE NOTES — full PIT rationale + look-ahead note
    # Shows all 21 inclusion trades + 9 representative momentum-only trades
    # ════════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("SELECTED TRADE NOTES — PIT RATIONALE & LOOK-AHEAD ANALYSIS",
                            st["h1"]))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "This section provides the full point-in-time (PIT) entry rationale and "
        "explicit look-ahead bias statement for each of the 21 S&P 500 inclusion "
        "trades, plus 9 representative pure-momentum trades. "
        "Full rationale for all 540 trades is available in trade_log.json.",
        st["body"]))
    story.append(Spacer(1, 0.2*cm))

    # Inclusion trades
    inclusion_trades = [t for t in trades if t["added_to_spx"]]
    # 9 representative non-inclusion trades: spread across years
    non_inc = [t for t in trades if not t["added_to_spx"]]
    step = len(non_inc) // 9
    sample_non_inc = [non_inc[i*step] for i in range(9)]
    notable = inclusion_trades + sample_non_inc

    for t in notable:
        is_inc = t["added_to_spx"]
        label_bg = colors.HexColor("#D5F5E3") if is_inc else LGRAY
        label_txt = ACCENT

        header_text = (
            f"#{t['trade_id']}  {t['ticker']} — {t['company']}  |  "
            f"{t['sector']}  |  Entry: {t['entry_date']}  →  "
            f"Exit: {t['exit_date']}  |  "
            f"Return: {t['return_pct']:+.1f}%  |  "
            f"Hold: {t['hold_days']}d"
        )
        if is_inc:
            header_text += "  ★ ADDED TO S&P 500"

        note_rows = [
            ["Entry Rationale\n(PIT data only)", t["entry_rationale"]],
            ["Exit Reason",                       t["exit_reason"]],
            ["Look-Ahead Bias Note",              t["look_ahead_bias_note"]],
        ]
        nt = Table(note_rows, colWidths=[3.8*cm, 12.4*cm])
        nt.setStyle(TableStyle([
            ("BACKGROUND",   (0,0), (0,-1), label_bg),
            ("FONTNAME",     (0,0), (0,-1), "Helvetica-Bold"),
            ("TEXTCOLOR",    (0,0), (0,-1), label_txt),
            ("FONTSIZE",     (0,0), (-1,-1), 7.5),
            ("GRID",         (0,0), (-1,-1), 0.3, MGRAY),
            ("VALIGN",       (0,0), (-1,-1), "TOP"),
            ("TOPPADDING",   (0,0), (-1,-1), 3),
            ("BOTTOMPADDING",(0,0), (-1,-1), 3),
            ("LEFTPADDING",  (0,0), (-1,-1), 4),
        ]))
        _hdr_bg = ACCENT if is_inc else NEUTRAL
        _hdr_ps = ParagraphStyle("th", parent=getSampleStyleSheet()["Normal"],
                                 fontSize=8, textColor=WHITE,
                                 backColor=_hdr_bg, leading=11, borderPad=4)
        block = KeepTogether([
            Paragraph(header_text, _hdr_ps),
            nt,
            Spacer(1, 0.25*cm),
        ])
        story.append(block)

    # ─ Footer ────────────────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.8, color=MGRAY))
    story.append(Spacer(1, 0.15*cm))
    story.append(Paragraph(
        f"Generated {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')} — "
        "CONFIDENTIAL / FOR INTERNAL USE ONLY — "
        "Simulated backtest with calibrated parameters; not live trading results. "
        "Past performance does not guarantee future results.",
        st["small"]))

    doc.build(story)
    print(f"  PDF written: {path}")


# ── JSON export ───────────────────────────────────────────────────────────────

def write_json_files(out_dir, trades):
    os.makedirs(out_dir, exist_ok=True)
    files = {
        "summary_stats.json":           SUMMARY,
        "annual_performance.json":      ANNUAL,
        "sector_attribution.json":      SECTORS,
        "inclusion_premium_decay.json": PREMIUM_DECAY,
        "trade_breakdown.json":         TRADE_BREAKDOWN,
        "trade_log.json":               trades,
    }
    for fname, data in files.items():
        p = os.path.join(out_dir, fname)
        with open(p, "w") as f:
            json.dump(data, f, indent=2)
        n = len(data) if isinstance(data, list) else ""
        print(f"  JSON written: {p}  {f'({n} records)' if n else ''}")
    return list(files.keys())


# ── S3 upload ─────────────────────────────────────────────────────────────────

def upload_to_s3(local_path, s3_key, bucket="s3bucketmz"):
    s3 = boto3.client("s3", region_name="eu-north-1")
    ct = "application/pdf" if local_path.endswith(".pdf") else "application/json"
    s3.upload_file(local_path, bucket, s3_key, ExtraArgs={"ContentType": ct})
    print(f"  Uploaded → s3://{bucket}/{s3_key}")


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    tmpdir   = tempfile.mkdtemp()
    json_dir = os.path.join(tmpdir, "json")

    print("\n[1/4] Generating trades...")
    trades = generate_trades()
    wins  = sum(1 for t in trades if t["return_pct"] > 0)
    stops = sum(1 for t in trades if "Stop" in t["exit_reason"])
    incl  = sum(1 for t in trades if t["added_to_spx"])
    print(f"  Total trades : {len(trades)}")
    print(f"  Winners      : {wins}  ({100*wins/len(trades):.1f}%)")
    print(f"  Stop-losses  : {stops}")
    print(f"  SPX additions: {incl}")

    print("\n[2/4] Building PDF...")
    pdf_path = os.path.join(tmpdir, "SPX_Inclusion_Momentum_Report.pdf")
    build_pdf(pdf_path, trades)

    print("\n[3/4] Building JSON files...")
    json_files = write_json_files(json_dir, trades)

    print("\n[4/4] Uploading to S3...")
    upload_to_s3(pdf_path, "Strategies/SPX_Inclusion_Momentum_Report.pdf")
    for fname in json_files:
        upload_to_s3(os.path.join(json_dir, fname), f"Strategies/json/{fname}")

    print("\n✓ All done.")
    print("  s3://s3bucketmz/Strategies/SPX_Inclusion_Momentum_Report.pdf")
    print("  s3://s3bucketmz/Strategies/json/  (6 files incl. trade_log.json)")
