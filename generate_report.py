"""
Generate SPX Inclusion Momentum PDF report and JSON data files,
then upload to s3://s3bucketmz/Strategies/
"""

import json
import os
import boto3
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

# ── Data ─────────────────────────────────────────────────────────────────────

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
    "backtest_period": "2012-2023",
    "generated_at": datetime.utcnow().isoformat() + "Z"
}

SECTORS = [
    {"sector": "IT",           "trades": 55, "mean_ret_pct": 7.8, "win_rate_pct": 85.5},
    {"sector": "Materials",    "trades": 60, "mean_ret_pct": 7.1, "win_rate_pct": 78.3},
    {"sector": "Cons.Disc",    "trades": 53, "mean_ret_pct": 6.5, "win_rate_pct": 75.5},
    {"sector": "Utilities",    "trades": 70, "mean_ret_pct": 5.4, "win_rate_pct": 77.1},
    {"sector": "Healthcare",   "trades": 63, "mean_ret_pct": 4.7, "win_rate_pct": 68.3},
    {"sector": "Comm.Svc",     "trades": 53, "mean_ret_pct": 4.6, "win_rate_pct": 69.8},
    {"sector": "Energy",       "trades": 61, "mean_ret_pct": 4.4, "win_rate_pct": 75.4},
    {"sector": "Industrials",  "trades": 65, "mean_ret_pct": 3.4, "win_rate_pct": 64.6},
    {"sector": "Financials",   "trades": 60, "mean_ret_pct": 1.3, "win_rate_pct": 60.0},
]

PREMIUM_DECAY = [
    {"period": "2012-2014", "inclusion_premium_pct": 8.5, "alpha_source": "Inclusion + Momentum"},
    {"period": "2015-2017", "inclusion_premium_pct": 8.5, "alpha_source": "Inclusion + Momentum"},
    {"period": "2018-2020", "inclusion_premium_pct": 6.6, "alpha_source": "Momentum-dominant"},
    {"period": "2021-2023", "inclusion_premium_pct": 5.2, "alpha_source": "Momentum-dominant"},
]

TRADE_BREAKDOWN = [
    {"category": "Eventually added to S&P 500", "n_trades": 21,  "mean_ret_pct": 10.0, "win_rate_pct": 100.0},
    {"category": "NOT added (pure momentum)",   "n_trades": 519, "mean_ret_pct": 4.8,  "win_rate_pct": 71.5},
]

RISKS = [
    ("Market Impact", "Large positions in small-cap candidates move prices; real-world capacity ~$50–200M AUM."),
    ("Crowding Risk",  "Trade is widely known; entry premium may rise, eroding the edge over time."),
    ("Classification Lag", "S&P committee has discretion — eligibility criteria alone do not guarantee inclusion timing."),
    ("Announcement Surprise", "Stocks added off-cycle (M&A, fast-track) will not appear in pre-positioned candidates."),
    ("Momentum Reversal", "High-momentum candidates can mean-revert sharply before announcement."),
    ("Simulation Caveat", "Price paths use calibrated parameters from published research; live feeds needed for production."),
]

# ── Colours ──────────────────────────────────────────────────────────────────

DARK   = colors.HexColor("#1A1A2E")
ACCENT = colors.HexColor("#0F3460")
GREEN  = colors.HexColor("#16213E")
GOLD   = colors.HexColor("#E94560")
LGRAY  = colors.HexColor("#F5F5F5")
MGRAY  = colors.HexColor("#CCCCCC")
WHITE  = colors.white
POS    = colors.HexColor("#27AE60")
NEG    = colors.HexColor("#E74C3C")
NEUTRAL= colors.HexColor("#2C3E50")

# ── PDF ──────────────────────────────────────────────────────────────────────

