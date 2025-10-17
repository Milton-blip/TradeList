import numpy as np
import pandas as pd
from .mapping import is_cashlike
from .config import FALLBACK_PROXY

def _round_shares(delta_dollars, price, ident, for_sell=False, cap_shares=0.0):
    if price <= 0: return 0.0
    raw = delta_dollars / price
    if for_sell: raw = max(-cap_shares, raw)
    return round(raw, 2) if is_cashlike(ident) else round(raw, 1)

def build_trades(holdings: pd.DataFrame,
                 W_targets: dict,
                 canon_acct: dict, canon_global: dict, price_map: dict) -> pd.DataFrame:
    qty = holdings.groupby(["Account","_ident"])["Quantity"].sum().to_dict()
    avgc = {}
    def _wavg(g):
        q, c = g["Quantity"].astype(float), g["AverageCost"].astype(float)
        t = q.sum()
        return 0.0 if t==0 else float((q*c).sum()/t)
    for (a,i), g in holdings.groupby(["Account","_ident"]):
        avgc[(a,i)] = _wavg(g)

    rows = []
    for acct, tgt in W_targets.items():
        dfA = holdings[holdings["Account"]==acct]
        cur = dfA.groupby("Sleeve")["Value"].sum().reindex(tgt.index, fill_value=0.0)
        delta = tgt - cur
        for sleeve, d in delta.items():
            if sleeve == "Illiquid_Automattic": continue
            owned_ident = canon_acct.get((acct, sleeve))
            ident = owned_ident if owned_ident else canon_global.get(sleeve)
            if ident is None: continue

            price = float(price_map.get(ident, 0.0))
            if price <= 0 and sleeve in FALLBACK_PROXY:
                proxy = FALLBACK_PROXY[sleeve]
                price = float(price_map.get(proxy, 0.0))
                if price > 0: ident = proxy
            if price <= 0 or abs(d) < 5.0:
                continue

            if d < 0:
                if owned_ident is None: continue
                have = float(qty.get((acct, owned_ident), 0.0))
                if have <= 0: continue
                sh = _round_shares(d, price, owned_ident, for_sell=True, cap_shares=have)
                if sh == 0.0: continue
                rows.append({
                    "Account": acct, "Identifier": owned_ident, "Sleeve": sleeve,
                    "Action": "SELL", "Shares_Delta": sh, "Price": price,
                    "AverageCost": float(avgc.get((acct, owned_ident), 0.0)),
                    "Delta_$": round(sh*price, 2),
                    "CapGain_$": round((price - float(avgc.get((acct, owned_ident),0.0))) * abs(sh), 2),
                })
                qty[(acct, owned_ident)] = have + sh  # sh negative
            else:
                sh = _round_shares(d, price, ident)
                if sh == 0.0: continue
                rows.append({
                    "Account": acct, "Identifier": ident, "Sleeve": sleeve,
                    "Action": "BUY", "Shares_Delta": sh, "Price": price,
                    "AverageCost": float(avgc.get((acct, ident), 0.0)),
                    "Delta_$": round(sh*price, 2), "CapGain_$": 0.0,
                })

        # cash nudge (optional): compute net_flow and offset to Cash if possible
        # (left out here to keep module focused; keep in cli or here if you prefer)
    return pd.DataFrame(rows)