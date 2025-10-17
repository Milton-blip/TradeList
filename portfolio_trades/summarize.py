import numpy as np
import pandas as pd
from .config import EST_TAX_RATE

def per_account_summary(tx: pd.DataFrame) -> pd.DataFrame:
    if tx.empty:
        return pd.DataFrame(columns=["Account","TaxStatus","Total_Buys","Total_Sells","Net_CapGain","Est_Tax"])
    tx = tx.copy()
    tx["Buy_$"]  = np.where(tx["Action"]=="BUY",  tx["Delta_$"], 0.0)
    tx["Sell_$"] = np.where(tx["Action"]=="SELL", -tx["Delta_$"], 0.0)
    acc = (tx.groupby(["Account","TaxStatus"], as_index=False)
             .agg(**{
                 "Total_Buys":  ("Buy_$","sum"),
                 "Total_Sells": ("Sell_$","sum"),
                 "Net_CapGain": ("CapGain_$","sum"),
             }))
    acc["Est_Tax"] = acc.apply(lambda r: EST_TAX_RATE.get(r["TaxStatus"], 0.15) * r["Net_CapGain"], axis=1)
    return acc

def by_tax_status_summary(tx: pd.DataFrame) -> pd.DataFrame:
    if tx.empty:
        return pd.DataFrame(columns=["TaxStatus","Total_Buys","Total_Sells","Net_CapGain","Est_Tax"])
    tx = tx.copy()
    tx["Buy_$"]  = np.where(tx["Action"]=="BUY",  tx["Delta_$"], 0.0)
    tx["Sell_$"] = np.where(tx["Action"]=="SELL", -tx["Delta_$"], 0.0)
    st = (tx.groupby("TaxStatus", as_index=False)
            .agg(**{
                "Total_Buys":  ("Buy_$","sum"),
                "Total_Sells": ("Sell_$","sum"),
                "Net_CapGain": ("CapGain_$","sum"),
            }))
    st["Est_Tax"] = st.apply(lambda r: EST_TAX_RATE.get(r["TaxStatus"], 0.15) * r["Net_CapGain"], axis=1)
    return st