import re
from .config import MAP_TO_SLEEVE, ACCOUNT_TAX_STATUS_RULES, DEFAULT_TAX_STATUS

def is_automattic(sym: str, name: str) -> bool:
    s = (sym or "").upper(); n = (name or "").upper()
    return "AUTOMATTIC" in (s + " " + n)

def map_sleeve(sym: str, name: str) -> str:
    s = (sym or "").upper().strip()
    n = (name or "").upper().strip()
    if is_automattic(sym, name): return "Illiquid_Automattic"
    if s in MAP_TO_SLEEVE: return MAP_TO_SLEEVE[s]
    if "INFLATION" in n: return "TIPS"
    if any(k in n for k in ["UST","TREAS","STRIP"]): return "Treasuries"
    return "US_Core"

def assign_tax_status(acct: str) -> str:
    low = (acct or "").lower()
    for pat, status in ACCOUNT_TAX_STATUS_RULES:
        if re.search(pat, low): return status
    return DEFAULT_TAX_STATUS

def is_cashlike(sym: str) -> bool:
    return (sym or "").upper() in {"SPAXX","VMFXX","FDRXX","BIL","CASH"}