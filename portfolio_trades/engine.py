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
# Basic helpers
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
    # cash-like to 0.01 share precision, others to 0.1 share precision
    return round(dollars / px, 2) if is_cashlike(ident) else round(dollars / px, 1)


def tax_rate_for_status(status: str) -> float:
    s = str(status).lower()
    if "hsa" in s or "roth" in s:
        return 0.0
    if "taxable" in s:
        return EST_TAX_RATE.get("Taxable", 0.15)
    if "trust" in s:
        return EST_TAX_RATE.get("Trust", 0.20)
    return 0.0


# -------------------------
# Core engine
# -------------------------
def build_trades_and_afterholdings(
    h: pd.DataFrame,
    W: pd.Series,
    cash_tolerance: float = 100.0,
):
    """
    Inputs (canonical columns expected):
      Account, TaxStatus, Name, Symbol, Quantity, Price, AverageCost,
      Value (=Quantity*Price), Cost (=Quantity*AverageCost)

    Behavior:
      * Target mix W is portfolio-wide by Sleeve.
      * Allocate target dollars across accounts tax-aware (prefer buys in 0%-tax accounts,
        place sells in high-tax accounts only when necessary; try to keep/merge sleeves
        into fewer accounts to reduce fragmentation).
      * Trades occur only within an account (no transfers).
      * Each account net cash flow is balanced with a CASH trade.
    """
    df = h.copy()

    # Ensure required cols
    required = ["Account", "Name", "Symbol", "Quantity", "Price", "AverageCost"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SystemExit(f"holdings missing required columns: {missing}")

    # Numeric coercion
    for c in ["Quantity", "Price", "AverageCost"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    # Compute Value/Cost if absent
    if "Value" not in df.columns:
        df["Value"] = df["Quantity"] * df["Price"]
    else:
        df["Value"] = pd.to_numeric(df["Value"], errors="coerce")
        bad = df["Value"].isna() | ~np.isfinite(df["Value"])
        df.loc[bad, "Value"] = df.loc[bad, "Quantity"] * df.loc[bad, "Price"]

    if "Cost" not in df.columns:
        df["Cost"] = df["Quantity"] * df["AverageCost"]
    else:
        df["Cost"] = pd.to_numeric(df["Cost"], errors="coerce")
        bad = df["Cost"].isna() | ~np.isfinite(df["Cost"])
        df.loc[bad, "Cost"] = df.loc[bad, "Quantity"] * df.loc[bad, "AverageCost"]

    # TaxStatus defaulting
    if "TaxStatus" not in df.columns:
        df["TaxStatus"] = df["Account"].map(assign_tax_status)
    else:
        df["TaxStatus"] = df["TaxStatus"].fillna("").astype("string")
        mask_blank = df["TaxStatus"].str.strip().eq("") | df["TaxStatus"].isna()
        df.loc[mask_blank, "TaxStatus"] = df.loc[mask_blank, "Account"].map(assign_tax_status)

    # Sleeve mapping (no list-literal assignment; avoid FutureWarning)
    if "Sleeve" not in df.columns:
        sleeve_series = df.apply(lambda r: map_sleeve(r["Symbol"], r["Name"]), axis=1)
        df["Sleeve"] = pd.Series(sleeve_series, dtype="string")
    else:
        # Clean and fill missing by mapping
        df["Sleeve"] = df["Sleeve"].astype("string")
        df["Sleeve"] = df["Sleeve"].mask(df["Sleeve"].str.strip().eq(""), pd.NA)
        mask_sleeve_na = df["Sleeve"].isna()
        if mask_sleeve_na.any():
            fill_series = df.loc[mask_sleeve_na].apply(
                lambda r: map_sleeve(r["Symbol"], r["Name"]), axis=1
            )
            df.loc[mask_sleeve_na, "Sleeve"] = pd.Series(fill_series, index=df.index[mask_sleeve_na])
        df["Sleeve"] = df["Sleeve"].astype("string")

    # Identifiers (trade at the Symbol level)
    df["_ident"] = df["Symbol"].astype(str)

    # Price per ident
    price_map: Dict[str, float] = (
        df.groupby("_ident", as_index=True)["Price"].median().astype(float).to_dict()
    )

    # Weighted avg cost per (Account, _ident) for realized gain calc
    acct_ident_cost: Dict[Tuple[str, str], float] = {}
    g_cost = df.groupby(["Account", "_ident"], as_index=False)[["AverageCost", "Quantity"]].agg(
        tot_cost=("AverageCost", lambda s: float((s * df.loc[s.index, "Quantity"]).sum())),
        tot_sh=("Quantity", "sum"),
    )
    for _, row in g_cost.iterrows():
        acct = str(row["Account"])
        ident = str(row["_ident"])
        tot_sh = float(row["tot_sh"])
        if tot_sh > 0:
            avgc = float(row["tot_cost"]) / tot_sh
        else:
            avgc = 0.0
        acct_ident_cost[(acct, ident)] = avgc

    # Account totals and investable (exclude illiquid Automattic)
    acct_total_val = df.groupby("Account", as_index=True)["Value"].sum().astype(float)

    df["__illq_flag"] = df.apply(lambda r: is_automattic(r["Symbol"], r["Name"]), axis=1)
    acct_illq_val = (
        df.loc[df["__illq_flag"]].groupby("Account")["Value"].sum() if df["__illq_flag"].any() else pd.Series(dtype=float)
    )
    acct_investable = (acct_total_val - acct_illq_val.reindex(acct_total_val.index).fillna(0.0)).clip(lower=0.0)

    # Normalize W over investable sleeves (exclude Illiquid_Automattic if present)
    W = W.copy()
    if "Illiquid_Automattic" in W.index:
        W = W.drop(index="Illiquid_Automattic")
    W = W.clip(lower=0.0)
    if W.sum() <= 0:
        # No targets => no trades
        after = df.copy()
        return pd.DataFrame(columns=[
            "Account","TaxStatus","Identifier","Sleeve","Action","Shares_Delta",
            "Price","AverageCost","Delta_Dollars","CapGain_Dollars"
        ]), after, {}

    W = W / W.sum()

    # Portfolio-wide investable dollars
    total_investable = float(acct_investable.sum())

    # Target dollars by sleeve (portfolio-wide)
    sleeve_target_dollars = (W * total_investable).rename("TargetDollars")

    # Current dollars by sleeve (portfolio-wide), excluding illiquid automattic
    cur_noillq = df.loc[~df["__illq_flag"]]
    sleeve_current_dollars = (
        cur_noillq.groupby("Sleeve", as_index=True)["Value"].sum().astype(float)
        if not cur_noillq.empty else pd.Series(dtype=float)
    )
    # Ensure sleeves present in W exist in current index
    sleeve_current_dollars = sleeve_current_dollars.reindex(W.index).fillna(0.0)

    # Per sleeve net dollar change needed portfolio-wide
    sleeve_delta_total = (sleeve_target_dollars - sleeve_current_dollars)

    # Which ident to use for each (Account, Sleeve): choose the largest $ ident in that acct+sleeve,
    # else fallback proxy.
    tmp = df.groupby(["Account", "Sleeve", "_ident"], as_index=False)["Value"].sum()
    tmp = tmp.sort_values(["Account", "Sleeve", "Value"], ascending=[True, True, False])
    acct_sleeve_ident: Dict[Tuple[str, str], str] = {
        (r["Account"], r["Sleeve"]): r["_ident"] for _, r in tmp.drop_duplicates(["Account", "Sleeve"]).iterrows()
    }

    # Per account share of investable to split each sleeve target across accounts
    investable_share = acct_investable.copy()
    if investable_share.sum() > 0:
        investable_share = investable_share / investable_share.sum()
    else:
        investable_share = investable_share * 0.0  # all zeros

    # Build desired dollars by (Account, Sleeve) from portfolio sleeve targets
    desired_acct_sleeve = {}
    for sleeve, tgt_d in sleeve_target_dollars.items():
        # Keep illiquid as-is; don't target them explicitly
        if sleeve == "Illiquid_Automattic":
            continue
        for acct, share in investable_share.items():
            desired_acct_sleeve[(acct, sleeve)] = float(tgt_d * share)

    # Current dollars by (Account, Sleeve) for tradable sleeves
    cur_acct_sleeve = (
        cur_noillq.groupby(["Account", "Sleeve"], as_index=True)["Value"].sum().astype(float)
        if not cur_noillq.empty else pd.Series(dtype=float)
    )

    # Deltas by (Account, Sleeve)
    pairs = list(desired_acct_sleeve.keys())
    idx = pd.MultiIndex.from_tuples(pairs, names=["Account", "Sleeve"])
    desired_series = pd.Series([desired_acct_sleeve[k] for k in pairs], index=idx)
    cur_series = cur_acct_sleeve.reindex(idx).fillna(0.0)
    acct_sleeve_delta = (desired_series - cur_series)

    # Fragmentation control:
    # For each sleeve, prefer to place positive deltas (buys) in the single account
    # that already has the largest dollar exposure to that sleeve (or the largest investable if nobody holds it).
    # Negative deltas (sells) taken first from tax-exempt accts with gains <= 0, then from highest tax-rate accts.
    # This is a heuristic; it reduces scattering while keeping taxes low.
    trades: List[Dict] = []

    sleeve_accounts = sorted(set([s for _, s in idx]))
    # Build quick maps for tax-rate per account and account tax status
    acct_tax_status = (df.groupby("Account")["TaxStatus"].first()).to_dict()
    acct_tax_rate = {a: tax_rate_for_status(t) for a, t in acct_tax_status.items()}

    # Helper to get ident + price for (acct, sleeve)
    def pick_ident(acct: str, sleeve: str) -> str | None:
        ident = acct_sleeve_ident.get((acct, sleeve))
        if ident:
            return ident
        return FALLBACK_PROXY.get(sleeve, None)

    for sleeve in sleeve_accounts:
        if sleeve == "Illiquid_Automattic":
            continue

        # Accounts involved for this sleeve
        sub = acct_sleeve_delta.xs(sleeve, level="Sleeve", drop_level=False)
        if sub.empty:
            continue

        # Build "host" account candidate for buys (largest current exposure, else largest investable)
        holders = cur_acct_sleeve.xs(sleeve, level="Sleeve", drop_level=False)
        if not holders.empty:
            holder_by_acct = holders.droplevel("Sleeve")
        else:
            holder_by_acct = pd.Series(dtype=float)

        if not holder_by_acct.empty and holder_by_acct.max() > 0:
            host_acct = str(holder_by_acct.idxmax())
        else:
            # Pick account with largest investable dollars
            host_acct = str(acct_investable.idxmax()) if len(acct_investable) else None

        # Split deltas: buys (positive) vs sells (negative)
        pos = sub[sub > 0.0].sort_values(ascending=False)
        neg = sub[sub < 0.0].sort_values(ascending=True)

        # Plan buys: concentrate into host_acct (single account) where possible
        if not pos.empty and host_acct is not None:
            total_buy_dollars = float(pos.sum())
            ident = pick_ident(host_acct, sleeve)
            px = float(price_map.get(ident, 0.0)) if ident else 0.0
            if ident and px > 0:
                # One consolidated BUY in host_acct
                sh = _round_shares(total_buy_dollars, px, ident)
                if sh > 0:
                    avgc = float(acct_ident_cost.get((host_acct, ident), 0.0))
                    trades.append(
                        {
                            "Account": host_acct,
                            "TaxStatus": acct_tax_status.get(host_acct, assign_tax_status(host_acct)),
                            "Identifier": ident,
                            "Sleeve": sleeve,
                            "Action": "BUY",
                            "Shares_Delta": sh,
                            "Price": px,
                            "AverageCost": avgc,
                            "Delta_Dollars": sh * px,
                            "CapGain_Dollars": 0.0,
                        }
                    )
            # Clear buys from sub so we don't handle them again
            for acct in pos.index.get_level_values("Account"):
                sub.loc[(acct, sleeve)] = min(0.0, float(sub.loc[(acct, sleeve)]))  # zero-out positive side

        # Plan sells: choose accounts based on lower realized tax impact first
        # For each acct with negative delta, we will SELL from its ident up to held shares.
        # Sort accounts by (tax_rate ascending, gain_per_dollar ascending)
        sell_accts = []
        for acct, d in neg.items():
            acct_name = acct[0] if isinstance(acct, tuple) else acct  # when coming from the index
            d_dollars = float(d)
            ident = pick_ident(acct_name, sleeve)
            if not ident:
                continue
            px = float(price_map.get(ident, 0.0))
            if px <= 0:
                continue

            held_sh = float(df.loc[(df["Account"] == acct_name) & (df["_ident"] == ident), "Quantity"].sum())
            held_val = held_sh * px
            if held_sh <= 0 and d_dollars < 0:
                # nothing to sell here
                continue

            avgc = float(acct_ident_cost.get((acct_name, ident), 0.0))
            gain_per_dollar = 0.0
            if px > 0:
                gain_per_dollar = max(0.0, (px - avgc) / px)  # only realized when selling at gain
            sell_accts.append((acct_name, d_dollars, ident, px, held_sh, avgc, gain_per_dollar, acct_tax_rate.get(acct_name, 0.0)))

        # Sort: tax rate ascending, then gain_per_dollar ascending (sell in low-tax / low-gain places first)
        sell_accts.sort(key=lambda t: (t[7], t[6]))

        for acct_name, d_dollars, ident, px, held_sh, avgc, gpd, t_rate in sell_accts:
            need_to_sell = abs(d_dollars)
            if need_to_sell <= 0:
                continue
            # Convert dollars -> shares to sell (cap by held shares)
            sh = _round_shares(need_to_sell, px, ident)
            sh_cap = min(sh, max(0.0, held_sh))
            if sh_cap <= 0:
                continue
            sh_to_sell = -sh_cap  # negative shares
            capgain = (px - avgc) * abs(sh_to_sell) if px > avgc else 0.0

            trades.append(
                {
                    "Account": acct_name,
                    "TaxStatus": acct_tax_status.get(acct_name, assign_tax_status(acct_name)),
                    "Identifier": ident,
                    "Sleeve": sleeve,
                    "Action": "SELL",
                    "Shares_Delta": sh_to_sell,
                    "Price": px,
                    "AverageCost": avgc,
                    "Delta_Dollars": sh_to_sell * px,
                    "CapGain_Dollars": capgain,
                }
            )

    # Build trades DataFrame
    if trades:
        tx = pd.DataFrame(trades)
    else:
        # No trades
        after = df.copy()
        return pd.DataFrame(columns=[
            "Account","TaxStatus","Identifier","Sleeve","Action","Shares_Delta",
            "Price","AverageCost","Delta_Dollars","CapGain_Dollars"
        ]), after, {}

    # Consolidate duplicate rows and set Action based on net shares
    tx = (
        tx.groupby(["Account", "TaxStatus", "Identifier", "Sleeve"], as_index=False)
        .agg(
            Shares_Delta=("Shares_Delta", "sum"),
            Price=("Price", "last"),
            AverageCost=("AverageCost", "last"),
            Delta_Dollars=("Delta_Dollars", "sum"),
            CapGain_Dollars=("CapGain_Dollars", "sum"),
        )
    )
    tx["Action"] = np.where(tx["Shares_Delta"] >= 0, "BUY", "SELL")

    # ---------------- Per-account CASH balancing ----------------
    acct_flow = tx.groupby("Account", as_index=True)["Delta_Dollars"].sum()
    if not acct_flow.empty:
        # Pick a cash ident per account
        def pick_cash_ident_for_account(acct: str) -> str:
            # Prefer actual cash-like ident present in that account; fallback to BIL
            ids = df.loc[df["Account"] == acct, "_ident"].unique().tolist()
            for ident in ids:
                if is_cashlike(ident):
                    return ident
            return FALLBACK_PROXY.get("Cash", "BIL")

        add_cash_rows = []
        for acct, flow in acct_flow.items():
            if abs(flow) <= cash_tolerance:
                continue
            cash_ident = pick_cash_ident_for_account(acct)
            px = float(price_map.get(cash_ident, 1.0))
            if not np.isfinite(px) or px <= 0:
                px = 1.0
            # Offset the existing flow
            sh = _round_shares(-flow, px, cash_ident)
            if sh == 0.0:
                continue
            add_cash_rows.append(
                {
                    "Account": acct,
                    "TaxStatus": acct_tax_status.get(acct, assign_tax_status(acct)),
                    "Identifier": cash_ident,
                    "Sleeve": "Cash",
                    "Action": "BUY" if sh > 0 else "SELL",
                    "Shares_Delta": sh,
                    "Price": px,
                    "AverageCost": 0.0,
                    "Delta_Dollars": sh * px,
                    "CapGain_Dollars": 0.0,
                }
            )

        if add_cash_rows:
            tx = pd.concat([tx, pd.DataFrame(add_cash_rows)], ignore_index=True)
            # Re-aggregate after adding cash
            tx = (
                tx.groupby(["Account", "TaxStatus", "Identifier", "Sleeve"], as_index=False)
                .agg(
                    Shares_Delta=("Shares_Delta", "sum"),
                    Price=("Price", "last"),
                    AverageCost=("AverageCost", "last"),
                    Delta_Dollars=("Delta_Dollars", "sum"),
                    CapGain_Dollars=("CapGain_Dollars", "sum"),
                )
            )
            tx["Action"] = np.where(tx["Shares_Delta"] >= 0, "BUY", "SELL")

    # ---------- Build holdings-after by applying share deltas ----------
    after = df.copy()
    after["_key"] = after["Account"].astype(str) + "||" + after["_ident"].astype(str)
    delta_by_key = (
        tx.assign(_key=tx["Account"].astype(str) + "||" + tx["Identifier"].astype(str))
        .groupby("_key", as_index=True)["Shares_Delta"]
        .sum()
        .to_dict()
    )

    # Ensure any traded (Account, Identifier) that wasn't originally held gets a row
    need_keys = set(delta_by_key.keys()) - set(after["_key"])
    if need_keys:
        inv_proxy = {v: k for k, v in FALLBACK_PROXY.items()}
        add_rows = []
        for k in need_keys:
            acct, ident = k.split("||", 1)
            # Guess sleeve from proxy map if needed
            sleeve_guess = None
            # If ident already present somewhere, reuse its sleeve
            present = df.loc[df["_ident"] == ident, "Sleeve"]
            if not present.empty:
                sleeve_guess = str(present.iloc[0])
            else:
                sleeve_guess = inv_proxy.get(ident, "US_Core")

            px = float(price_map.get(ident, 1.0))
            if not np.isfinite(px) or px <= 0:
                px = 1.0
            add_rows.append(
                {
                    "Account": acct,
                    "TaxStatus": acct_tax_status.get(acct, assign_tax_status(acct)),
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

    # Apply deltas by key (vectorized)
    after["Quantity"] = after["Quantity"] + after["_key"].map(delta_by_key).fillna(0.0)
    after = after[after["Quantity"].abs() > 1e-9].copy()
    after["Value"] = after["Quantity"] * after["Price"]
    after["Cost"] = after["Quantity"] * after["AverageCost"]
    after.drop(columns=["_key", "__illq_flag"], errors="ignore", inplace=True)

    # Residuals (should be ~0 after cash balancing)
    flow = (
        tx.groupby("Account", as_index=True)["Delta_Dollars"].sum()
        if not tx.empty
        else pd.Series(dtype=float)
    )
    residuals = {acct: float(v) for acct, v in flow.items() if abs(v) > cash_tolerance}

    return tx, after, residuals