def build_pdf(path):
    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm
    )
    styles = getSampleStyleSheet()

    def s(name, **kw):
        return ParagraphStyle(name, parent=styles["Normal"], **kw)

    h1 = s("H1", fontSize=22, textColor=WHITE, backColor=DARK,
            spaceAfter=4, spaceBefore=0, leading=28, alignment=TA_CENTER,
            borderPad=12)
    h2 = s("H2", fontSize=13, textColor=WHITE, backColor=ACCENT,
            spaceAfter=2, spaceBefore=10, leading=18, borderPad=6)
    h3 = s("H3", fontSize=10, textColor=ACCENT, bold=True,
            spaceAfter=2, spaceBefore=6)
    body = s("Body", fontSize=9, textColor=NEUTRAL, leading=13, spaceAfter=3)
    small= s("Small", fontSize=8, textColor=colors.HexColor("#555555"), leading=11)
    bold_body = s("BoldBody", fontSize=9, textColor=NEUTRAL, bold=True, leading=13)
    insight = s("Insight", fontSize=9, textColor=colors.HexColor("#1A5276"),
                backColor=colors.HexColor("#EBF5FB"), leading=13,
                borderPad=6, spaceAfter=4, spaceBefore=4)

    story = []

    # ── Cover block ──
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("SPX INCLUSION MOMENTUM STRATEGY", h1))
    story.append(Spacer(1, 0.2*cm))

    meta = [
        ["Backtest Period", "2012 – 2026 (full data through Mar 2026)"],
        ["Rebalance",       "Quarterly, aligned with S&P change schedule"],
        ["Universe",        "S&P 400 + eligible non-index large caps (≥$12B, profitable)"],
        ["Generated",       datetime.utcnow().strftime("%d %b %Y %H:%M UTC")],
    ]
    mt = Table(meta, colWidths=[4.5*cm, 11*cm])
    mt.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,-1), LGRAY),
        ("FONTNAME",   (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 8.5),
        ("TEXTCOLOR",  (0,0), (0,-1), ACCENT),
        ("GRID",       (0,0), (-1,-1), 0.5, MGRAY),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [WHITE, LGRAY]),
        ("LEFTPADDING",  (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING",   (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0), (-1,-1), 4),
    ]))
    story.append(mt)
    story.append(Spacer(1, 0.4*cm))

    # ── Strategy rules ──
    story.append(Paragraph("STRATEGY RULES", h2))
    rules = [
        ["Entry Signal",  "Top 20% composite momentum score, 30 days before S&P change cycle"],
        ["Momentum",      "Composite = 0.6 × (12-1m rank) + 0.4 × (3m rank)"],
        ["Sizing",        "Equal weight, 4% per position, max 25 positions"],
        ["Exit (added)",  "Sell at effective date + 3 trading days"],
        ["Exit (missed)", "Hold to next quarterly rebalance, re-score and rotate"],
        ["Stop-Loss",     "Hard exit at −15% from entry"],
        ["Bias Control",  "Candidate pool built on point-in-time eligibility; non-included tracked"],
    ]
    rt = Table(rules, colWidths=[4*cm, 11.5*cm])
    rt.setStyle(TableStyle([
        ("FONTNAME",  (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTSIZE",  (0,0), (-1,-1), 8.5),
        ("TEXTCOLOR", (0,0), (0,-1), ACCENT),
        ("GRID",      (0,0), (-1,-1), 0.5, MGRAY),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [WHITE, LGRAY]),
        ("LEFTPADDING",  (0,0), (-1,-1), 6),
        ("TOPPADDING",   (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0), (-1,-1), 4),
    ]))
    story.append(rt)
    story.append(Spacer(1, 0.5*cm))

    # ── Summary stats ──
    story.append(Paragraph("SUMMARY STATISTICS", h2))
    kpis = [
        ["Metric",                         "Strategy",    "S&P 500",  "Edge"],
        ["Total Return",                   "+1,040.4%",   "+364.0%",  "+676.4%"],
        ["CAGR",                           "+17.6%",      "+10.8%",   "+6.8pp"],
        ["Sharpe Ratio",                   "1.34",        "0.77",     "+0.57"],
        ["Max Drawdown",                   "−17.1%",      "−24.7%",   "+7.6pp"],
        ["Win Rate",                       "72.6%",       "—",        "—"],
        ["Total Trades",                   "540",         "—",        "—"],
        ["Stop-Loss Hits",                 "25 (4.6%)",   "—",        "—"],
        ["Avg Holding Period",             "~75 days",    "—",        "—"],
    ]
    kt = Table(kpis, colWidths=[6*cm, 3.5*cm, 3.5*cm, 2.5*cm])
    kt.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0), DARK),
        ("TEXTCOLOR",   (0,0), (-1,0), WHITE),
        ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTNAME",    (0,1), (0,-1), "Helvetica-Bold"),
        ("TEXTCOLOR",   (0,1), (0,-1), ACCENT),
        ("FONTSIZE",    (0,0), (-1,-1), 8.5),
        ("ALIGN",       (1,0), (-1,-1), "CENTER"),
        ("GRID",        (0,0), (-1,-1), 0.5, MGRAY),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [WHITE, LGRAY]),
        ("TEXTCOLOR",   (3,1), (3,1), POS),  # total return edge
        ("TEXTCOLOR",   (3,2), (3,2), POS),
        ("TOPPADDING",  (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0), (-1,-1), 4),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(kt)
    story.append(Spacer(1, 0.5*cm))

    # ── Annual performance ──
    story.append(Paragraph("ANNUAL PERFORMANCE", h2))
    hdr = ["Year", "Strategy", "S&P 500", "Alpha", "Signal"]
    rows = [hdr]
    for r in ANNUAL:
        sig = "★" if abs(r["alpha"]) >= 5 else ""
        rows.append([
            str(r["year"]),
            f"{r['strategy']:+.1f}%",
            f"{r['spx']:+.1f}%",
            f"{r['alpha']:+.1f}%",
            sig,
        ])
    at = Table(rows, colWidths=[2.5*cm, 3.5*cm, 3.5*cm, 3.5*cm, 2.5*cm])

    ts = [
        ("BACKGROUND",  (0,0), (-1,0), ACCENT),
        ("TEXTCOLOR",   (0,0), (-1,0), WHITE),
        ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8.5),
        ("ALIGN",       (1,0), (-1,-1), "CENTER"),
        ("GRID",        (0,0), (-1,-1), 0.5, MGRAY),
        ("TOPPADDING",  (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0), (-1,-1), 3),
    ]
    for i, r in enumerate(ANNUAL, start=1):
        bg = LGRAY if i % 2 == 0 else WHITE
        ts.append(("BACKGROUND", (0,i), (-1,i), bg))
        c = POS if r["alpha"] >= 0 else NEG
        ts.append(("TEXTCOLOR", (3,i), (3,i), c))
        if abs(r["alpha"]) >= 5:
            ts.append(("TEXTCOLOR", (4,i), (4,i), GOLD))
            ts.append(("FONTNAME",  (4,i), (4,i), "Helvetica-Bold"))
    at.setStyle(TableStyle(ts))
    story.append(at)
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "★ = alpha ≥ 5% in that calendar year", small))
    story.append(Spacer(1, 0.5*cm))

    # ── Inclusion vs momentum ──
    story.append(Paragraph("INCLUDED vs PURE MOMENTUM CANDIDATES", h2))
    tb2 = [
        ["Category",                          "Trades", "Mean Return", "Win Rate"],
        ["Eventually added to S&P 500",       "21",     "+10.0%",      "100.0%"],
        ["NOT added (pure momentum)",          "519",    "+4.8%",       "71.5%"],
    ]
    t2 = Table(tb2, colWidths=[7.5*cm, 2.5*cm, 3*cm, 2.5*cm])
    t2.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0), DARK),
        ("TEXTCOLOR",   (0,0), (-1,0), WHITE),
        ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8.5),
        ("ALIGN",       (1,0), (-1,-1), "CENTER"),
        ("GRID",        (0,0), (-1,-1), 0.5, MGRAY),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [WHITE, LGRAY]),
        ("TEXTCOLOR",   (2,1), (3,1), POS),
        ("TEXTCOLOR",   (2,2), (3,2), POS),
        ("TOPPADDING",  (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0), (-1,-1), 4),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(t2)
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "→ Pure-momentum candidates deliver +4.8% mean return even without confirmed inclusion, "
        "confirming the momentum signal has standalone value beyond the inclusion event premium.",
        insight))
    story.append(Spacer(1, 0.4*cm))

    # ── Sector attribution ──
    story.append(Paragraph("SECTOR ATTRIBUTION", h2))
    sh = [["Sector", "Trades", "Mean Return", "Win Rate", "Relative Strength"]]
    for s_ in SECTORS:
        bar = "█" * int(s_["mean_ret_pct"] / 1.0)
        sh.append([s_["sector"], str(s_["trades"]),
                   f"+{s_['mean_ret_pct']:.1f}%",
                   f"{s_['win_rate_pct']:.1f}%", bar])
    st_ = Table(sh, colWidths=[3*cm, 2*cm, 3*cm, 3*cm, 4.5*cm])
    sts = [
        ("BACKGROUND",  (0,0), (-1,0), ACCENT),
        ("TEXTCOLOR",   (0,0), (-1,0), WHITE),
        ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8.5),
        ("ALIGN",       (1,0), (-1,-1), "CENTER"),
        ("GRID",        (0,0), (-1,-1), 0.5, MGRAY),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [WHITE, LGRAY]),
        ("FONTNAME",    (4,1), (4,-1), "Courier"),
        ("TEXTCOLOR",   (4,1), (4,-1), ACCENT),
        ("TOPPADDING",  (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0), (-1,-1), 3),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
    ]
    st_.setStyle(TableStyle(sts))
    story.append(st_)
    story.append(Spacer(1, 0.5*cm))

    # ── Premium decay ──
    story.append(Paragraph("INCLUSION PREMIUM DECAY OVER TIME", h2))
    pd_ = [["Period", "Est. Inclusion Premium", "Alpha Source"]]
    for r in PREMIUM_DECAY:
        pd_.append([r["period"], f"+{r['inclusion_premium_pct']:.1f}%", r["alpha_source"]])
    pdt = Table(pd_, colWidths=[3.5*cm, 4.5*cm, 7.5*cm])
    pdt.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0), DARK),
        ("TEXTCOLOR",   (0,0), (-1,0), WHITE),
        ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8.5),
        ("ALIGN",       (1,0), (1,-1), "CENTER"),
        ("GRID",        (0,0), (-1,-1), 0.5, MGRAY),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [WHITE, LGRAY]),
        ("TOPPADDING",  (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0), (-1,-1), 4),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(pdt)
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "The inclusion premium has compressed from ~8.5% (2012-2014) to ~5.2% (2021-2023) as "
        "quantitative funds entered the trade. The strategy compensates by leaning harder on the "
        "momentum factor — evidenced by strong recent alpha in 2023 (+14.5%) and 2025 (+19.8%).",
        insight))
    story.append(Spacer(1, 0.5*cm))

    # ── Risks ──
    story.append(Paragraph("KEY RISKS & LIMITATIONS", h2))
    for i, (title, desc) in enumerate(RISKS, 1):
        story.append(Paragraph(f"{i}. <b>{title}:</b> {desc}", body))
    story.append(Spacer(1, 0.4*cm))

    # ── Footer ──
    story.append(HRFlowable(width="100%", thickness=1, color=MGRAY))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        f"Generated {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')} — "
        "CONFIDENTIAL / FOR INTERNAL USE ONLY — "
        "Past performance does not guarantee future results.",
        small))

    doc.build(story)
    print(f"  PDF written: {path}")


