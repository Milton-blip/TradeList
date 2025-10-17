# portfolio_trades/cli.py
"""
CLI for PortfolioAnalytics2 trade generation.

Workflow:
  1) Load holdings and portfolio-wide target mix
  2) Compute trades per account (no inter-account transfers)
  3) Write CSV, holdings-after, and PDF summaries
"""
from pathlib import Path
import argparse
import pandas as pd

from .io_utils import (
    load_holdings,
    load_targets,
    ensure_outdir,
    write_csv,
    write_holdings,
    today_str,
)
from .engine import build_trades_and_afterholdings
from .report_pdf import render_pdf
from .conventions import DEFAULT_CASH_TOL


def _tax_rate_from_status(status: str) -> float:
    """
    Robust 0% for Roth/HSA regardless of formatting; 20% Trust; 15% Taxable; 0% otherwise.
    """
    s = (status or "").strip().lower()
    if "roth" in s or "hsa" in s:
        return 0.0
    if "trust" in s:
        return 0.20
    if "taxable" in s:
        return 0.15
    return 0.0


def main():
    parser = argparse.ArgumentParser(
        description="Generate per-account trade lists to reach the portfolio-wide target mix"
    )
    parser.add_argument(
        "--vol",
        type=float,
        default=0.08,
        help="Target volatility (e.g. 0.08 = 8%%)",  # '%%' escapes percent for argparse
    )
    parser.add_argument(
        "--cash-tol",
        type=float,
        default=DEFAULT_CASH_TOL,
        help="Per-account cash tolerance in $",
    )
    parser.add_argument(
        "--holdings",
        type=str,
        default="data/holdings.csv",
        help="Path to holdings CSV (default: data/holdings.csv)",
    )
    args = parser.parse_args()

    vol_pct_tag = int(round(args.vol * 100))
    print(f"Target volatility: {vol_pct_tag}%")

    # === Load inputs ===
    h: pd.DataFrame = load_holdings(args.holdings)
    W = load_targets(vol_pct_tag)

    # === Engine ===
    tx, after, residuals = build_trades_and_afterholdings(
        h, W, cash_tolerance=args.cash_tol
    )

    # === Outputs ===
    outdir = ensure_outdir()
    date = today_str()
    base = Path.cwd().name

    # CSV (rename money columns to _$
    tx_out = tx.rename(
        columns={"Delta_Dollars": "Delta_$", "CapGain_Dollars": "CapGain_$"}
    )
    csv_out = outdir / f"{base}_Trades_{date}.csv"
    write_csv(tx_out, csv_out)
    print(f"CSV written: {csv_out}")

    hold_after_out = outdir / f"holdings_aftertrades_{date}.csv"
    write_holdings(after, hold_after_out)
    print(f"Holdings-after written: {hold_after_out}")

    # Residual cash warnings
    for acct, amt in residuals.items():
        sign = "-" if amt < 0 else ""
        print(f"[WARN] Residual cash flow in '{acct}': {sign}${abs(amt):,.2f}")

    # === PDF ===
    if not tx.empty:
        tx_pdf = tx.copy()
        tx_pdf["Buy_$"] = tx_pdf["Delta_Dollars"].where(tx_pdf["Action"] == "BUY", 0.0)
        tx_pdf["Sell_$"] = (-tx_pdf["Delta_Dollars"]).where(
            tx_pdf["Action"] == "SELL", 0.0
        )

        acc_sum = (
            tx_pdf.groupby(["Account", "TaxStatus"], as_index=False)
            .agg(
                Total_Buys=("Buy_$", "sum"),
                Total_Sells=("Sell_$", "sum"),
                Net_CapGain=("CapGain_Dollars", "sum"),
            )
            .copy()
        )
        acc_sum["Est_Tax"] = acc_sum.apply(
            lambda r: _tax_rate_from_status(r["TaxStatus"]) * r["Net_CapGain"], axis=1
        )

        by_status = (
            tx_pdf.groupby("TaxStatus", as_index=False)
            .agg(
                Total_Buys=("Buy_$", "sum"),
                Total_Sells=("Sell_$", "sum"),
                Net_CapGain=("CapGain_Dollars", "sum"),
            )
            .copy()
        )
        by_status["Est_Tax"] = by_status.apply(
            lambda r: _tax_rate_from_status(r["TaxStatus"]) * r["Net_CapGain"], axis=1
        )

        pdf_out = outdir / f"{base}_{vol_pct_tag}vol_{date}.pdf"
        render_pdf(tx_out, acc_sum, by_status, vol_pct_tag, str(pdf_out))
        print(f"PDF written: {pdf_out}")
    else:
        print("No trades; PDF skipped.")


if __name__ == "__main__":
    main()