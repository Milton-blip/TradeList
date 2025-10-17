import pandas as pd
from .config import FALLBACK_PROXY

def account_canon_ident(holdings: pd.DataFrame) -> tuple[dict, dict, dict]:
    # returns (canon_acct, canon_global, price_map)
    by_a_s_i = holdings.groupby(["Account","Sleeve","_ident"], as_index=False)["Value"].sum()
    canon_acct = {(a,s): df.sort_values("Value", ascending=False)["_ident"].iloc[0]
                  for (a,s), df in by_a_s_i.groupby(["Account","Sleeve"])}
    by_s_i = holdings.groupby(["Sleeve","_ident"], as_index=False)["Value"].sum()
    canon_global = {}
    for s, df in by_s_i.groupby("Sleeve"):
        canon_global[s] = df.sort_values("Value", ascending=False)["_ident"].iloc[0]
    for s, proxy in FALLBACK_PROXY.items():
        canon_global.setdefault(s, proxy)
    price_map = holdings.groupby("_ident")["Price"].median().to_dict()
    return canon_acct, canon_global, price_map

def per_account_target_values(holdings: pd.DataFrame, W_avg: pd.Series) -> dict:
    """Return dict[account] -> target_value_per_sleeve (pd.Series) with Automattic held fixed."""
    out = {}
    for acct, dfA in holdings.groupby("Account"):
        cur = dfA.groupby("Sleeve")["Value"].sum()
        illq = dfA.loc[dfA["Sleeve"]=="Illiquid_Automattic","Value"].sum()
        investable_total = max(0.0, dfA["Value"].sum() - illq)
        W_inv = W_avg.drop(index="Illiquid_Automattic", errors="ignore")
        W_inv = W_inv / W_inv.sum() if W_inv.sum() > 0 else W_inv
        tgt = (W_inv * investable_total).reindex(cur.index.union(W_inv.index)).fillna(0.0)
        out[acct] = tgt
    return out