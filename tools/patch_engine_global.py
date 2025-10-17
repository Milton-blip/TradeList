# tools/patch_engine_global.py
from pathlib import Path
import re, subprocess

SRC = Path("portfolio_trades/engine.py")
FUNC_NAME = "build_trades_and_afterholdings"

new_func = r'''
def build_trades_and_afterholdings(h: pd.DataFrame, W: pd.Series, cash_tolerance: float = 100.0):
    df = h.copy()

    need = ["Account","Symbol","Name","Quantity","Price","AverageCost"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise SystemExit(f"holdings missing columns: {miss}")

    for c in ["Quantity","Price","AverageCost"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    if "Value" not in df.columns:
        df["Value"] = df["Quantity"] * df["Price"]
    if "Cost" not in df.columns:
        df["Cost"] = df["Quantity"] * df["AverageCost"]

    if "TaxStatus" not in df.columns or df["TaxStatus"].isna().all():
        df["TaxStatus"] = df["Account"].map(assign_tax_status)

    if "Sleeve" not in df.columns:
        df["Sleeve"] = [map_sleeve(s, n) for s, n in zip(df["Symbol"], df["Name"])]

    df["_ident"] = df["Symbol"].astype(str)

    def tax_rate_for_status(status: str) -> float:
        s = str(status).lower()
        if "hsa" in s or "roth" in s:
            return 0.0
        if "trust" in s:
            return EST_TAX_RATE.get("Trust", 0.20)
        if "taxable" in s:
            return EST_TAX_RATE.get("Taxable", 0.15)
        return 0.0

    illq_mask = [is_automattic(s, n) for s, n in zip(df["Symbol"], df["Name"])]
    illq_val_total = float(df.loc[illq_mask, "Value"].sum())
    total_value = float(df["Value"].sum())
    investable_total = max(0.0, total_value - illq_val_total)

    W_inv = W.copy()
    if "Illiquid_Automattic" in W_inv.index:
        W_inv = W_inv.drop(index="Illiquid_Automattic")
    W_inv = W_inv / (W_inv.sum() if W_inv.sum() > 0 else 1.0)

    tgt_by_sleeve = (W_inv * investable_total).rename("Target_$")

    cur_by_sleeve = (
        df.loc[~pd.Series(illq_mask, index=df.index)]
          .groupby("Sleeve")["Value"].sum()
          .reindex(W_inv.index).fillna(0.0)
    )

    net_delta_by_sleeve = (tgt_by_sleeve - cur_by_sleeve).to_dict()

    acct_taxrate = df.groupby("Account")["TaxStatus"].first().map(tax_rate_for_status).to_dict()
    acct_total = df.groupby("Account")["Value"].sum().to_dict()

    by_acct_sleeve_ident = (
        df.groupby(["Account","Sleeve","_ident"])["Value"].sum().reset_index()
    )
    acct_sleeve_ident = {}
    for (acct, sleeve), g in by_acct_sleeve_ident.groupby(["Account","Sleeve"]):
        ident = g.sort_values("Value", ascending=False)["_ident"].iloc[0]
        acct_sleeve_ident[(acct, sleeve)] = ident

    by_sleeve_ident = df.groupby(["Sleeve","_ident"])["Value"].sum().reset_index()
    canon_global = {}
    sleeves_all = sorted(set(list(W_inv.index) + df["Sleeve"].unique().tolist()))
    for s in sleeves_all:
        g = by_sleeve_ident[by_sleeve_ident["Sleeve"] == s]
        if not g.empty:
            canon_global[s] = g.sort_values("Value", ascending=False)["_ident"].iloc[0]
        else:
            canon_global[s] = FALLBACK_PROXY.get(s)

    price_map = df.groupby("_ident")["Price"].median().to_dict()

    rows = []

    cur_val_acct_sleeve = (
        df.groupby(["Account","Sleeve"])["Value"].sum().to_dict()
    )

    accounts = sorted(df["Account"].unique().tolist())

    def acct_order_for_buy(sleeve: str):
        def key(a):
            tax = float(acct_taxrate.get(a, 0.0))
            existing = float(cur_val_acct_sleeve.get((a, sleeve), 0.0))
            size = float(acct_total.get(a, 0.0))
            return (tax, -1.0*(existing > 0.0), -existing, -size)
        return sorted(accounts, key=key)

    def acct_order_for_sell(sleeve: str):
        sell_candidates = []
        for a in accounts:
            held_val = float(cur_val_acct_sleeve.get((a, sleeve), 0.0))
            if held_val <= 0: 
                continue
            ident = acct_sleeve_ident.get((a, sleeve), canon_global.get(sleeve))
            if ident is None: 
                continue
            px = float(price_map.get(ident, 0.0))
            gA = df[(df["Account"]==a) & (df["_ident"]==ident)]
            if gA.empty or px <= 0: 
                continue
            tot_q = float(gA["Quantity"].sum())
            if tot_q <= 0: 
                continue
            avgc = float((gA["AverageCost"] * gA["Quantity"]).sum() / tot_q) if tot_q else 0.0
            per_gain = px - avgc
            sell_candidates.append((a, float(acct_taxrate.get(a,0.0)), per_gain, held_val, ident, px, avgc, tot_q))
        sell_candidates.sort(key=lambda t: (t[1], t[2], -t[3]))
        return sell_candidates

    for sleeve, d in net_delta_by_sleeve.items():
        if sleeve == "Illiquid_Automattic": 
            continue
        if abs(d) < 1.0: 
            continue

        if d > 0:
            for acct in acct_order_for_buy(sleeve):
                ident = acct_sleeve_ident.get((acct, sleeve), canon_global.get(sleeve))
                if ident is None: 
                    continue
                px = float(price_map.get(ident, 0.0))
                if not np.isfinite(px) or px <= 0: 
                    continue
                sh = _round_shares(d, px, ident)
                if sh == 0: 
                    continue
                rows.append({
                    "Account": acct,
                    "TaxStatus": df.loc[df["Account"]==acct, "TaxStatus"].iloc[0],
                    "Identifier": ident,
                    "Sleeve": sleeve,
                    "Action": "BUY",
                    "Shares_Delta": sh,
                    "Price": px,
                    "AverageCost": float(df[(df["Account"]==acct) & (df["_ident"]==ident)]
                                          .pipe(lambda g: (g["AverageCost"]*g["Quantity"]).sum()/g["Quantity"].sum()
                                                if g["Quantity"].sum()>0 else 0.0)),
                    "Delta_Dollars": sh * px,
                    "CapGain_Dollars": 0.0
                })
                cur_val_acct_sleeve[(acct, sleeve)] = cur_val_acct_sleeve.get((acct, sleeve), 0.0) + sh*px
                d = 0.0
                break

        else:
            need_sell = -d
            sellers = acct_order_for_sell(sleeve)
            for (acct, tax, per_gain, held_val, ident, px, avgc, tot_q) in sellers:
                if need_sell <= 0: 
                    break
                max_dollars = min(need_sell, held_val)
                sh = _round_shares(-max_dollars, px, ident)
                sh = -abs(sh)
                sh = -min(abs(sh), abs(tot_q))
                if sh == 0: 
                    continue
                cap = (px - avgc) * abs(sh)
                rows.append({
                    "Account": acct,
                    "TaxStatus": df.loc[df["Account"]==acct, "TaxStatus"].iloc[0],
                    "Identifier": ident,
                    "Sleeve": sleeve,
                    "Action": "SELL",
                    "Shares_Delta": sh,
                    "Price": px,
                    "AverageCost": avgc,
                    "Delta_Dollars": sh * px,
                    "CapGain_Dollars": cap
                })
                cur_val_acct_sleeve[(acct, sleeve)] = cur_val_acct_sleeve.get((acct, sleeve), 0.0) + sh*px
                need_sell -= abs(sh*px)

    tx = pd.DataFrame(rows)

    if not tx.empty:
        flow = tx.groupby("Account")["Delta_Dollars"].sum()
        price_map = df.groupby("_ident")["Price"].median().to_dict()

        def pick_cash_ident(acct_df: pd.DataFrame) -> str:
            ids = acct_df["_ident"].unique().tolist()
            for i in ids:
                if is_cashlike(i): 
                    return i
            return FALLBACK_PROXY.get("Cash","BIL")

        add_bal = []
        for acct, amt in flow.items():
            if abs(amt) <= cash_tolerance:
                continue
            gA = df[df["Account"]==acct]
            cident = pick_cash_ident(gA)
            px = float(price_map.get(cident, 1.0))
            sh = _round_shares(-amt, px, cident)
            if sh == 0: 
                continue
            add_bal.append({
                "Account": acct,
                "TaxStatus": gA["TaxStatus"].iloc[0] if not gA.empty else assign_tax_status(acct),
                "Identifier": cident,
                "Sleeve": "Cash",
                "Action": "BUY" if sh>0 else "SELL",
                "Shares_Delta": sh,
                "Price": px,
                "AverageCost": 0.0,
                "Delta_Dollars": sh*px,
                "CapGain_Dollars": 0.0
            })
        if add_bal:
            tx = pd.concat([tx, pd.DataFrame(add_bal)], ignore_index=True)

    if tx.empty:
        after = df.copy()
        return tx, after, {}

    tx = (tx
          .groupby(["Account","Identifier","TaxStatus","Sleeve"], as_index=False)
          .agg(Shares_Delta=("Shares_Delta","sum"),
               Price=("Price","last"),
               AverageCost=("AverageCost","last"),
               Delta_Dollars=("Delta_Dollars","sum"),
               CapGain_Dollars=("CapGain_Dollars","sum"))
          )
    tx["Action"] = np.where(tx["Shares_Delta"]>=0, "BUY", "SELL")

    after = df.copy()
    after["_key"] = after["Account"].astype(str) + "||" + after["_ident"].astype(str)
    sd = (tx.assign(_key = tx["Account"].astype(str)+"||"+tx["Identifier"].astype(str))
            .groupby("_key")["Shares_Delta"].sum().to_dict())

    have = set(after["_key"])
    need = set(sd.keys()) - have
    if need:
        inv_proxy = {v:k for k,v in FALLBACK_PROXY.items()}
        add = []
        for k in need:
            acct, ident = k.split("||",1)
            sleeve_guess = inv_proxy.get(ident, "US_Core")
            px = float(df.loc[df["_ident"]==ident, "Price"].median())
            if not np.isfinite(px) or px<=0: px = 1.0
            add.append({
                "Account": acct, "TaxStatus": assign_tax_status(acct),
                "Name": ident, "Symbol": ident, "Sleeve": sleeve_guess,
                "_ident": ident, "Quantity": 0.0, "Price": px, "AverageCost": 0.0,
                "Value": 0.0, "Cost": 0.0, "_key": k
            })
        after = pd.concat([after, pd.DataFrame(add)], ignore_index=True)

    after["Quantity"] = after["Quantity"] + after["_key"].map(sd).fillna(0.0)
    after = after[after["Quantity"].abs()>1e-9].copy()
    after["Value"] = after["Quantity"] * after["Price"]
    after["Cost"]  = after["Quantity"] * after["AverageCost"]
    after.drop(columns=["_key"], errors="ignore", inplace=True)

    flow = tx.groupby("Account")["Delta_Dollars"].sum()
    residuals = {a: float(v) for a,v in flow.items() if abs(v) > cash_tolerance}

    return tx, after, residuals
'''

def main():
    txt = SRC.read_text()
    pattern = re.compile(rf"def {FUNC_NAME}\(.*?\):.*?(?=\ndef |\Z)", re.S)
    if not pattern.search(txt):
        raise SystemExit(f"Could not locate {FUNC_NAME}() in {SRC}")
    new_txt = pattern.sub(new_func.strip() + "\n\n", txt)
    SRC.write_text(new_txt)
    subprocess.run(["python", "-m", "py_compile", str(SRC)], check=True)
    print(f"patched {SRC}: replaced {FUNC_NAME}() and passed syntax check")

if __name__ == "__main__":
    main()