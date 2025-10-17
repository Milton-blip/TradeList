EST_TAX_RATE = {
    "HSA": 0.00,
    "ROTH IRA": 0.00,
    "Trust": 0.20,
    "Taxable": 0.15,
}

def tax_rate_for(status: str) -> float:
    return EST_TAX_RATE.get(str(status), 0.15)