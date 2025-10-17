from __future__ import annotations
from fpdf import FPDF
from .fonts import register_pdf_font

def _fmt_currency(x):
    s = f"${abs(float(x)):,.2f}"
    return f"({s})" if float(x) < 0 else s

def _fmt_number(x):
    return f"{float(x):,.0f}"

def render_pdf(tx, acc_sum, by_status, vol_pct_tag: int, outfile: str):
    pdf = FPDF(orientation="P", unit="mm", format="Letter")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    # Register Unicode font from a TEMPORARY copy (no stale .pkl reuse)
    register_pdf_font(pdf)

    # Title (ASCII hyphen; no em-dash)
    title = f"Transaction List - Target {vol_pct_tag}% Vol (Real Terms)"
    pdf.cell(0, 8, title)
    pdf.ln(8)

    # Column layout (fits portrait width)
    cols = [
        ("Identifier",   32, "L"),
        ("Sleeve",       24, "L"),
        ("Action",       14, "C"),
        ("Shares_Delta", 20, "R"),
        ("Price",        20, "R"),
        ("AverageCost",  20, "R"),
        ("Delta_Dollars",24, "R"),
        ("CapGain_Dollars", 24, "R"),
    ]

    def _cell_r(w, h, t, b=1):
        pdf.cell(w, h, str(t), border=b, align="R")

    def header_row():
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
            _cell_r(w, 7, v) if a == "R" else pdf.cell(w, 7, str(v), border=1, align=a)
        pdf.ln(7)

    def kv(label, value):
        pdf.set_font("Unicode", size=10)
        pdf.cell(65, 6, label, border=0, align="L")
        _cell_r(40, 6, value, b=0)
        pdf.ln(6)

    # Guard: if Action missing or duplicated, compute from shares (BUY/SELL) and de-dup columns
    if "Action" not in tx.columns:
        tx = tx.copy()
        tx["Action"] = (tx["Shares_Delta"] >= 0).map({True: "BUY", False: "SELL"})
    if tx.columns.duplicated().any():
        tx = tx.loc[:, ~tx.columns.duplicated(keep="first")]

    # Per-account sections
    for (acct, tax), g in tx.sort_values(["Account", "Action", "Sleeve", "Identifier"]).groupby(["Account", "TaxStatus"]):
        pdf.ln(2)
        pdf.set_font("Unicode", size=11)
        pdf.cell(0, 7, f"Account: {acct}")
        pdf.ln(7)
        pdf.set_font("Unicode", size=10)
        pdf.cell(0, 6, f"Tax Status: {tax}")
        pdf.ln(6)

        header_row()
        for _, r in g.iterrows():
            row(r)

        s = acc_sum[(acc_sum["Account"] == acct) & (acc_sum["TaxStatus"] == tax)]
        if not s.empty:
            s = s.iloc[0]
            pdf.ln(2)
            kv("Total Buys",                _fmt_currency(s["Total_Buys"]))
            kv("Total Sells",               _fmt_currency(s["Total_Sells"]))
            kv("Net Realized Capital Gain", _fmt_currency(s["Net_CapGain"]))
            kv("Est Cap Gains Tax",         _fmt_currency(s["Est_Tax"]))
            pdf.ln(2)

    # Tax status summary
    pdf.ln(4)
    pdf.set_font("Unicode", size=11)
    pdf.cell(0, 7, "Tax Status Summary")
    pdf.ln(7)

    ts_cols = [
        ("Tax Status", 40, "L"),
        ("Total Buys", 35, "R"),
        ("Total Sells",35, "R"),
        ("Net CapGain",35, "R"),
        ("Est Tax",    35, "R"),
    ]
    for h, w, a in ts_cols:
        pdf.cell(w, 7, h, border=1, align=a)
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
            _cell_r(w, 7, v) if a == "R" else pdf.cell(w, 7, str(v), border=1, align=a)
        pdf.ln(7)

    pdf.output(outfile)