# portfolio_trades/engine.py
from __future__ import annotations

import re
from typing import Dict, Tuple, List

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


def tax_rate_for_status(status: str) -> float:
    s = (status or "").lower()
    if "hsa" in s or "roth" in s:
        return 0.0
    if "taxable" in s:
        return EST_TAX_RATE.get("Taxable", 0.15)
    if "trust" in s:
        return EST_TAX_RATE.get("Trust", 0.20)
    return 0.0


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
def build_trades_and_afterholdings(
    h: pd.DataFrame,
    W: pd.Series,
    cash_tolerance: float = 100.0,
):
    """
    Inputs expected (per io_utils.load_holdings):
      Account, TaxStatus, Name, Symbol, Quantity, Price, AverageCost, Value, Cost, [Sleeve?, Tradable?]

    W: Series of target weights by Sleeve (portfolio-wide, sums to 1.0)
    """

    # Canonicalize input
    df = h.copy()

    if "TaxStatus" not in df.columns or df["TaxStatus"].isna().all():
        df["TaxStatus"] = df["Account"].map(assign_tax_status)
    df["TaxStatus"] = df["TaxStatus"].fillna(df["Account"].map(assign_tax_status))

    # Identifier we trade by = Symbol
    df["_ident"] = df["Symbol"].astype(str)

    # Sleeve (if missing, derive)
    if "Sleeve" not in df.columns:
        df["Sleeve"] = [map_sleeve(s, n) for s, n in zip(df["Symbol"], df["Name"])]
    else:
        df["Sleeve"] = df["Sleeve"].fillna("").replace("", np.nan)
        df.loc[df["Sleeve"].isna(), "Sleeve"] = [
            map_sleeve(s, n) for s, n in zip(df["Symbol"], df["Name"])
        ]

    # Tradable default True (we won't place trades for non-tradable)
    if "Tradable" not in df.columns:
        df["Tradable"] = True
    df["Tradable"] = df["Tradable"].fillna(True).astype(bool)

    # Prices / costs per ident
    price_map = df.groupby("_ident")["Price"].median().to_dict()

    # Account totals & investable (exclude illiquid Automattic from investable)
    acct_total = df.groupby("Account")["Value"].sum()
    illiq_acct = (
        df[[is_automattic(s, n) for s, n in zip(df["Symbol"], df["Name"])]]
        .groupby("Account")["Value"]
        .sum()
        .reindex(acct_total.index)
        .fillna(0.0)
    )
    investable_acct = (acct_total - illiq_acct).clip(lower=0.0)

    # Targets excluding the illiquid sleeve; renormalize
    W_inv = W.copy()
    if "Illiquid_Automattic" in W_inv.index:
        W_inv = W_inv.drop(index="Illiquid_Automattic")
    total_w = float(W_inv.sum())
    if total_w <= 0:
        # degenerate targets; do nothing
        after = df.copy()
        return pd.DataFrame(), after, {}

    W_inv = W_inv / total_w

    # For each account, compute desired dollar per sleeve (portfolio-wide weights times that account's investable)
    # current dollar per sleeve in that account; delta = target - current
    cur_acct_sleeve = df.groupby(["Account", "Sleeve"])["Value"].sum().unstack(fill_value=0.0)
    tgt_acct_sleeve = pd.DataFrame(
        {acct: W_inv * float(investable_acct.get(acct, 0.0)) for acct in investable_acct.index}
    ).T.reindex(columns=W_inv.index, fill_value=0.0)

    # Keep illiquid fixed
    if "Illiquid_Automattic" in cur_acct_sleeve.columns:
        tgt_acct_sleeve["Illiquid_Automattic"] = cur_acct_sleeve["Illiquid_Automattic"]

    # Ensure same columns
    for c in cur_acct_sleeve.columns:
        if c not in tgt_acct_sleeve.columns:
            tgt_acct_sleeve[c] = 0.0
    for c in tgt_acct_sleeve.columns:
        if c not in cur_acct_sleeve.columns:
            cur_acct_sleeve[c] = 0.0
    cur_acct_sleeve = cur_acct_sleeve.reindex(columns=tgt_acct_sleeve.columns, fill_value=0.0)

    delta_acct_sleeve = tgt_acct_sleeve - cur_acct_sleeve

    # Choose a single ident per (Account, Sleeve) to trade: prefer largest $ within that bucket, else FALLBACK_PROXY
    pick_ident: Dict[Tuple[str, str], str] = {}
    by_bucket = (
        df.groupby(["Account", "Sleeve", "_ident"])["Value"].sum().reset_index()
    )
    by_bucket.sort_values(["Account", "Sleeve", "Value"], ascending=[True, True, False], inplace=True)
    seen: set[Tuple[str, str]] = set()
    for _, r in by_bucket.iterrows():
        key = (str(r["Account"]), str(r["Sleeve"]))
        if key in seen:
            continue
        pick_ident[key] = str(r["_ident"])
        seen.add(key)

    # Patch in proxies if needed
    for acct in investable_acct.index:
        for sleeve in W_inv.index:
            k = (acct, sleeve)
            if k not in pick_ident:
                proxy = FALLBACK_PROXY.get(sleeve)
                if proxy:
                    pick_ident[k] = proxy

    # Build trades per account/sleeve delta, capping sells by shares in THAT account
    trades: List[dict] = []
    for acct, row in delta_acct_sleeve.iterrows():
        # skip accounts without money
        if float(investable_acct.get(acct, 0.0)) <= 0 and acct not in cur_acct_sleeve.index:
            continue

        g = df[df["Account"] == acct]

        for sleeve, d_dollars in row.items():
            if abs(d_dollars) < 1.0:  # skip tiny noise
                continue
            if sleeve == "Illiquid_Automattic":
                continue

            ident = pick_ident.get((acct, sleeve)) or FALLBACK_PROXY.get(sleeve)
            if ident is None:
                continue

            px = float(price_map.get(ident, 0.0))
            if not np.isfinite(px) or px <= 0:
                continue

            sh = _round_shares(d_dollars, px, ident)
            if sh == 0.0:
                continue

            # cap sells to shares held in this account for this ident
            if sh < 0:
                held_sh = float(g.loc[g["_ident"] == ident, "Quantity"].sum())
                sh = -min(abs(sh), abs(held_sh))
                if sh == 0.0:
                    continue

            # weighted avg cost per share in THIS account for ident
            rows_ident = g[g["_ident"] == ident]
            if not rows_ident.empty and rows_ident["Quantity"].sum() > 0:
                tot_sh = float(rows_ident["Quantity"].sum())
                avgc = float((rows_ident["AverageCost"] * rows_ident["Quantity"]).sum() / tot_sh)
            else:
                avgc = 0.0

            action = "BUY" if sh > 0 else "SELL"
            capgain = (px - avgc) * abs(sh) if action == "SELL" else 0.0

            trades.append(
                {
                    "Account": acct,
                    "TaxStatus": g["TaxStatus"].iloc[0] if "TaxStatus" in g.columns else assign_tax_status(acct),
                    "Identifier": ident,          # Symbol string
                    "Sleeve": sleeve,
                    "Action": action,
                    "Shares_Delta": float(sh),
                    "Price": float(px),           # per-share
                    "AverageCost": float(avgc),   # per-share
                    "Delta_Dollars": float(sh * px),
                    "CapGain_Dollars": float(capgain),
                }
            )

        # account-level CASH balancing to ~zero net flow within cash_tolerance
        acct_trades = [t for t in trades if t["Account"] == acct]
        if acct_trades:
            net_flow = sum(t["Delta_Dollars"] for t in acct_trades)
            if abs(net_flow) > cash_tolerance:
                # pick cash ident in this account if present; else proxy "BIL"
                cash_ident = None
                ids = g["_ident"].unique().tolist()
                for ident0 in ids:
                    if is_cashlike(ident0):
                        cash_ident = ident0
                        break
                if cash_ident is None:
                    cash_ident = FALLBACK_PROXY.get("Cash", "BIL")
                px_cash = float(price_map.get(cash_ident, 1.0))
                if not np.isfinite(px_cash) or px_cash <= 0:
                    px_cash = 1.0
                sh_cash = _round_shares(-net_flow, px_cash, cash_ident)
                if sh_cash != 0.0:
                    trades.append(
                        {
                            "Account": acct,
                            "TaxStatus": g["TaxStatus"].iloc[0] if "TaxStatus" in g.columns else assign_tax_status(acct),
                            "Identifier": cash_ident,
                            "Sleeve": "Cash",
                            "Action": "BUY" if sh_cash > 0 else "SELL",
                            "Shares_Delta": float(sh_cash),
                            "Price": float(px_cash),
                            "AverageCost": 0.0,
                            "Delta_Dollars": float(sh_cash * px_cash),
                            "CapGain_Dollars": 0.0,
                        }
                    )

    tx = pd.DataFrame(trades, columns=[
        "Account","TaxStatus","Identifier","Sleeve","Action",
        "Shares_Delta","Price","AverageCost","Delta_Dollars","CapGain_Dollars"
    ])

    if tx.empty:
        after = df.copy()
        return tx, after, {}

    # Coalesce trades per (Account, Identifier, Sleeve, TaxStatus)
    tx = (
        tx.groupby(["Account","Identifier","Sleeve","TaxStatus"], as_index=False)
        .agg(
            Shares_Delta=("Shares_Delta","sum"),
            Price=("Price","last"),
            AverageCost=("AverageCost","last"),
            Delta_Dollars=("Delta_Dollars","sum"),
            CapGain_Dollars=("CapGain_Dollars","sum"),
        )
    )
    tx["Action"] = np.where(tx["Shares_Delta"] >= 0, "BUY", "SELL")

    # ---------- Build holdings-after by applying share deltas (key-based; no groupby.apply) ----------
    after = df.copy()
    after["_key"] = after["Account"].astype(str) + "||" + after["_ident"].astype(str)

    share_deltas = (
        tx.assign(_key=tx["Account"].astype(str) + "||" + tx["Identifier"].astype(str))
        .groupby("_key")["Shares_Delta"]
        .sum()
        .to_dict()
    )

    # ensure placeholder rows for traded-but-not-held keys
    have_keys = set(after["_key"])
    need_keys = set(share_deltas.keys()) - have_keys
    if need_keys:
        inv_proxy = {v: k for k, v in FALLBACK_PROXY.items()}
        add_rows = []
        for k in sorted(need_keys):
            acct, ident = k.split("||", 1)
            # guess sleeve
            guess = inv_proxy.get(ident)
            if guess is None:
                # use largest sleeve $ for that ident across portfolio
                rows_ident = df[df["_ident"] == ident]
                if rows_ident.empty:
                    guess = "US_Core"
                else:
                    s = rows_ident.groupby("Sleeve")["Value"].sum()
                    guess = s.sort_values(ascending=False).index[0] if not s.empty else "US_Core"
            px = float(df.loc[df["_ident"] == ident, "Price"].median())
            if not np.isfinite(px) or px <= 0:
                px = 1.0
            add_rows.append(
                {
                    "Account": acct,
                    "TaxStatus": assign_tax_status(acct),
                    "Name": ident,
                    "Symbol": ident,
                    "Sleeve": guess,
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

    # apply deltas by key (vectorized)
    after["Quantity"] = after["Quantity"] + after["_key"].map(share_deltas).fillna(0.0)
    # drop dust
    after = after[after["Quantity"].abs() > 1e-9].copy()
    # recompute values
    after["Value"] = after["Quantity"] * after["Price"]
    after["Cost"] = after["Quantity"] * after["AverageCost"]
    after.drop(columns=["_key"], errors="ignore", inplace=True)

    # Per-account residual cash (should be near 0 thanks to cash balancing)
    flow = tx.groupby("Account")["Delta_Dollars"].sum()
    residuals = {acct: float(v) for acct, v in flow.items() if abs(v) > cash_tolerance}

    return tx, after, residuals