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
    EST_TAX_RATE,
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
    if px <= 0:
        return 0.0
    return round(dollars / px, 2) if is_cashlike(ident) else round(dollars / px, 1)


# -------------------------
# Core engine
# -------------------------
def build_trades_and_afterholdings(df: pd.DataFrame, W_inv: pd.DataFrame, cash_tolerance: float = 1e-2):
    import numpy as np
    import pandas as pd

    df = df.copy()
    df["_ident"] = df["Identifier"]
    df["Sleeve"] = df["Sleeve"].fillna("")

    sleeves_from_targets = [str(x) for x in list(W_inv.index) if not pd.isna(x)]
    sleeves_from_holdings = df["Sleeve"].dropna().astype(str).tolist()
    sleeves_all = sorted(set(sleeves_from_targets + sleeves_from_holdings))

    tx_list = []

    price_map = df.groupby("_ident")["Price"].median().to_dict()
    cost_map = df.groupby("_ident")["AverageCost"].median().to_dict()

    for s in sleeves_all:
        sub = df[df["Sleeve"] == s]
        if sub.empty:
            continue
        sleeve_weight = W_inv.loc[s, "TargetWeight"] if s in W_inv.index else 0.0
        total_val = df["Value"].sum()
        target_val = total_val * sleeve_weight
        acct_vals = sub.groupby("Account")["Value"].sum()
        acct_weights = acct_vals / acct_vals.sum() if acct_vals.sum() != 0 else 0
        for acct, val in acct_vals.items():
            target_acct_val = target_val * acct_weights.get(acct, 0)
            diff = target_acct_val - val
            if abs(diff) < 1e-9:
                continue
            tx_list.append({
                "Account": acct,
                "Identifier": sub["_ident"].iloc[0],
                "Sleeve": s,
                "Shares_Delta": diff / price_map.get(sub["_ident"].iloc[0], 1),
                "Delta_Dollars": diff,
                "Price": price_map.get(sub["_ident"].iloc[0], 1),
                "AverageCost": cost_map.get(sub["_ident"].iloc[0], 1)
            })

    tx = pd.DataFrame(tx_list)
    if tx.empty:
        after = df.copy()
        residuals = {}
        return tx, after, residuals

    after = df.copy()
    after["_key"] = after["Account"].astype(str) + "||" + after["_ident"].astype(str)
    sd = tx.assign(_key=tx["Account"].astype(str) + "||" + tx["Identifier"].astype(str)).groupby("_key")["Shares_Delta"].sum().to_dict()
    after["Quantity"] = after["Quantity"] + after["_key"].map(sd).fillna(0.0)
    after = after[after["Quantity"].abs() > 1e-9].copy()
    after["Value"] = after["Quantity"] * after["Price"]
    after["Cost"] = after["Quantity"] * after["AverageCost"]
    after.drop(columns=["_key"], errors="ignore", inplace=True)

    flow = tx.groupby("Account")["Delta_Dollars"].sum()
    residuals = {acct: float(v) for acct, v in flow.items() if abs(v) > cash_tolerance}
    return tx, after, residuals

