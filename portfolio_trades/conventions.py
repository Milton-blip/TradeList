from dataclasses import dataclass

# Directory with 6 scenario files:
# allocation_targetVol_{VOL%}_{Scenario}_Real.csv
TARGETS_DIR = "portfolio_targets"

# Where to write CSV/PDF
OUTPUT_DIR = "outputs"

# Cash tolerance per account (absolute dollars)
DEFAULT_CASH_TOL = 100.0

# Scenarios in the average
SCENARIOS = ["Base","Disinflation","Reflation","HardLanding","Stagflation","Geopolitical"]

# Sleeve fallback proxies
FALLBACK_PROXY = {
    "US_Core":"SCHB","US_Value":"VTV","US_SmallValue":"VBR","US_Growth":"IVW",
    "Intl_DM":"VXUS","EM":"VWO","Energy":"XLE",
    "IG_Core":"AGG","Treasuries":"IEF","TIPS":"TIP",
    "EM_USD":"VWOB","IG_Intl_Hedged":"BNDX","Cash":"BIL"
}

# Direct symbol → sleeve mapping
MAP_TO_SLEEVE = {
    'IVW':'US_Growth','VOOG':'US_Growth','AMZN':'US_Growth',
    'SCHB':'US_Core','DFAU':'US_Core','SCHM':'US_Core',
    'SCHA':'US_SmallValue','VBR':'US_SmallValue',
    'IUSV':'US_Value','VTV':'US_Value','VOOV':'US_Value','MGV':'US_Value',
    'VXUS':'Intl_DM','VPL':'Intl_DM','FNDF':'Intl_DM','FNDC':'Intl_DM',
    'VWO':'EM','EMXC':'EM','FNDE':'EM','TSM':'EM',
    'XLE':'Energy','VDE':'Energy',
    'AGG':'IG_Core','SCHZ':'IG_Core',
    'VWOB':'EM_USD','BNDX':'IG_Intl_Hedged',
    'SPAXX':'Cash','FDRXX':'Cash','VMFXX':'Cash','BIL':'Cash'
}

# Tax status rules (regex applied to Account name, case-insensitive)
ACCOUNT_TAX_STATUS_RULES = [
    (r"\broth\b|\broth ira\b|vanguard roth|schwab roth", "ROTH IRA"),
    (r"\bhsa\b|fidelity hsa",                            "HSA"),
    (r"\bwing\b.*\btrust\b|\btrust\b",                   "Trust"),
]
DEFAULT_TAX_STATUS = "Taxable"

# Flat est LTCG tax rates by status
EST_TAX_RATE = {"HSA":0.0, "ROTH IRA":0.0, "Trust":0.20, "Taxable":0.15}

def is_cashlike(sym: str) -> bool:
    return str(sym).upper() in {"SPAXX","VMFXX","FDRXX","BIL","CASH"}

def is_automattic(sym: str, name: str) -> bool:
    s = str(sym).strip().upper(); n = str(name).strip().upper()
    return ("AUTOMATTIC" in n) or (s == "AUTOMATTIC")