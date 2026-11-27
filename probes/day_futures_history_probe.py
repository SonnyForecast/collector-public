"""Task 51 probe: is EEX day-future history before Oct 2026 reachable? (1) EEX public table-data for expired day
contracts (shortCode FCdd / maturity yyyymm; EEX's catalogue lists only the last ~45 days); (2) Energy-Charts
price_futures for a day product. Prints what answers; writes nothing."""
import requests
H = {"User-Agent": "hpfc-data-archiver/0.1 (personal research)", "Referer": "https://www.eex.com/", "Origin": "https://www.eex.com"}
API = "https://api.eex-group.com/pub/market-data/table-data"
s = requests.Session(); s.headers.update(H)
for area, code in (("CH", "FC"), ("DE", "DB"), ("FR", "F7")):
    for mat, day in (("202611", 20), ("202610", 20), ("202609", 15), ("202606", 10), ("202601", 20), ("202510", 15), ("202503", 12), ("202410", 8)):
        p = dict(shortCode=f"{code}{day:02d}", commodity="POWER", pricing="F", area=area, product="Base", maturity=mat,
                 startDate=f"{mat[:4]}-{mat[4:]}-01", endDate="2026-11-27", maturityType="Day", isRolling="true")
        try:
            r = s.get(API, params=p, timeout=60)
            j = r.json() if r.ok else {}
            rows = j.get("data") or []
            print(area, p["shortCode"], mat, "HTTP", r.status_code, "rows", len(rows), (rows[:2] if rows else r.text[:120]))
        except Exception as e:
            print(area, mat, "error", str(e)[:100])
for c in ("ch", "de", "fr"):
    for k in ("power_day", "power_weekend", "power_daily", "day"):
        u = f"https://www.energy-charts.info/charts/price_futures/data/{c}/{k}.json"
        try:
            r = requests.get(u, timeout=60)
            print("energy-charts", c, k, "HTTP", r.status_code, len(r.text), r.text[:150].replace("\n", " "))
        except Exception as e:
            print("energy-charts", c, k, "error", str(e)[:100])
