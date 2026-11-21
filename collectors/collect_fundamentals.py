#!/usr/bin/env python3
"""Hourly generation and load (Energy-Charts /public_power) for DE, CH, FR, AT, IT and installed capacity,
into cache/fundamentals/ (not committed: Energy-Charts keeps the history; past years are cached by the
workflow). Only the current year is refetched once the past years are present.

  python collect_fundamentals.py [--first-year 2021] [--out cache/fundamentals] [--csv-dir data/energy_charts/fundamentals]

--csv-dir also writes compact hourly files that are committed (spot-ensemble reads them):
  gen_<country>_<year>.csv.gz  (ts_utc, one column per Energy-Charts production type and load, MW, hourly mean)
  installed_<country>.json     (installed capacity; DE monthly, others yearly)
"""
import argparse
import json
import time

import pandas as pd
from datetime import date, timedelta
from pathlib import Path

from collect_energy_charts import get

API = "https://api.energy-charts.info"
COUNTRIES = ["de", "ch", "fr", "at", "it"]


def write_csv(j, path):
    """Energy-Charts public_power JSON -> hourly CSV (UTC interval start, MW)."""
    idx = pd.to_datetime(j["unix_seconds"], unit="s", utc=True)
    df = pd.DataFrame({p["name"]: p["data"] for p in j["production_types"]}, index=idx, dtype=float)
    df = df.resample("1h").mean()
    df.index.name = "ts_utc"
    df.round(1).to_csv(path, compression={"method": "gzip", "compresslevel": 9, "mtime": 0})
    print("  ->", path, df.shape, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--first-year", type=int, default=2021)
    ap.add_argument("--out", default="cache/fundamentals")
    ap.add_argument("--csv-dir", default=None)
    a = ap.parse_args()
    csv = Path(a.csv_dir) if a.csv_dir else None
    if csv:
        csv.mkdir(parents=True, exist_ok=True)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    this = date.today().year
    for c in COUNTRIES:
        for y in range(a.first_year, this + 1):
            f = out / f"ec_public_power_{c}_{y}.json"
            g = csv / f"gen_{c}_{y}.csv.gz" if csv else None
            if y < this and (g.exists() if g else f.exists()):
                continue
            if y < this and f.exists():
                j = json.loads(f.read_text(encoding="utf-8"))
            else:
                end = f"{y}-12-31" if y < this else (date.today() + timedelta(days=1)).isoformat()
                j = get(f"{API}/public_power?country={c}&start={y}-01-01&end={end}").json()
                f.write_text(json.dumps({"unix_seconds": j["unix_seconds"], "production_types": j["production_types"]}),
                             encoding="utf-8")
                print("public_power", c, y, len(j["unix_seconds"]), "points"); time.sleep(2)
            if g:
                write_csv(j, g)
    j = get(f"{API}/installed_power?country=de&time_step=monthly").json()
    (out / "ec_installed_de.json").write_text(json.dumps(j), encoding="utf-8")
    if csv:
        (csv / "installed_de.json").write_text(json.dumps(j), encoding="utf-8")
    for c in ["at", "fr", "it", "ch"]:
        try:
            j = get(f"{API}/installed_power?country={c}&time_step=yearly").json()
        except Exception as e:
            print("installed", c, "failed:", e); continue
        (out / f"ec_installed_yearly_{c}.json").write_text(json.dumps(j), encoding="utf-8")
        if csv:
            (csv / f"installed_{c}.json").write_text(json.dumps(j), encoding="utf-8")
        time.sleep(1)
    print("installed capacity: de (monthly), at/fr/it (yearly)")


if __name__ == "__main__":
    main()
