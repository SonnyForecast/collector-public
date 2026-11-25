#!/usr/bin/env python3
"""Archive EEX day futures settlements (public, delayed; EEX keeps each contract only ~45 days) for CH, DE, FR, AT,
IT, Base and Peak -> data/eex/day_futures.csv in the data repo (DATA_ROOT). One row per (area, product, delivery day,
trade date). Why (25 Nov 2026): the CH day contract for D settled on the evening of D−2 is known at the 07:00 issue on
D−1 and carried the level the members missed on 26 Nov (settled 288, actual 280, ensemble 258); 37-day test in
spot-ensemble CH/results/day_futures_20261125. Personal, non-commercial research use (EEX Market Data Hub).
    python collect_eex_day.py [--days 12]     (first run / after a gap: --days 46)"""
import argparse
import base64
import csv
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import requests

API = "https://api.eex-group.com/pub/market-data/table-data"
CAT = "https://api.eex-group.com/pub/customise-widget/filter-data-with-scope"
H = {"User-Agent": "hpfc-data-archiver/0.1 (personal research)", "Referer": "https://www.eex.com/", "Origin": "https://www.eex.com"}
AREAS = ("CH", "DE", "FR", "AT", "IT")
FIELDS = ["area", "product", "delivery", "trade_date", "settlement", "volume_mwh", "open_interest_lots", "short_code"]


def catalogue(s):
    enc = base64.b64encode(json.dumps([dict(commodity="POWER", pricing="F", area="All", product="All",
                                            productSpecific="All", maturityType="All")]).encode()).decode()
    for i in range(5):
        try:
            r = s.post(CAT, params={"data": enc}, data={"data": enc}, timeout=120)
            if r.status_code in (200, 201):
                j = r.json(); ix = {h: k for k, h in enumerate(j["header"])}
                return [{k: row[ix[k]] for k in ix} for row in j["data"]]
        except (requests.RequestException, ValueError):
            pass
        time.sleep(5 * (i + 1))
    raise RuntimeError("EEX catalogue not available")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--days", type=int, default=12); a = ap.parse_args()
    out = Path(os.environ.get("DATA_ROOT", ".")) / "data" / "eex" / "day_futures.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    if out.exists():
        with open(out, newline="") as f:
            for r in csv.DictReader(f):
                old[(r["area"], r["product"], r["delivery"], r["trade_date"])] = r
    s = requests.Session(); s.headers.update(H)
    cat = [c for c in catalogue(s) if c.get("area") in AREAS and c.get("maturityType") == "Day"
           and c.get("product") in ("Base", "Peak")]
    end = date.today(); start = end - timedelta(days=a.days)
    n_new = n_fail = 0
    for c in cat:
        try:
            deliv = date(int(str(c["maturity"])[:4]), int(str(c["maturity"])[4:6]), int(c["displayDay"])).isoformat()
        except (KeyError, ValueError, TypeError):
            continue
        p = dict(shortCode=c["shortCode"], commodity="POWER", pricing="F", area=c["area"], product=c["product"],
                 maturity=c["maturity"], startDate=str(start), endDate=str(end), maturityType="Day", isRolling="true")
        for i in range(3):
            try:
                r = s.get(API, params=p, timeout=60)
                if r.status_code == 200:
                    j = r.json(); hx = {h: k for k, h in enumerate(j.get("header") or [])}
                    for x in j.get("data") or []:
                        g = lambda n: x[hx[n]] if n in hx else None      # noqa: E731
                        if g("settlPx") is None:
                            continue
                        key = (c["area"], c["product"], deliv, g("tradeDate"))
                        if key not in old:
                            n_new += 1
                        old[key] = dict(area=c["area"], product=c["product"], delivery=deliv, trade_date=g("tradeDate"),
                                        settlement=g("settlPx"), volume_mwh=v(g("totVolTrdd")),
                                        open_interest_lots=v(g("grossOpenInt")), short_code=c["shortCode"])
                    break
                if r.status_code in (400, 404):
                    break
            except (requests.RequestException, ValueError):
                pass
            time.sleep(3 * (i + 1))
        else:
            n_fail += 1
        time.sleep(0.3)
    rows = sorted(old.values(), key=lambda r: (r["area"], r["product"], r["delivery"], r["trade_date"]))
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    print(f"day futures: {len(cat)} contracts listed, {n_fail} failed, {n_new} new rows, {len(rows)} in {out}")


def v(x):
    return "" if x is None else x


if __name__ == "__main__":
    main()
