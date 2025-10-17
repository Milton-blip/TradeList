# portfolio_trades/report_pdf.py
from __future__ import annotations

from fpdf import FPDF
from fpdf.enums import XPos, YPos
import pandas as pd

from .fonts import ensure_unicode_font


def _fmt_currency(x) -> str:
    try:
        v = float(x)
    except Exception:
        v = 0.0
    s = f"${abs(v):,.2f}"
    return f"({s})" if v < 0 else s


def _fmt_number(x) -> str:
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return "0"


def _normalize_money_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Accept either Delta_Dollars/CapGain_Dollars or Delta_$/CapGain_$.
    Return a copy with Delta_Dollars and CapGain_Dollars present.
    """
    if df is None or df.empty:
        return df
    rename = {}
    if "Delta_Dollars" not in df.columns and "Delta_$" in df.columns:
        rename["Delta_$"] = "Delta_Dollars"
    if "CapGain_Dollars" not in df.columns and "CapGain_$" in df.columns:
        rename["CapGain_$"] = "CapGain_Dollars"
    if rename:
        return df.rename(columns=rename)
    return df


def render_pdf(tx: pd.DataFrame,
               acc_sum: pd.DataFrame,
               by_status: pd.DataFrame,
               vol_pct_tag: int,
               outfile: str):
    """
    tx: trades detail (must have Account, TaxStatus, Identifier, Sleeve, Action,
        Shares_Delta, Price, AverageCost, and either Delta_Dollars/CapGain_Dollars
        or Delta_$/CapGain_$ which will be normalized).
    acc_sum: per-account summary with Total_Buys, Total_Sells, Net_CapGain, Est_Tax
    by_status: summary grouped by TaxStatus (same columns as acc_sum minus Account)
    """
    # --- normalize columns for money ---
    tx = _normalize_money_cols(tx)

    # Safety: ensure required columns exist
    needed = [
        "Account", "TaxStatus", "Identifier", "Sleeve", "Action",
        "Shares_Delta", "Price", "AverageCost", "Delta_Dollars", "CapGain_Dollars"
    ]
    missing = [c for c in needed if c not in tx.columns]
    if missing:
        raise SystemExit(f"PDF render missing required columns in tx: {missing}")

    font_path = ensure_unicode_font()
    pdf = FPDF(orientation="P", unit="mm", format="Letter")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.add_font("Unicode", "", font_path)
    pdf.set_font("Unicode", size=12)

    # Use a plain hyphen to avoid older font/core enc issues
    title = f"Transaction List - Target {vol_pct_tag}% Vol (Real Terms)"
    pdf.cell(0, 8, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Table layout
    cols = [
        ("Identifier",    32, "L"),
        ("Sleeve",        24, "L"),
        ("Action",        14, "C"),
        ("Shares_Delta",  20, "R"),
        ("Price",         20, "R"),
        ("AverageCost",   20, "R"),
        ("Delta_Dollars", 24, "R"),
        ("CapGain_Dollars", 24, "R"),
    ]

    def right_cell(w, h, t, b=0):
        pdf.cell(w, h, t, border=b, align="R")

    def header():
        pdf.set_font("Unicode", size=10)
        for (h, w, a) in cols:
            pdf.cell(w, 7, h, border=1, align=a)
        pdf.ln(7)

    def row(r):
        pdf.set_font("Unicode", size=9)
        vals = [
            r["Identifier"],
            r["Sleeve"],
            r["Action"],
            _fmt_number(r["Shares_Delta"]),
            _fmt_currency(r["Price"]),
            _fmt_currency(r["AverageCost"]),
            _fmt_currency(r["Delta_Dollars"]),
            _fmt_currency(r["CapGain_Dollars"]),
        ]
        for (hdr, w, a), v in zip(cols, vals):
            (right_cell(w, 7, str(v), 1) if a == "R"
             else pdf.cell(w, 7, str(v), border=1, align=a))
        pdf.ln(7)

    def kv(label, value):
        pdf.set_font("Unicode", size=10)
        pdf.cell(65, 6, label, border=0, align="L")
        right_cell(40, 6, value, 0)
        pdf.ln(6)

    # Per-account sections
    for (acct, tax), g in tx.sort_values(
        ["Account", "Action", "Sleeve", "Identifier"]
    ).groupby(["Account", "TaxStatus"]):

        pdf.ln(2)
        pdf.set_font("Unicode", size=11)
        pdf.cell(0, 7, f"Account: {acct}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Unicode", size=10)
        pdf.cell(0, 6, f"Tax Status: {tax}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        header()
        for _, r in g.iterrows():
            row(r)

        s = acc_sum[(acc_sum["Account"] == acct) & (acc_sum["TaxStatus"] == tax)]
        if not s.empty:
            srow = s.iloc[0]
            pdf.ln(2)
            kv("Total Buys",                _fmt_currency(srow["Total_Buys"]))
            kv("Total Sells",               _fmt_currency(srow["Total_Sells"]))
            kv("Net Realized Capital Gain", _fmt_currency(srow["Net_CapGain"]))
            kv("Est Cap Gains Tax",         _fmt_currency(srow["Est_Tax"]))
            pdf.ln(2)

    # Tax status summary
    pdf.ln(4)
    pdf.set_font("Unicode", size=11)
    pdf.cell(0, 7, "Tax Status Summary", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    ts_cols = [
        ("Tax Status", 40, "L"),
        ("Total Buys", 35, "R"),
        ("Total Sells", 35, "R"),
        ("Net CapGain", 35, "R"),
        ("Est Tax", 35, "R"),
    ]
    for h, w, a in ts_cols:
        pdf.cell(w, 7, h, 1, align=a)
    pdf.ln(7)

    pdf.set_font("Unicode", size=9)
    for _, r in by_status.iterrows():
        vals = [
            r["TaxStatus"],
            _fmt_currency(r["Total_Buys"]),
            _fmt_currency(r["Total_Sells"]),
            _fmt_currency(r["Net_CapGain"]),
            _fmt_currency(r["Est_Tax"]),
        ]
        for (h, w, a), v in zip(ts_cols, vals):
            (right_cell(w, 7, str(v), 1) if a == "R"
             else pdf.cell(w, 7, str(v), 1, align=a))
        pdf.ln(7)

    pdf.output(outfile)