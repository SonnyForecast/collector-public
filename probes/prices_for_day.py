"""Print the CH day-ahead prices of one delivery day from ENTSO-E (A44), for scoring a live forecast as soon as the
prices are out. Usage: python probes/prices_for_day.py 2026-11-26 [ZONE]   (default CH; quarter-hours averaged to hours)"""
import os, re, sys
from datetime import date, timedelta
sys.path.insert(0, "collectors")
import entsoe_api
from entsoe_api import EIC

D = date.fromisoformat(sys.argv[1]); zone = sys.argv[2] if len(sys.argv) > 2 else "CH"
# delivery day in CET: 23:00 UTC of D−1 to 23:00 UTC of D (winter)
# sequence 1 = the coupled day-ahead auction (SDAC); DE/AT also carry EXAA as sequence 2 (pass "2" as 3rd argument)
seq = sys.argv[3] if len(sys.argv) > 3 else "1"
p = {"documentType": "A44", "in_Domain": EIC[zone], "out_Domain": EIC[zone], "contract_MarketAgreement.type": "A01",
     "classificationSequence_AttributeInstanceComponent.position": seq,
     "periodStart": (D - timedelta(days=1)).strftime("%Y%m%d") + "2300", "periodEnd": D.strftime("%Y%m%d") + "2300"}
x = entsoe_api.fetch(os.environ["ENTSOE_TOKEN"], p, tries=2)
txt = x.decode() if isinstance(x, bytes) else str(x or "")
res = re.findall(r"<resolution>([^<]+)</resolution>", txt)
pts = [(int(a), float(b)) for a, b in re.findall(r"<position>(\d+)</position>\s*<price\.amount>([^<]+)</price\.amount>", txt)]
print("resolution", res[:2], "points", len(pts))
if pts:
    per_h = 4 if res and res[0] == "PT15M" else 1
    vals = dict(pts); n = max(vals)
    filled, last = [], None
    for i in range(1, n + 1):                      # A03 curve: a missing position repeats the previous value
        last = vals.get(i, last); filled.append(last)
    hours = [sum(filled[h * per_h:(h + 1) * per_h]) / per_h for h in range(len(filled) // per_h)]
    print("PRICES", zone + ("" if seq == "1" else f"_seq{seq}"), D, ",".join(f"{v:.3f}" for v in hours))
