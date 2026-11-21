#!/usr/bin/env python3
"""Refresh raw data into data/raw/ (run on your own PC; needs internet).

  python collect_data.py                 # everything
  python collect_data.py spot            # only hourly spot prices
  python collect_data.py futures bfe     # futures + Swiss reservoir levels

Sources
  futures : Energy-Charts (Fraunhofer ISE, CC BY 4.0) - EEX base power futures settlements
            (year/quarter/month/week) for CH, DE, FR, AT, daily since 2021
  spot    : Energy-Charts API hourly day-ahead prices for CH, DE-LU, FR, IT-North, AT.
            Cached per year, so a rerun after a 429/503 only fetches what is missing.
            Falls back to ENTSO-E (entsoe-py) if ENTSOE_API_KEY is set and Energy-Charts fails.
  bfe     : SFOE weekly reservoir fill levels (4 regions, since 2000)
"""
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

import os as _os
# Data folder: the checked-out private data repo (env DATA_ROOT); default = next to this script
DATA_HOME = Path(_os.environ.get("DATA_ROOT", Path(__file__).resolve().parent))

ROOT = DATA_HOME / "data"
EC = Path(os.environ["EC_DIR"]) if os.environ.get("EC_DIR") else ROOT / "energy_charts"   # EC_DIR: build cache
UA = {"User-Agent": "hpfc-data-archiver/0.1 (personal research)"}
ZONES = ["CH", "DE-LU", "FR", "IT-North", "AT"]
ENTSOE_CODE = {"CH": "CH", "DE-LU": "DE_LU", "FR": "FR", "IT-North": "IT_NORD", "AT": "AT"}


def get(url, tries=12):
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=90)
        except requests.RequestException as e:
            print("  network error, retrying:", e); time.sleep(10); continue
        if r.status_code in (429, 502, 503, 504):
            wait = min(20 * (i + 1), 120)
            print(f"  HTTP {r.status_code}, waiting {wait}s"); time.sleep(wait); continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"gave up: {url}")


def futures():
    EC.mkdir(parents=True, exist_ok=True)
    for c in ["ch", "de", "fr", "at"]:
        # DE also carries EEX THE gas futures (daily settlements per contract since 2021): daily gas input for the
        # spot ensemble (spot-ensemble/CH) and the HPFC fuel history.
        kinds = ["power_year", "power_quarter", "power_month", "power_week"]
        if c == "de":
            kinds += ["gas_month", "gas_year"]
        for k in kinds:
            r = get(f"https://www.energy-charts.info/charts/price_futures/data/{c}/{k}.json")
            (EC / f"ec_futures_{c}_{k}.json").write_text(r.text, encoding="utf-8")
            print("futures", c, k, f"{len(r.text) / 1e3:.0f} kB"); time.sleep(1)


def _spot_year_entsoe(zone, y, end):
    import pandas as pd
    from entsoe import EntsoePandasClient
    cl = EntsoePandasClient(api_key=os.environ["ENTSOE_API_KEY"])
    s = cl.query_day_ahead_prices(ENTSOE_CODE[zone], start=pd.Timestamp(f"{y}-01-01", tz="Europe/Zurich"),
                                  end=pd.Timestamp(end, tz="Europe/Zurich"))
    return {"unix_seconds": [int(t.timestamp()) for t in s.index], "price": [float(v) for v in s.values]}


def spot(first_year=2019):
    cache = EC / "spot_years"; cache.mkdir(parents=True, exist_ok=True)
    this = date.today().year
    for z in ZONES:
        ts, px = [], []
        for y in range(first_year, this + 1):
            f = cache / f"{z}_{y}.json"
            if f.exists() and y < this:          # past years never change: reuse cache
                j = json.loads(f.read_text(encoding="utf-8"))
            else:
                end = f"{y + 1}-01-01" if y < this else (date.today() + timedelta(days=2)).isoformat()
                try:
                    j = get(f"https://api.energy-charts.info/price?bzn={z}&start={y}-01-01&end={end}").json()
                    j = {"unix_seconds": j["unix_seconds"], "price": j["price"]}
                except Exception as e:
                    if not os.environ.get("ENTSOE_API_KEY"):
                        raise
                    print(f"  Energy-Charts failed ({e}); using ENTSO-E")
                    j = _spot_year_entsoe(z, y, end)
                f.write_text(json.dumps(j), encoding="utf-8"); time.sleep(4)
            ts += j["unix_seconds"]; px += j["price"]
            print("spot", z, y, len(j["unix_seconds"]), "points")
        (EC / f"ec_spot_{z}.json").write_text(json.dumps(
            {"bzn": z, "unix_seconds": ts, "price": px, "source": "Energy-Charts API (CC BY 4.0)"}), encoding="utf-8")


def bfe():
    d = ROOT / "bfe"; d.mkdir(parents=True, exist_ok=True)
    # BFE moved the file in Sep 2026; the old URL still answers but stopped at 28 Sep 2026
    r = get("https://www.bfe-ogd.ch/ogd17/ogd17_fuellungsgrad_speicherseen.csv")
    last = r.text.strip().splitlines()[-1].split(",")[0]
    print("bfe reservoirs: last week", last)
    if "TotalCH_speicherinhalt_gwh" not in r.text[:400]:
        raise RuntimeError("bfe reservoirs: unexpected file")
    (d / "reservoirs.csv").write_bytes(r.content)
    print("bfe reservoirs", f"{len(r.content) / 1e3:.0f} kB")


if __name__ == "__main__":
    jobs = {"futures": futures, "spot": spot, "bfe": bfe}
    todo = sys.argv[1:] or list(jobs)
    for t in todo:
        if t not in jobs:
            sys.exit(f"unknown target {t!r}; choose from {list(jobs)}")
        print(f"== {t} =="); jobs[t]()
    print("done; data in", ROOT)
