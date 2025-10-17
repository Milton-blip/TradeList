# portfolio_trades/cli.py
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

from .io_utils import load_holdings, load_targets, ensure_outdir, write_csv, write_holdings, today_str
from .engine import build_trades_and_afterholdings
from .report_pdf import render_pdf
from .conventions import EST_TAX_RATE

def _tax_rate_for_status(status: str) -> float:
    """HSAs and Roth IRAs => 0% capital gains tax. Others via EST_TAX_RATE."""
    s = (status or "").lower()
    if "hsa" in s or "roth" in s:
        return 0.0
    if "trust" in s:
        return EST_TAX_RATE.get("Trust", 0.20)
    if "taxable" in s:
        return EST_TAX_RATE.get("Taxable", 0.15)
    # Fallback: conservative 0 for unknown tax-advantaged labels, else taxable baseline
    return 0.0

def main():
    parser = argparse.ArgumentParser(prog="TradesList")
    parser.add_argument("--vol", type=float, default=0.08, help="Target volatility (e.g. 0.08 = 8%%)")
    parser.add_argument("--holdings", type=str, default="holdings.csv")
    parser.add_argument("--outdir", type=str, default="outputs")
    parser.add_argument("--cash-tol", type=float, default=100.0)
    args = parser.parse_args()

    vol_pct_tag = int(round(args.vol * 100))
    print(f"Target volatility: {vol_pct_tag}%")

    outdir = ensure_outdir(args.outdir)

    # Load inputs
    h = load_holdings(args.holdings)
    W = load_targets(vol_pct_tag)

    # Build trades + after-holdings
    tx, after, residuals = build_trades_and_afterholdings(h, W, cash_tolerance=args.cash_tol)

    # Write CSV outputs
    base = Path.cwd().name
    date = today_str()
    csv_out = outdir / f"{base}_Trades_{date}.csv"
    after_out = outdir / f"holdings_aftertrades_{date}.csv"

    tx_out = tx.copy()
    # Normalize expected columns
    expect_cols = ["Account","TaxStatus","Identifier","Sleeve","Action",
                   "Shares_Delta","Price","AverageCost","Delta_Dollars","CapGain_Dollars"]
    for c in expect_cols:
        if c not in tx_out.columns:
            tx_out[c] = 0.0 if c not in ["Account","TaxStatus","Identifier","Sleeve","Action"] else ""

    write_csv(tx_out[expect_cols], csv_out)
    print(f"CSV written: {csv_out}")

    write_holdings(after, after_out)
    print(f"Holdings-after written: {after_out}")

    # Residual cash warnings
    for acct, v in residuals.items():
        s = f"${abs(v):,.2f}"
        s = f"({s})" if v < 0 else s
        print(f"[WARN] Residual cash flow in '{acct}': {s}")

    # Summaries for PDF
    tx_num = tx_out.copy()
    for c in ["Shares_Delta","Price","AverageCost","Delta_Dollars","CapGain_Dollars"]:
        tx_num[c] = pd.to_numeric(tx_num[c], errors="coerce").fillna(0.0)

    # Per-account summary (buys, sells, realized gains, est tax)
    def _buys(x):  return float(x[x > 0].sum())
    def _sells(x): return float(-x[x < 0].sum())

    acc_sum = (
        tx_num.groupby(["Account","TaxStatus"], as_index=False)
              .agg(Total_Buys=("Delta_Dollars", _buys),
                   Total_Sells=("Delta_Dollars", _sells),
                   Net_CapGain=("CapGain_Dollars","sum"))
    )
    acc_sum["Est_TaxRate"] = acc_sum["TaxStatus"].apply(_tax_rate_for_status)
    acc_sum["Est_Tax"] = acc_sum["Net_CapGain"] * acc_sum["Est_TaxRate"]

    # By-status rollup using the already-taxed per-account numbers
    by_status = (
        acc_sum.groupby("TaxStatus", as_index=False)
               .agg(Total_Buys=("Total_Buys","sum"),
                    Total_Sells=("Total_Sells","sum"),
                    Net_CapGain=("Net_CapGain","sum"),
                    Est_Tax=("Est_Tax","sum"))
    )

    # Render PDF
    pdf_out = outdir / f"{base}_{vol_pct_tag}vol_{date}.pdf"
    render_pdf(tx_out[expect_cols + ["Action"]], acc_sum, by_status, vol_pct_tag, str(pdf_out))
    print(f"PDF written: {pdf_out}")

if __name__ == "__main__":
    main()