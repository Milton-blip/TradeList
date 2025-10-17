# portfolio_trades/engine.py
from __future__ import annotations

import re
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from .conventions import (
    MAP_TO_SLEEVE,
    FALLBACK_PROXY,
    ACCOUNT_TAX_STATUS_RULES,
    DEFAULT_TAX_STATUS,
    is_cashlike,
    is_automattic,
)


# -------------------------
# Helpers
# -------------------------
def assign_tax_status(acct: str) -> str:
    if not isinstance(acct, str):
        return DEFAULT_TAX_STATUS
    low = acct.lower()
    for pat, status in ACCOUNT_TAX_STATUS_RULES:
        if re.search(pat, low):
            return status
    return DEFAULT_TAX_STATUS


def map_sleeve(sym: str, name: str) -> str:
    s = str(sym).upper().strip()
    n = str(name).upper().strip()
    if is_automattic(s, n):
        return "Illiquid_Automattic"
    if s in MAP_TO_SLEEVE:
        return MAP_TO_SLEEVE[s]
    if "INFLATION" in n:
        return "TIPS"
    if any(k in n for k in ["UST", "TREAS", "STRIP"]):
        return "Treasuries"
    return "US_Core"


def _round_shares(dollars: float, px: float, ident: str) -> float:
    if px <= 0 or not np.isfinite(px):
        return 0.0
    return round(dollars / px, 2) if is_cashlike(ident) else round(dollars / px, 1)


