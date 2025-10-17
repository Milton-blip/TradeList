#!/usr/bin/env python3
import argparse, hashlib, pandas as pd

def _hash(name: str, salt: str) -> str:
    h = hashlib.sha256((salt + "|" + str(name)).encode()).hexdigest()[:10]
    return f"{name[:1]}_{h}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--infile", required=True)
    ap.add_argument("--outfile", required=True)
    ap.add_argument("--salt", required=True)
    ap.add_argument("--scale", type=float, default=0.75)
    args = ap.parse_args()

    df = pd.read_csv(args.infile)
    df["Account"] = df["Account"].apply(lambda x: _hash(str(x), args.salt))
    df["Name"]    = df["Name"].apply(lambda x: _hash(str(x), args.salt))

    for col in ["Quantity","PricePerShare","MarketValue","CostPerShare","TotalCost"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
            if col not in ["PricePerShare", "CostPerShare"]:
                df[col] *= args.scale

    df["MarketValue"] = df["Quantity"] * df["PricePerShare"]
    df["TotalCost"]   = df["Quantity"] * df["CostPerShare"]
    df.to_csv(args.outfile, index=False)
    print(f"Anonymized written: {args.outfile}")

if __name__ == "__main__":
    main()