from pathlib import Path
import re
import sys

ENGINE = Path("portfolio_trades/engine.py")

def fail(msg):
    print(f"[PATCH ERROR] {msg}")
    sys.exit(1)

if not ENGINE.exists():
    fail(f"Cannot find {ENGINE}")

src = ENGINE.read_text()

# ---- Step A: Replace the in-account per-sleeve trade loop with accumulation only ----
# We anchor on the exact comment + the 'delta = (tgt - cur)' line, then the loop.
anchor_a = r'(#\s*Sleeve deltas\s*\(dollars\)\s*\n\s*delta\s*=\s*\(tgt\s*-\s*cur\)\s*\n)'
m = re.search(anchor_a, src)
if not m:
    fail("Could not find the sleeve-deltas anchor (comment + delta calc).")

start_idx = m.end()

# Find the 'for sleeve, d_dollars in delta.items():' that follows
m2 = re.search(r'(?P<indent>[ \t]*)for\s+sleeve,\s*d_dollars\s+in\s+delta\.items\(\):\s*\n', src[start_idx:])
if not m2:
    fail("Could not find the per-sleeve loop after the delta=(tgt-cur) line.")
loop_rel_start = start_idx + m2.start()
indent = m2.group("indent")

# We need to cut the entire loop block until it dedents to the same 'indent' level.
# Simple block scanner:
lines = src.splitlines(keepends=True)
# Compute absolute line index of loop start
prefix = src[:loop_rel_start]
loop_line_idx = prefix.count("\n")  # 0-based

def leading_ws(s):  # count leading spaces/tabs
    return len(s) - len(s.lstrip(" \t"))

loop_indent_str = indent
loop_indent_len = leading_ws(loop_indent_str)

# Find loop end by dedent
end_line_idx = loop_line_idx + 1
while end_line_idx < len(lines):
    line = lines[end_line_idx]
    # stop when we hit a line with fewer indentation (dedent) and not blank/comment
    if line.strip() != "" and not line.lstrip().startswith("#"):
        if leading_ws(line) < loop_indent_len:
            break
    end_line_idx += 1

# Build the replacement block: accumulate deltas only (no trades here)
accum_block = (
    f"{indent}# --- collect for consolidation; do not place trades here ---\n"
    f"{indent}if '___acct_deltas' not in locals():\n"
    f"{indent}    ___acct_deltas = {{}}\n"
    f"{indent}for sleeve, d_dollars in delta.items():\n"
    f"{indent}    if sleeve == 'Illiquid_Automattic':\n"
    f"{indent}        continue\n"
    f"{indent}    ___acct_deltas.setdefault(sleeve, {{}})\n"
    f"{indent}    ___acct_deltas[sleeve][acct] = float(d_dollars)\n"
)

# Splice in the accumulation block
before = "".join(lines[:loop_line_idx])
middle = accum_block
after  = "".join(lines[end_line_idx:])

src2 = before + middle + after

# ---- Step B: Insert consolidation pass BEFORE 'tx = pd.DataFrame(trades)' ----
anchor_b = r'\ntx\s*=\s*pd\.DataFrame\(\s*trades\s*\)\s*'
m3 = re.search(anchor_b, src2)
if not m3:
    fail("Could not find 'tx = pd.DataFrame(trades)' to insert consolidation before.")

insertion_pt = m3.start()

