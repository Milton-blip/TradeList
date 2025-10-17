SCENARIOS = ["Base","Disinflation","Reflation","HardLanding","Stagflation","Geopolitical"]

MAP_TO_SLEEVE = {...}  # your dict from the monolith
FALLBACK_PROXY = {...}

ACCOUNT_TAX_STATUS_RULES = [
    (r"\broth\b|\broth ira\b|vanguard roth|schwab roth", "ROTH IRA"),
    (r"\bhsa\b|fidelity hsa",                            "HSA"),
    (r"\bwing\b.*\btrust\b|\btrust\b",                   "Trust"),
]
DEFAULT_TAX_STATUS = "Taxable"

EST_TAX_RATE = {
    "HSA": 0.00, "ROTH IRA": 0.00, "Trust": 0.20, "Taxable": 0.15
}