# ── JSON files ────────────────────────────────────────────────────────────────

def write_json_files(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    files = {
        "summary_stats.json":          SUMMARY,
        "annual_performance.json":     ANNUAL,
        "sector_attribution.json":     SECTORS,
        "inclusion_premium_decay.json":PREMIUM_DECAY,
        "trade_breakdown.json":        TRADE_BREAKDOWN,
    }
    for fname, data in files.items():
        path = os.path.join(out_dir, fname)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  JSON written: {path}")
    return list(files.keys())


# ── S3 upload ─────────────────────────────────────────────────────────────────

def upload_to_s3(local_path, s3_key, bucket="s3bucketmz"):
    s3 = boto3.client("s3", region_name="eu-north-1")
    ct = "application/pdf" if local_path.endswith(".pdf") else "application/json"
    s3.upload_file(local_path, bucket, s3_key,
                   ExtraArgs={"ContentType": ct})
    print(f"  Uploaded → s3://{bucket}/{s3_key}")


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    tmpdir = tempfile.mkdtemp()
    json_dir = os.path.join(tmpdir, "json")

    print("\n[1/3] Building PDF...")
    pdf_path = os.path.join(tmpdir, "SPX_Inclusion_Momentum_Report.pdf")
    build_pdf(pdf_path)

    print("\n[2/3] Building JSON files...")
    json_files = write_json_files(json_dir)

    print("\n[3/3] Uploading to S3...")
    upload_to_s3(pdf_path, "Strategies/SPX_Inclusion_Momentum_Report.pdf")
    for fname in json_files:
        upload_to_s3(os.path.join(json_dir, fname), f"Strategies/json/{fname}")

    print("\n✓ All done.")
    print("  s3://s3bucketmz/Strategies/SPX_Inclusion_Momentum_Report.pdf")
    print("  s3://s3bucketmz/Strategies/json/  (5 files)")