consol_block = r"""
    # ---------- CONSOLIDATION PASS ----------
    # Route all BUY dollars for each sleeve to one preferred account (Roth/HSA first),
    # keep SELLs in their original account to avoid extra realized gains elsewhere.
    trades = []

    # Helper: choose preferred account for a sleeve
    def _preferred_acct_for_sleeve(sleeve: str) -> str | None:
        # compute sleeve dollars per acct (existing footprint) and account totals
        sleeve_dollars_by_acct = (
            df[df["Sleeve"] == sleeve].groupby("Account")["Value"].sum()
            if (df["Sleeve"] == sleeve).any() else pd.Series(dtype=float)
        )
        acct_total_val = df.groupby("Account")["Value"].sum()

        roth_hsa, taxable_trust = [], []
        for acct in acct_total_val.index:
            status = assign_tax_status(acct).lower()
            entry = (acct,
                     float(sleeve_dollars_by_acct.get(acct, 0.0)),
                     float(acct_total_val.get(acct, 0.0)))
            if ("roth" in status) or ("hsa" in status):
                roth_hsa.append(entry)
            else:
                taxable_trust.append(entry)

        roth_hsa.sort(key=lambda x: (x[1], x[2]), reverse=True)
        taxable_trust.sort(key=lambda x: (x[1], x[2]), reverse=True)

        if roth_hsa:
            return roth_hsa[0][0]
        if taxable_trust:
            return taxable_trust[0][0]
        return acct_total_val.sort_values(ascending=False).index[0] if not acct_total_val.empty else None

    price_map = df.groupby("_ident")["Price"].median().to_dict()
    acct_sleeve_ident = (
        df.groupby(["Account","Sleeve","_ident"])["Value"].sum().reset_index()
          .sort_values(["Account","Sleeve","Value"], ascending=[True,True,False])
          .drop_duplicates(["Account","Sleeve"])
          .set_index(["Account","Sleeve"])["_ident"].to_dict()
    )

    # PASS 1: place SELLs where they originate
    for sleeve, per_acct in (___acct_deltas if '___acct_deltas' in locals() else {}).items():
        for acct, d_dollars in per_acct.items():
            if d_dollars >= 0:
                continue
            ident = acct_sleeve_ident.get((acct, sleeve)) or FALLBACK_PROXY.get(sleeve)
            if ident is None:
                continue
            px = float(price_map.get(ident, 0.0))
            if not np.isfinite(px) or px <= 0:
                continue
            g = df[df["Account"] == acct]
            held_sh = float(g.loc[g["_ident"] == ident, "Quantity"].sum())
            if held_sh <= 0:
                continue
            sh = _round_shares(d_dollars, px, ident)  # negative
            sh = -min(abs(sh), abs(held_sh))
            if sh == 0:
                continue
            rows_ident = g[g["_ident"] == ident]
            if not rows_ident.empty and rows_ident["Quantity"].sum() > 0:
                tot_sh = float(rows_ident["Quantity"].sum())
                avgc = float((rows_ident["AverageCost"] * rows_ident["Quantity"]).sum() / tot_sh)
            else:
                avgc = 0.0
            capgain = (px - avgc) * abs(sh)
            trades.append({
                "Account": acct,
                "TaxStatus": g["TaxStatus"].iloc[0] if "TaxStatus" in g.columns else assign_tax_status(acct),
                "Identifier": ident,
                "Sleeve": sleeve,
                "Action": "SELL",
                "Shares_Delta": sh,
                "Price": px,
                "AverageCost": avgc,
                "Delta_Dollars": sh * px,
                "CapGain_Dollars": capgain,
            })

    # PASS 2: pool all BUY dollars into one preferred account per sleeve
    for sleeve, per_acct in (___acct_deltas if '___acct_deltas' in locals() else {}).items():
        total_buy = sum(v for v in per_acct.values() if v > 0)
        if total_buy <= 0:
            continue
        acct = _preferred_acct_for_sleeve(sleeve)
        if acct is None:
            continue
        ident = acct_sleeve_ident.get((acct, sleeve)) or FALLBACK_PROXY.get(sleeve)
        if ident is None:
            continue
        px = float(price_map.get(ident, 0.0))
        if not np.isfinite(px) or px <= 0:
            continue
        sh = _round_shares(total_buy, px, ident)
        if sh == 0:
            continue
        g = df[df["Account"] == acct]
        rows_ident = g[g["_ident"] == ident]
        if not rows_ident.empty and rows_ident["Quantity"].sum() > 0:
            tot_sh = float(rows_ident["Quantity"].sum())
            avgc = float((rows_ident["AverageCost"] * rows_ident["Quantity"]).sum() / tot_sh)
        else:
            avgc = 0.0
        trades.append({
            "Account": acct,
            "TaxStatus": g["TaxStatus"].iloc[0] if "TaxStatus" in g.columns else assign_tax_status(acct),
            "Identifier": ident,
            "Sleeve": sleeve,
            "Action": "BUY",
            "Shares_Delta": sh,
            "Price": px,
            "AverageCost": avgc,
            "Delta_Dollars": sh * px,
            "CapGain_Dollars": 0.0,
        })
"""

# Insert just before 'tx = pd.DataFrame(trades)'
src3 = src2[:insertion_pt] + consol_block + src2[insertion_pt:]

# Backup and write
ENGINE.with_suffix(".py.bak").write_text(src)
ENGINE.write_text(src3)
print("Patched:", ENGINE)
print("Backup at:", ENGINE.with_suffix(".py.bak"))