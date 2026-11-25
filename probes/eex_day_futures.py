"""EEX day futures (public, delayed, ~45 days kept): which areas list them and what they settled at each trade date.
Question (25 Nov 2026): is the settlement of the day-D contract on the evening of D−2 (known at our 07:00 issue on
D−1) a useful signal for the level of D? Writes eex_day_futures.csv (uploaded as a workflow artifact).
    python probes/eex_day_futures.py [areas, default CH,DE,FR,AT,IT]"""
import base64, csv, json, sys, time
from datetime import date, timedelta
import requests

API = "https://api.eex-group.com/pub/market-data/table-data"
CAT = "https://api.eex-group.com/pub/customise-widget/filter-data-with-scope"
H = {"User-Agent": "hpfc-data-archiver/0.1 (personal research)", "Referer": "https://www.eex.com/", "Origin": "https://www.eex.com"}
AREAS = (sys.argv[1] if len(sys.argv) > 1 else "CH,DE,FR,AT,IT").split(",")
s = requests.Session(); s.headers.update(H)
enc = base64.b64encode(json.dumps([dict(commodity="POWER", pricing="F", area="All", product="All", productSpecific="All",
                                        maturityType="All")]).encode()).decode()
j = None
for i in range(5):
    try:
        r = s.post(CAT, params={"data": enc}, data={"data": enc}, timeout=120)
        if r.status_code in (200, 201):
            j = r.json(); break
    except Exception as e:
        print("catalogue retry", e)
    time.sleep(5 * (i + 1))
ix = {h: k for k, h in enumerate(j["header"])}
g = lambda row, k: row[ix[k]] if k in ix else None
rows = j["data"]
types = {}
for row in rows:
    if g(row, "area") in AREAS:
        types.setdefault((g(row, "area"), g(row, "maturityType")), set()).add(g(row, "product"))
print("maturity types listed per area:")
for k in sorted(types):
    print("  ", k, sorted(types[k]))
day = [row for row in rows if g(row, "area") in AREAS and g(row, "maturityType") == "Day" and g(row, "product") in ("Base", "Peak")]
print("day contracts in catalogue:", len(day))
end = date.today(); start = end - timedelta(days=46)
out = []
for row in day:
    p = dict(shortCode=g(row, "shortCode"), commodity="POWER", pricing="F", area=g(row, "area"), product=g(row, "product"),
             maturity=g(row, "maturity") or "null", startDate=str(start), endDate=str(end), maturityType="Day", isRolling="true")
    for i in range(3):
        try:
            r = s.get(API, params=p, timeout=60)
            if r.status_code == 200:
                d = r.json(); hx = {h: k for k, h in enumerate(d.get("header") or [])}
                for x in d.get("data") or []:
                    gg = lambda n: x[hx[n]] if n in hx else None
                    out.append(dict(area=g(row, "area"), product=g(row, "product"), short_code=g(row, "shortCode"),
                                    maturity=g(row, "maturity"), display_day=g(row, "displayDay"),
                                    delivery_day=gg("deliveryDay") or gg("maturityDate"), trade_date=gg("tradeDate"),
                                    settlement=gg("settlPx"), volume=gg("totVolTrdd"), open_interest=gg("grossOpenInt")))
                break
            if r.status_code in (400, 404):
                break
        except Exception:
            pass
        time.sleep(3 * (i + 1))
    time.sleep(0.3)
with open("eex_day_futures.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0]) if out else ["area"]); w.writeheader(); w.writerows(out)
print("rows", len(out))
if out:
    print("example rows:", out[:3])
