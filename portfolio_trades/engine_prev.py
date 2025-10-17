import re
import numpy as np
import pandas as pd
from .conventions import (
    MAP_TO_SLEEVE, FALLBACK_PROXY, ACCOUNT_TAX_STATUS_RULES, DEFAULT_TAX_STATUS,
    EST_TAX_RATE, is_cashlike, is_automattic
)

def assign_tax_status(acct: str) -> str:
    if not isinstance(acct, str): return DEFAULT_TAX_STATUS
    low = acct.lower()
    for pat, status in ACCOUNT_TAX_STATUS_RULES:
        if re.search(pat, low): return status
    return DEFAULT_TAX_STATUS

def map_sleeve(sym: str, name: str) -> str:
    s = str(sym).upper().strip(); n = str(name).upper().strip()
    if is_automattic(s, n): return "Illiquid_Automattic"
    if s in MAP_TO_SLEEVE:  return MAP_TO_SLEEVE[s]
    if "INFLATION" in n:    return "TIPS"
    if any(k in n for k in ["UST","TREAS","STRIP"]): return "Treasuries"
    return "US_Core"

def build_trades_and_afterholdings(h: pd.DataFrame, W: pd.Series, cash_tolerance: float = 100.0):
    """Compute trades (per account) to move toward portfolio-wide targets while:
       - prohibiting trades in Automattic
       - not transferring cash between accounts
       - minimizing sells (cap-gain aware: only sells incur realized gains)
    Inputs:
       h: DataFrame with columns
          Account, TaxStatus (optional), Name, Symbol, Quantity, Price (per share),
          AverageCost (per share), Value (=Quantity*Price), CostTotal (=Quantity*AverageCost)
       W: Series of target weights by Sleeve (portfolio-wide)
    Returns:
       tx: trades table
       after: holdings after trades (same schema as input plus Sleeve)
       residuals: dict of account -> residual cash flow (|flow| > tolerance reported)
    """

    df = h.copy()
    if "TaxStatus" not in df.columns or df["TaxStatus"].eq("").all():
        df["TaxStatus"] = df["Account"].map(assign_tax_status)

    # Sleeves
    df["Sleeve"] = [map_sleeve(s, n) for s, n in zip(df["Symbol"], df["Name"])]
    df["_ident"] = df["Symbol"].astype(str)

    # Canonical ident per (Account,Sleeve): largest $ in that account
    acct_sleeve_ident = (
        df.groupby(["Account","Sleeve","_ident"])["Value"].sum()
          .reset_index().sort_values(["Account","Sleeve","Value"], ascending=[True,True,False])
          .drop_duplicates(["Account","Sleeve"])
          .set_index(["Account","Sleeve"])["_ident"].to_dict()
    )

    # Global price map for any ident
    price_map = df.groupby("_ident")["Price"].median().to_dict()

    # Portfolio-total by account
    acct_tot = df.groupby("Account")["Value"].sum()

    trades = []

    def round_shares(delta_dollars, px, ident):
        if px <= 0: return 0.0
        return round(delta_dollars/px, 2) if is_cashlike(ident) else round(delta_dollars/px, 1)

    # For each account, compute target sleeve dollars from *portfolio-wide* W,
    # but remove Illiquid_Automattic from the investable pool inside that account.
    for acct, g in df.groupby("Account"):
        total_val = float(acct_tot.get(acct, 0.0))
        if total_val <= 0: continue

        # Illiquid value (Automattic) in this account
        illq = g.loc[[is_automattic(s, n) for s,n in zip(g["Symbol"], g["Name"])], "Value"].sum()
        investable = max(0.0, total_val - float(illq))

        # Investable weights exclude Illiquid_Automattic
        W_inv = W.copy()
        if "Illiquid_Automattic" in W_inv.index:
            W_inv = W_inv.drop(index="Illiquid_Automattic")
        W_inv = W_inv / (W_inv.sum() if W_inv.sum() > 0 else 1.0)

        tgt_val = (W_inv * investable)

        cur_val = g.groupby("Sleeve")["Value"].sum()
        all_sleeves = sorted(set(cur_val.index).union(tgt_val.index))
        cur = cur_val.reindex(all_sleeves).fillna(0.0)
        tgt = tgt_val.reindex(all_sleeves).fillna(0.0)

        # Do not trade illiquid sleeve
        if "Illiquid_Automattic" in cur.index:
            tgt.loc["Illiquid_Automattic"] = cur.loc["Illiquid_Automattic"]

        delta_by_sleeve = (tgt - cur)

        for sleeve, d_dollars in delta_by_sleeve.items():
            if sleeve == "Illiquid_Automattic":
                continue

            # Choose ident to trade in this account for that sleeve
            ident = acct_sleeve_ident.get((acct, sleeve))
            if ident is None:
                ident = FALLBACK_PROXY.get(sleeve)
            if ident is None:
                continue

            px = float(price_map.get(ident, 0.0))
            if px <= 0:
                continue

            if abs(d_dollars) < 1.0:  # tiny noise
                continue

            sh = round_shares(d_dollars, px, ident)
            if sh == 0.0:
                continue

            action = "BUY" if d_dollars > 0 else "SELL"

            # Cap SELL to available shares in this account
            if action == "SELL":
                held_sh = g.loc[g["_ident"]==ident, "Quantity"].sum()
                sh = min(abs(sh), abs(held_sh))
                sh = -sh  # selling is negative shares
                if sh == 0:
                    continue

            # Account-level weighted average cost per share for this ident
            rows_ident = g[g["_ident"]==ident]
            if not rows_ident.empty and rows_ident["Quantity"].sum() > 0:
                tot_sh = float(rows_ident["Quantity"].sum())
                avgc = float((rows_ident["AverageCost"] * rows_ident["Quantity"]).sum() / tot_sh)
            else:
                avgc = 0.0

            capgain = 0.0
            if action == "SELL":
                shares_sold = abs(sh)
                capgain = (px - avgc) * shares_sold

            trades.append({
                "Account": acct,
                "TaxStatus": g["TaxStatus"].iloc[0],
                "Identifier": ident,
                "Sleeve": sleeve,
                "Action": action,
                "Shares_Delta": sh,
                "Price": px,             # per-share
                "AverageCost": avgc,     # per-share
                "Delta_Dollars": sh * px,
                "CapGain_Dollars": capgain
            })

    tx = pd.DataFrame(trades)
    if tx.empty:
        # Build a minimal after = input + sleeve
        after = df.copy()
        return tx, after, {}

    # Aggregate per (Account, Identifier)
    tx["_key"] = tx["Account"].astype(str) + "||" + tx["Identifier"].astype(str)
    agg = (tx.groupby(["Account","Identifier","TaxStatus","Sleeve"], as_index=False)
             .agg(Shares_Delta=("Shares_Delta","sum"),
                  Price=("Price","last"),
                  AverageCost=("AverageCost","last"),
                  Delta_Dollars=("Delta_Dollars","sum"),
                  CapGain_Dollars=("CapGain_Dollars","sum"),
                  Action=("Action","last")))
    # Recompute action from net shares
    agg["Action"] = np.where(agg["Shares_Delta"]>=0, "BUY", "SELL")
    tx = agg.copy()

    # Build 'after' by applying share deltas per (Account,Identifier)
    after = df.copy()
    after["_key"] = after["Account"].astype(str) + "||" + after["_ident"].astype(str)

    share_deltas = (tx.assign(_key = tx["Account"].astype(str)+"||"+tx["Identifier"].astype(str))
                      .groupby("_key")["Shares_Delta"].sum().to_dict())

    # Ensure missing (new) positions exist
    need_keys = set(share_deltas.keys()) - set(after["_key"])
    if need_keys:
        add_rows = []
        inv_proxy = {v:k for k,v in FALLBACK_PROXY.items()}
        for k in need_keys:
            acct, ident = k.split("||", 1)
            # Guess sleeve by inverse mappings or default
            sleeve_guess = inv_proxy.get(ident, "US_Core")
            px = float(df.loc[df["_ident"]==ident, "Price"].median())
            if not np.isfinite(px) or px<=0: px = 1.0
            add_rows.append({
                "Account":acct,"TaxStatus":assign_tax_status(acct),
                "Name":ident,"Symbol":ident,"Sleeve":sleeve_guess,"_ident":ident,
                "Quantity":0.0,"Price":px,"AverageCost":0.0,
                "Value":0.0,"CostTotal":0.0,"_key":k
            })
        after = pd.concat([after, pd.DataFrame(add_rows)], ignore_index=True)

    def apply_delta(group: pd.DataFrame) -> pd.DataFrame:
        acct = group["Account"].iloc[0]
        ident = group["_ident"].iloc[0]
        k = f"{acct}||{ident}"
        sh = float(share_deltas.get(k, 0.0))
        if sh != 0.0:
            g = group.copy()
            g.loc[:,"Quantity"] = g["Quantity"] + sh
            g.loc[:,"Value"] = g["Quantity"] * g["Price"]
            g.loc[:,"CostTotal"] = g["Quantity"] * g["AverageCost"]
            return g
        return group

    after = after.groupby(["Account","_ident"], group_keys=False).apply(apply_delta)
    after = after[after["Quantity"].abs() > 1e-9].copy()
    after["Value"] = after["Quantity"] * after["Price"]
    after["CostTotal"] = after["Quantity"] * after["AverageCost"]

    # Residual cash flow by account = sum of Delta_Dollars
    flow = tx.groupby("Account")["Delta_Dollars"].sum()
    residuals = {acct: float(v) for acct, v in flow.items() if abs(v) > cash_tolerance}

    return tx, after, residuals