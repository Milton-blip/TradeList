import pandas as pd

def apply_trades_to_holdings(holdings: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    out = holdings.copy()
    out["_key"] = out["Account"].astype(str) + "||" + out["_ident"].astype(str)
    if trades.empty: return out

    share_deltas = (
        trades.assign(_key=trades["Account"] + "||" + trades["Identifier"])
              .groupby("_key", as_index=True)["Shares_Delta"].sum().to_dict()
    )
    have = set(out["_key"].astype(str))
    need = set(share_deltas.keys()) - have
    if need:
        add = []
        for k in need:
            acct, ident = k.split("||", 1)
            add.append({
                "Account": acct, "TaxStatus": "", "Name": ident, "Symbol": ident,
                "Sleeve": "US_Core", "_ident": ident, "Quantity": 0.0,
                "Price": 0.0, "AverageCost": 0.0, "Value": 0.0
            })
        out = pd.concat([out, pd.DataFrame(add)], ignore_index=True)

    def apply_delta(group: pd.DataFrame) -> pd.DataFrame:
        acct, ident = group.name
        k = f"{acct}||{ident}"
        sh = float(share_deltas.get(k, 0.0))
        if sh != 0.0:
            g = group.copy()
            g.loc[:, "Quantity"] = g["Quantity"] + sh
            g.loc[:, "Value"] = g["Quantity"] * g["Price"]
            return g
        return group

    out = out.groupby(["Account","_ident"], group_keys=False).apply(apply_delta, include_groups=False)
    out = out[out["Quantity"].abs() > 1e-6].copy()
    out["Value"] = out["Quantity"] * out["Price"]
    return out