# -------------------------
# Core engine
# -------------------------
def build_trades_and_afterholdings(
    h: pd.DataFrame,
    W: pd.Series,
    cash_tolerance: float = 100.0,
):
    """
    Compute trades (per account) to move toward portfolio-wide targets while:
      - prohibiting trades in Automattic
      - no inter-account transfers
      - per-account net cash flow neutralized by a CASH trade
      - realized gains computed from AverageCost (per-share)

    Inputs (canonical columns expected from io_utils.load_holdings):
      Account, TaxStatus, Name, Symbol, Quantity, Price, AverageCost,
      Value (=Quantity*Price), Cost (=Quantity*AverageCost)
    """
    # Copy and normalize expected columns
    df = h.copy()
    required = ["Account", "Name", "Symbol", "Quantity", "Price", "AverageCost", "Value"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SystemExit(f"holdings missing required columns: {missing}")

    # Ensure TaxStatus exists
    if "TaxStatus" not in df.columns or df["TaxStatus"].eq("").all():
        df["TaxStatus"] = df["Account"].map(assign_tax_status)

    # Create sleeves and identifier
    df["Sleeve"] = [map_sleeve(s, n) for s, n in zip(df["Symbol"], df["Name"])]
    df["_ident"] = df["Symbol"].astype(str)

    # Canonical ident to trade **within each account & sleeve** (pick largest $ there)
    acct_sleeve_ident: Dict[Tuple[str, str], str] = (
        df.groupby(["Account", "Sleeve", "_ident"])["Value"]
        .sum()
        .reset_index()
        .sort_values(["Account", "Sleeve", "Value"], ascending=[True, True, False])
        .drop_duplicates(["Account", "Sleeve"])
        .set_index(["Account", "Sleeve"])["_ident"]
        .to_dict()
    )

    # Price per ident
    price_map = df.groupby("_ident")["Price"].median().to_dict()

    # Total value per account (for sizing)
    acct_total = df.groupby("Account")["Value"].sum()

    trades = []

    # Target sizing is portfolio-wide; per-account invests only its investable pool
    for acct, g in df.groupby("Account"):
        total_val = float(acct_total.get(acct, 0.0))
        if total_val <= 0:
            continue

        # Illiquid Automattic dollars in this account (held, not traded)
        illq_val = g.loc[
            [is_automattic(s, n) for s, n in zip(g["Symbol"], g["Name"])], "Value"
        ].sum()

        investable = max(0.0, total_val - float(illq_val))

        # Remove illiquid from investable target weights
        W_inv = W.copy()
        if "Illiquid_Automattic" in W_inv.index:
            W_inv = W_inv.drop(index="Illiquid_Automattic")
        denom = W_inv.sum()
        if denom <= 0:
            continue
        W_inv = W_inv / denom

        tgt_val = W_inv * investable
        cur_val = g.groupby("Sleeve")["Value"].sum()

        sleeves = sorted(set(cur_val.index).union(tgt_val.index))
        cur = cur_val.reindex(sleeves).fillna(0.0)
        tgt = tgt_val.reindex(sleeves).fillna(0.0)

        # Keep illiquid fixed
        if "Illiquid_Automattic" in cur.index:
            tgt.loc["Illiquid_Automattic"] = cur.loc["Illiquid_Automattic"]

        # Sleeve deltas (dollars)
        delta = (tgt - cur)

        # Generate per-sleeve trades (excluding illiquid)
        for sleeve, d_dollars in delta.items():
            if sleeve == "Illiquid_Automattic":
                continue
            ident = acct_sleeve_ident.get((acct, sleeve)) or FALLBACK_PROXY.get(sleeve)
            if ident is None:
                continue

            px = float(price_map.get(ident, 0.0))
            if not np.isfinite(px) or px <= 0:
                continue

            # Skip micro-noise
            if abs(d_dollars) < 1.0:
                continue

            sh = _round_shares(d_dollars, px, ident)
            if sh == 0.0:
                continue

            # SELLs cannot exceed shares in THIS account
            if d_dollars < 0:
                held_sh = float(g.loc[g["_ident"] == ident, "Quantity"].sum())
                sh = -min(abs(sh), abs(held_sh))  # negative shares for sell
                if sh == 0.0:
                    continue

            # Account-level weighted average cost per share for this ident
            rows_ident = g[g["_ident"] == ident]
            if not rows_ident.empty and rows_ident["Quantity"].sum() > 0:
                tot_sh = float(rows_ident["Quantity"].sum())
                avgc = float(
                    (rows_ident["AverageCost"] * rows_ident["Quantity"]).sum() / tot_sh
                )
            else:
                avgc = 0.0

            action = "BUY" if sh > 0 else "SELL"
            capgain = (px - avgc) * abs(sh) if action == "SELL" else 0.0

            trades.append(
                {
                    "Account": acct,
                    "TaxStatus": g["TaxStatus"].iloc[0],
                    "Identifier": ident,
                    "Sleeve": sleeve,
                    "Action": action,
                    "Shares_Delta": sh,
                    "Price": px,           # per-share
                    "AverageCost": avgc,   # per-share
                    "Delta_Dollars": sh * px,
                    "CapGain_Dollars": capgain,
                }
            )

        # ---------- Per-account CASH balancing ----------
        acct_net = sum(t["Delta_Dollars"] for t in trades if t["Account"] == acct)
        if abs(acct_net) > cash_tolerance:
            cash_ident = acct_sleeve_ident.get((acct, "Cash")) or FALLBACK_PROXY.get(
                "Cash", "BIL"
            )
            cash_px = float(price_map.get(cash_ident, 1.0))
            if not np.isfinite(cash_px) or cash_px <= 0:
                cash_px = 1.0
            sh_cash = _round_shares(-acct_net, cash_px, cash_ident)  # offset to zero
            if sh_cash != 0.0:
                trades.append(
                    {
                        "Account": acct,
                        "TaxStatus": g["TaxStatus"].iloc[0],
                        "Identifier": cash_ident,
                        "Sleeve": "Cash",
                        "Action": "BUY" if sh_cash > 0 else "SELL",
                        "Shares_Delta": sh_cash,
                        "Price": cash_px,
                        "AverageCost": 0.0,
                        "Delta_Dollars": sh_cash * cash_px,
                        "CapGain_Dollars": 0.0,
                    }
                )

    tx = pd.DataFrame(trades)

    if tx.empty:
        after = df.copy()
        return tx, after, {}

    # Aggregate after cash balancing
    tx = (
        tx.groupby(["Account", "Identifier", "TaxStatus", "Sleeve"], as_index=False)
        .agg(
            Shares_Delta=("Shares_Delta", "sum"),
            Price=("Price", "last"),
            AverageCost=("AverageCost", "last"),
            Delta_Dollars=("Delta_Dollars", "sum"),
            CapGain_Dollars=("CapGain_Dollars", "sum"),
        )
        .copy()
    )
    tx["Action"] = np.where(tx["Shares_Delta"] >= 0, "BUY", "SELL")

    # ---------- Build holdings-after by applying share deltas ----------
    after = df.copy()
    after["_key"] = after["Account"].astype(str) + "||" + after["_ident"].astype(str)

    share_deltas = (
        tx.assign(_key=tx["Account"].astype(str) + "||" + tx["Identifier"].astype(str))
        .groupby("_key")["Shares_Delta"]
        .sum()
        .to_dict()
    )

    # Ensure any (Account, Identifier) traded but not held gets a placeholder row
    have_keys = set(after["_key"])
    need_keys = set(share_deltas.keys()) - have_keys
    if need_keys:
        inv_proxy = {v: k for k, v in FALLBACK_PROXY.items()}
        add_rows = []
        for k in need_keys:
            acct, ident = k.split("||", 1)
            sleeve_guess = inv_proxy.get(ident, "US_Core")
            px = float(df.loc[df["_ident"] == ident, "Price"].median())
            if not np.isfinite(px) or px <= 0:
                px = 1.0
            add_rows.append(
                {
                    "Account": acct,
                    "TaxStatus": assign_tax_status(acct),
                    "Name": ident,
                    "Symbol": ident,
                    "Sleeve": sleeve_guess,
                    "_ident": ident,
                    "Quantity": 0.0,
                    "Price": px,
                    "AverageCost": 0.0,
                    "Value": 0.0,
                    "Cost": 0.0,
                    "_key": k,
                }
            )
        after = pd.concat([after, pd.DataFrame(add_rows)], ignore_index=True)

    def _apply(group: pd.DataFrame) -> pd.DataFrame:
        acct = group["Account"].iloc[0]
        ident = group["_ident"].iloc[0]
        k = f"{acct}||{ident}"
        sh = float(share_deltas.get(k, 0.0))
        if sh == 0.0:
            return group
        g = group.copy()
        g.loc[:, "Quantity"] = g["Quantity"] + sh
        g.loc[:, "Value"] = g["Quantity"] * g["Price"]
        g.loc[:, "Cost"] = g["Quantity"] * g["AverageCost"]
        return g

    after = after.groupby(["Account", "_ident"], group_keys=False).apply(_apply)
    after = after[after["Quantity"].abs() > 1e-9].copy()
    after["Value"] = after["Quantity"] * after["Price"]
    after["Cost"] = after["Quantity"] * after["AverageCost"]

    # Residual cash (should be ~0 now)
    flow = tx.groupby("Account")["Delta_Dollars"].sum()
    residuals = {acct: float(v) for acct, v in flow.items() if abs(v) > cash_tolerance}

    return tx, after, residuals