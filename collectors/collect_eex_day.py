#!/usr/bin/env python3
"""Archive EEX day futures settlements (public, delayed; EEX keeps each contract only ~45 days) for CH, DE, FR, AT,
IT, Base and Peak -> data/eex/day_futures.csv in the data repo (DATA_ROOT); single-weekend contracts (EEX maturity
type "Weekend", Sat+Sun) of the same areas -> data/eex/weekend_futures.csv (added 28 Nov 2026, delivery = the
Saturday; for crash-depth tests on solar-surplus weekends). One row per (area, product, delivery day,
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


def deliv_of(c, mtype):
    """Day: delivery day from maturity (yyyymm) + displayDay. Weekend: EEX numbers weekends within the month like the
    weeks (short code ends in n = 1..5, no displayDay); returns the Saturday of the n-th weekend whose Sunday falls in
    the month (the CH week convention). Checked against the Sat/Sun day futures (spot-ensemble
    CH/results/solar_crash_20261128/check_weekend_mapping.py); the raw maturity and n are kept in the file so another
    convention can be re-mapped without a re-download."""
    y, m = int(str(c["maturity"])[:4]), int(str(c["maturity"])[4:6])
    if mtype == "Day":
        return date(y, m, int(c["displayDay"])).isoformat()
    n = int(str(c["shortCode"])[-1])
    first = date(y, m, 1)
    sunday = first + timedelta(days=(6 - first.weekday()) % 7) + timedelta(days=7 * (n - 1))
    if sunday.month != m:
        raise ValueError("no such weekend")
    return (sunday - timedelta(days=1)).isoformat()


FILES = {"Day": "day_futures.csv", "Weekend": "weekend_futures.csv"}
WE_FIELDS = FIELDS + ["maturity", "weekend_n"]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--days", type=int, default=12); a = ap.parse_args()
    s = requests.Session(); s.headers.update(H)
    full = catalogue(s)
    for mtype, name in FILES.items():
        collect(s, full, mtype, Path(os.environ.get("DATA_ROOT", ".")) / "data" / "eex" / name, a.days)


def collect(s, full, mtype, out, days):
    out.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    if out.exists():
        with open(out, newline="") as f:
            for r in csv.DictReader(f):
                old[(r["area"], r["product"], r["delivery"], r["trade_date"])] = r
    cat = [c for c in full if c.get("area") in AREAS and c.get("maturityType") == mtype
           and c.get("product") in ("Base", "Peak")]
    if mtype != "Day" and cat:      # log what EEX sends for these contracts, so the delivery mapping can be checked
        print(mtype, "catalogue sample:", {k: cat[0].get(k) for k in ("area", "product", "shortCode", "maturity", "displayDay")})
    end = date.today(); start = end - timedelta(days=days)
    n_new = n_fail = n_odd = 0
    for c in cat:
        try:
            deliv = deliv_of(c, mtype)
        except (KeyError, ValueError, TypeError):
            continue
        p = dict(shortCode=c["shortCode"], commodity="POWER", pricing="F", area=c["area"], product=c["product"],
                 maturity=c["maturity"], startDate=str(start), endDate=str(end), maturityType=mtype, isRolling="true")
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
                        if mtype == "Weekend":
                            old[key].update(maturity=c["maturity"], weekend_n=str(c["shortCode"])[-1])
                            if not n_odd:      # once per run: what the API sends for a weekend contract
                                print("Weekend data header:", list(hx)); n_odd = 1
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
        w = csv.DictWriter(f, fieldnames=WE_FIELDS if mtype == "Weekend" else FIELDS); w.writeheader(); w.writerows(rows)
    print(f"{mtype} futures: {len(cat)} contracts listed, {n_fail} failed, {n_new} new rows, {len(rows)} in {out}")


def v(x):
    return "" if x is None else x


if __name__ == "__main__":
    main()
