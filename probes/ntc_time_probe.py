"""When are the day-ahead capacities (ENTSO-E A61, contract A01) of the Swiss borders published? createdDateTime is
just the time of the API answer, so instead this checks, run every hour in the morning, whether tomorrow's and the
day after's values are there yet (and prints them, to see later revisions)."""
import os, sys, re
from datetime import date, timedelta, datetime, timezone
sys.path.insert(0, "collectors")
import entsoe_api
from entsoe_api import EIC
tok = os.environ["ENTSOE_TOKEN"]
print("now UTC", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
for a, b in (("DE_LU", "CH"), ("CH", "DE_LU"), ("FR", "CH"), ("IT_NORTH", "CH"), ("CH", "IT_NORTH"), ("AT", "CH")):
    for d in range(1, 3):
        t0 = date.today() + timedelta(days=d)
        p = {"in_Domain": EIC[b], "out_Domain": EIC[a], "documentType": "A61", "contract_MarketAgreement.Type": "A01",
             "periodStart": t0.strftime("%Y%m%d") + "0000", "periodEnd": (t0 + timedelta(days=1)).strftime("%Y%m%d") + "0000"}
        try:
            x = entsoe_api.fetch(tok, p)
            if x is None:
                print(f"{a}>{b} delivery {t0}: none"); continue
            txt = x if isinstance(x, str) else (x.decode() if isinstance(x, bytes) else str(x))
            q = re.findall(r"<quantity>([^<]+)</quantity>", txt)
            print(f"{a}>{b} delivery {t0}: {len(q)} values, first {q[:3]}")
        except Exception as e:
            print(f"{a}>{b} delivery {t0}: none ({str(e)[:80]})")
