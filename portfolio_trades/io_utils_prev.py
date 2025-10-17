# portfolio_trades/io_utils.py
from __future__ import annotations

from pathlib import Path
import datetime
import pandas as pd
import re

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

def _to_num(series: pd.Series) -> pd.Series:
    """Coerce money/number strings to float. Handles $ , and parentheses."""
    if not isinstance(series, pd.Series):
        series = pd.Series(series)
    s = series.astype(str)
    # turn (1234.56) into -1234.56
    s = s.str.replace(r"\(", "-", regex=True).str.replace(r"\)", "", regex=True)
    # strip $ and commas/spaces
    s = s.str.replace(r"[\$,]", "", regex=True).str.strip()
    s = s.replace({"nan": "", "None": ""})
    return pd.to_numeric(s, errors="coerce")

def load_holdings(path: str) -> pd.DataFrame:
    """
    Robustly locate and normalize holdings CSV.

    Accepted input column names (we map them to canonical names):
      - Symbol → Symbol
      - Name → Name
      - Account → Account
      - TaxStatus → TaxStatus
      - Quantity → Quantity
      - PricePerShare | Price | CurrentPrice → Price
      - CostPerShare | AverageCost → AverageCost
      - MarketValue | CurrentValue | Value → Value
      - TotalCost | Cost → Cost
      - Sleeve (optional) → Sleeve
      - Tradable (optional) → Tradable
      - Notes (optional) → Notes

    Returns a DataFrame with canonical columns at least:
      Symbol, Name, Account, TaxStatus, Quantity, Price,
      AverageCost, Value, Cost, Sleeve, Tradable, _ident
    """
    candidates = [
        Path(path),
        Path("data/holdings.csv"),
        Path("inputs/holdings.csv"),
        Path("holdings.csv"),
    ]
    found = _first_existing(candidates)
    if not found:
        tried = "\n  ".join(str(p.resolve()) for p in candidates)
        raise SystemExit(f"holdings.csv not found. Tried:\n  {tried}")

    df = pd.read_csv(found)

    # --- Rename to canonical internal names ---
    rename_priority = [
        ("PricePerShare", "Price"),
        ("CurrentPrice", "Price"),
        ("Price", "Price"),
        ("CostPerShare", "AverageCost"),
        ("AverageCost", "AverageCost"),
        ("MarketValue", "Value"),
        ("CurrentValue", "Value"),
        ("Value", "Value"),
        ("TotalCost", "Cost"),
        ("Cost", "Cost"),
    ]
    # build a rename map using first match present in df
    rename_map: dict[str, str] = {}
    present = set(df.columns)
    for src, dst in rename_priority:
        if src in present and dst not in rename_map.values():
            rename_map[src] = dst
    df = df.rename(columns=rename_map)

    # Ensure required logical columns exist
    required_min = ["Symbol","Name","Account","TaxStatus","Quantity"]
    for col in required_min:
        if col not in df.columns:
            raise SystemExit(f"holdings.csv is missing required column '{col}'.")

    # Create optional columns if missing (will compute/fill below)
    for col in ["Price","AverageCost","Value","Cost","Sleeve","Tradable","Notes"]:
        if col not in df.columns:
            if col in ("Sleeve","Tradable","Notes"):
                df[col] = ""
            else:
                df[col] = 0.0

    # --- Numeric coercion for key fields ---
    for col in ["Quantity","Price","AverageCost","Value","Cost"]:
        df[col] = _to_num(df[col]).fillna(0.0)

    # Back-fill computed values if needed
    # Value = shares * price; Cost = shares * average cost
    if (df["Value"] == 0).any() or "MarketValue" in rename_map:
        df["Value"] = (df["Quantity"] * df["Price"]).round(2)
    if (df["Cost"] == 0).any() or "TotalCost" in rename_map or "Cost" in rename_map:
        df["Cost"] = (df["Quantity"] * df["AverageCost"]).round(2)

    # Identifier used throughout engine
    df["_ident"] = df["Symbol"].astype(str)

    # Normalize Tradable to {True/False} if present as strings like "Y"/"N"
    if "Tradable" in df.columns:
        def _to_bool(x):
            if isinstance(x, str):
                return x.strip().lower() in {"y","yes","true","1"}
            return bool(x)
        df["Tradable"] = df["Tradable"].map(_to_bool)

    return df

def load_targets(vol_pct_tag: int) -> pd.Series:
    """
    Load and average target sleeves from portfolio_targets/ directory.
    Expected filenames:
      portfolio_targets/allocation_targetVol_<vol%>_<Scenario>_Real.csv
    Returns a normalized Series (weights sum to 1.0).
    """
    base = Path("portfolio_targets")
    files = [
        base / f"allocation_targetVol_{vol_pct_tag}_{s}_Real.csv"
        for s in SCENARIOS
    ]
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
        raise SystemExit("Target weights sum to 0 after loading targets.")
    return (W / total).rename("TargetWeight")

def write_csv(df: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)

def write_holdings(df: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)