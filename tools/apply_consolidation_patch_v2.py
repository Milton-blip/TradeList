from pathlib import Path
import re, sys

ENGINE = Path("portfolio_trades/engine.py")

def die(msg):
    print(f"[PATCH ERROR] {msg}")
    sys.exit(1)

if not ENGINE.exists():
    die(f"Cannot find {ENGINE}")

text = ENGINE.read_text()

# ---------- A) Find the start of the account loop ----------
m_acct = re.search(r'\n(?P<indent>[ \t]*)for\s+acct,\s*g\s+in\s+df\.groupby\(\s*"Account"\s*\)\s*:\s*\n', text)
if not m_acct:
    die("Could not find: for acct, g in df.groupby(\"Account\"):")
acct_indent = m_acct.group("indent")
acct_loop_start = m_acct.start()

# Compute end (dedent) of that account loop block
lines = text.splitlines(keepends=True)
start_line = text[:acct_loop_start].count("\n") + 1  # 1-based
def lws(s): return len(s) - len(s.lstrip(" \t"))
acct_indent_len = lws(acct_indent)
i = start_line
while i < len(lines):
    line = lines[i]
    if line.strip() and not line.lstrip().startswith("#") and lws(line) < acct_indent_len:
        break
    i += 1
acct_loop_end_abs_line = i  # first line after the loop block
acct_loop_end_abs_idx = sum(len(l) for l in lines[:acct_loop_end_abs_line])

# ---------- B) Inside the account loop, replace the per-sleeve trading loop with accumulation ----------
# Find the sleeve delta calc inside the loop
m_delta = re.search(r'(#\s*Sleeve deltas\s*\(dollars\)\s*\n[ \t]*delta\s*=\s*\(tgt\s*-\s*cur\)\s*\n)', text[m_acct.end():])
if not m_delta:
    die("Could not find sleeve-deltas anchor (comment + delta calc) inside account loop.")
delta_block_start = m_acct.end() + m_delta.end()

# Find the 'for sleeve, d_dollars in delta.items():' loop that follows
m_sleeve = re.search(r'(?P<indent>[ \t]*)for\s+sleeve,\s*d_dollars\s+in\s+delta\.items\(\)\s*:\s*\n', text[delta_block_start:])
if not m_sleeve:
    die("Could not find per-sleeve loop after delta calc.")
sleeve_loop_abs_idx = delta_block_start + m_sleeve.start()
sleeve_indent = m_sleeve.group("indent")
sleeve_indent_len = lws(sleeve_indent)

# Compute end of sleeve loop block by dedent
s_lines = text.splitlines(keepends=True)
s_start_line = text[:sleeve_loop_abs_idx].count("\n") + 1
j = s_start_line + 1
while j < len(s_lines):
    line = s_lines[j]
    if line.strip() and not line.lstrip().startswith("#") and lws(line) < sleeve_indent_len:
        break
    j += 1
sleeve_loop_end_abs_idx = sum(len(l) for l in s_lines[:j])

accum_block = (
    f"{sleeve_indent}# --- accumulate per-sleeve per-account dollars; no trades here ---\n"
    f"{sleeve_indent}if '___acct_deltas' not in locals():\n"
    f"{sleeve_indent}    ___acct_deltas = {{}}\n"
    f"{sleeve_indent}for sleeve, d_dollars in delta.items():\n"
    f"{sleeve_indent}    if sleeve == 'Illiquid_Automattic':\n"
    f"{sleeve_indent}        continue\n"
    f"{sleeve_indent}    ___acct_deltas.setdefault(sleeve, {{}})\n"
    f"{sleeve_indent}    ___acct_deltas[sleeve][acct] = float(d_dollars)\n"
)

text2 = text[:sleeve_loop_abs_idx] + accum_block + text[sleeve_loop_end_abs_idx:]

# Recompute acct loop end in the new text
lines2 = text2.splitlines(keepends=True)
acct_loop_end_abs_idx = None
depth = 0
# Re-find the account loop to be safe
m_acct2 = re.search(r'\n(?P<indent>[ \t]*)for\s+acct,\s*g\s+in\s+df\.groupby\(\s*"Account"\s*\)\s*:\s*\n', text2)
if not m_acct2:
    die("Internal: lost account loop after rewrite.")
acct_indent2 = m_acct2.group("indent")
acct_indent_len2 = lws(acct_indent2)
start_line2 = text2[:m_acct2.start()].count("\n") + 1
k = start_line2 + 1
while k < len(lines2):
    line = lines2[k]
    if line.strip() and not line.lstrip().startswith("#") and lws(line) < acct_indent_len2:
        acct_loop_end_abs_idx = sum(len(l) for l in lines2[:k])
        break
    k += 1
if acct_loop_end_abs_idx is None:
    acct_loop_end_abs_idx = len(text2)

# ---------- C) Insert consolidation pass just AFTER the account loop ----------
consol = f"""
    # ---------- CONSOLIDATION PASS ----------
    # Pool all BUY dollars by sleeve into one preferred account (Roth/HSA first),
    # keep SELLs in their source accounts.
    trades = []

    def _preferred_acct_for_sleeve(sleeve: str) -> str | None:
        sleeve_dollars_by_acct = (df[df["Sleeve"] == sleeve].groupby("Account")["Value"].sum()
                                  if (df["Sleeve"] == sleeve).any() else pd.Series(dtype=float))
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

    # SELLs stay in-place
    for sleeve, per_acct in (___acct_deltas if '___acct_deltas' in locals() else {{}}).items():
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
            trades.append({{
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
            }})

    # BUYs pooled to one preferred account
    for sleeve, per_acct in (___acct_deltas if '___acct_deltas' in locals() else {{}}).items():
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
        trades.append({{
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
        }})
""".rstrip("\n") + "\n"

text3 = text2[:acct_loop_end_abs_idx] + consol + text2[acct_loop_end_abs_idx:]

# Write backup + patched
ENGINE.with_suffix(".py.bak").write_text(text)
ENGINE.write_text(text3)
print("Patched:", ENGINE)
print("Backup:", ENGINE.with_suffix(".py.bak"))