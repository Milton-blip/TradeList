
# ============================================
# file: portfolio_trades/sleeves.py
# ============================================
def sleeve_for(symbol: str, name: str) -> str:
    s = (symbol or "").upper().strip()
    n = (name or "").upper().strip()
    if "AUTOMATTIC" in n or s == "AUTOMATTIC": return "Illiquid_Automattic"
    MAP = {
        'IVW':'US_Growth','VOOG':'US_Growth','AMZN':'US_Growth',
        'SCHB':'US_Core','DFAU':'US_Core','SCHM':'US_Core','VB':'US_Core','VONG':'US_Core',
        'SCHA':'US_SmallValue','VBR':'US_SmallValue',
        'IUSV':'US_Value','VTV':'US_Value','VOOV':'US_Value','MGV':'US_Value','SCHV':'US_Value',
        'VXUS':'Intl_DM','VPL':'Intl_DM','FNDF':'Intl_DM','FNDC':'Intl_DM',
        'VWO':'EM','EMXC':'EM','FNDE':'EM','TSM':'EM',
        'XLE':'Energy','VDE':'Energy',
        'AGG':'IG_Core','SCHZ':'IG_Core',
        'VWOB':'EM_USD','BNDX':'IG_Intl_Hedged',
        'SPAXX':'Cash','FDRXX':'Cash','VMFXX':'Cash','BIL':'Cash','CASH':'Cash'
    }
    if s in MAP: return MAP[s]
    if "INFLATION" in n: return "TIPS"
    if any(k in n for k in ["UST","TREAS","STRIP","TREASUR"]): return "Treasuries"
    return "US_Core"

FALLBACK_PROXY = {
    "US_Core":"SCHB","US_Value":"VTV","US_SmallValue":"VBR","US_Growth":"IVW",
    "Intl_DM":"VXUS","EM":"VWO","Energy":"XLE",
    "IG_Core":"AGG","Treasuries":"IEF","TIPS":"TIP",
    "EM_USD":"VWOB","IG_Intl_Hedged":"BNDX","Cash":"BIL"
}
