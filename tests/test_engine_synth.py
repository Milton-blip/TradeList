import pandas as pd
from portfolio_trades.engine import build_trades_and_afterholdings
from portfolio_trades.io_utils import load_targets

def test_balanced_trades():
    h = pd.read_csv("tests/fixtures/holdings_synth.csv")
    W = load_targets(8, base_dir="tests/fixtures/portfolio_targets")
    tx, after, residuals = build_trades_and_afterholdings(h, W, cash_tolerance=100)

    # Sells never exceed holdings
    held = h.groupby(["Account","Symbol"])["Quantity"].sum()
    traded = tx.groupby(["Account","Identifier"])["Shares_Delta"].sum()
    for (acct, sym), delta in traded.items():
        if delta < 0:
            assert abs(delta) <= held.get((acct, sym), 0) + 1e-6

    # Each account cash balanced within $100
    for acct, amt in residuals.items():
        assert abs(amt) <= 100