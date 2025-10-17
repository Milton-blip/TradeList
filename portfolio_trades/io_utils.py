from pathlib import Path
import datetime
import pandas as pd
import numpy as np

SCENARIOS = ["Base","Disinflation","Reflation","HardLanding","Stagflation","Geopolitical"]

def today_str() -> str:
    return datetime.date.today().isoformat()

def ensure_outdir(name: str = "outputs") -> Path:
    p = Path(name)
    p.mkdir(parents=True, exist_ok=True)
    return p

def _first_existing(paths) -> Path | None:
    for p in paths:
        p = Path(p).expanduser()
        if p.exists():
            return p.resolve()
    return None

def load_holdings(path: str) -> pd.DataFrame:
    """
    Locate and load holdings CSV, normalizing to the canonical schema:

      Symbol, Name, Account, TaxStatus, Quantity,
      Price (per share), AverageCost (per share),
      Value (= Quantity*Price), TotalCost (= Quantity*AverageCost),
      Sleeve, Tradable (bool)

    We do NOT alter your numbers; we only coerce types and fill derived columns.
    """
    candidates = [
        Path(path),
        Path("data/holdings.csv"),
        Path("inputs/holdings.csv"),
        Path("holdings.csv"),
    ]
    found = _first_existing(candidates)
    if not found:
        tried = "\n  ".join(str(Path(p).resolve()) for p in candidates)
        raise SystemExit(f"holdings.csv not found. Tried:\n  {tried}")

    df = pd.read_csv(found)

    # Flexible header normalization (case-insensitive)
    lower = {c.lower(): c for c in df.columns}
    rename_map = {
        # per-share price
        "pricepershare": "Price",
        "currentprice": "Price",
        "lastprice": "Price",
        "price": "Price",
        # per-share average cost
        "averagecost": "AverageCost",
        "avgcost": "AverageCost",
        "costpershare": "AverageCost",
        # market value (we recompute anyway)
        "marketvalue": "Value",
        "currentvalue": "Value",
        "currvalue": "Value",
        # total (aggregate) cost
        "totalcost": "TotalCost",
    }
    for k, v in rename_map.items():
        if k in lower:
            df.rename(columns={lower[k]: v}, inplace=True)

    # Required logical columns
    required = ["Symbol","Name","Account","TaxStatus","Quantity","Price","AverageCost"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SystemExit(f"holdings.csv missing required columns: {missing}")

    # Types/coercion — per-share columns remain per-share
    for c in ["Quantity","Price","AverageCost"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    # Derived columns (don’t overwrite if present but recompute if clearly stale)
    if "Value" not in df.columns:
        df["Value"] = df["Quantity"] * df["Price"]
    else:
        # If present but not consistent, fix it silently
        calc_val = df["Quantity"] * df["Price"]
        mask = ~np.isclose(df["Value"].astype(float), calc_val.astype(float), rtol=0, atol=0.01)
        if mask.any():
            df.loc[mask, "Value"] = calc_val[mask]

    if "TotalCost" not in df.columns:
        df["TotalCost"] = df["Quantity"] * df["AverageCost"]
    else:
        calc_cost = df["Quantity"] * df["AverageCost"]
        mask = ~np.isclose(df["TotalCost"].astype(float), calc_cost.astype(float), rtol=0, atol=0.01)
        if mask.any():
            df.loc[mask, "TotalCost"] = calc_cost[mask]

    if "Sleeve" not in df.columns:
        df["Sleeve"] = "US_Core"

    if "Tradable" not in df.columns:
        df["Tradable"] = True
    df["Tradable"] = df["Tradable"].fillna(True).astype(bool)

    # canonical identifier used throughout
    df["_ident"] = df["Symbol"].astype(str)

    return df

def load_targets(vol_pct_tag: int) -> pd.Series:
    """
    Average target sleeve weights from portfolio_targets/.
    Filenames: portfolio_targets/allocation_targetVol_<vol%>_<Scenario>_Real.csv
    Returns normalized weights summing to 1.0
    """
    base = Path("portfolio_targets")
    files = [base / f"allocation_targetVol_{vol_pct_tag}_{s}_Real.csv" for s in SCENARIOS]
    missing = [f for f in files if not f.exists()]
    if missing:
        miss = "\n  ".join(str(m.resolve()) for m in missing)
        raise SystemExit(f"Missing target files:\n  {miss}")

    parts = []
    for f in files:
        s = pd.read_csv(f, index_col=0).squeeze("columns").astype(float)
        parts.append(s)
    W = pd.concat(parts, axis=1).mean(axis=1).clip(lower=0.0)
    total = W.sum()
    if total <= 0:
        raise SystemExit("Target weights sum to zero.")
    return (W / total).rename("TargetWeight")

def write_csv(df: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)

def write_holdings(df: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)