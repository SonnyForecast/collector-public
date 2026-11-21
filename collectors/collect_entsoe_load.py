#!/usr/bin/env python3
"""ENTSO-E total load: day-ahead forecast and actual, hourly, for CH and its neighbours (spot-ensemble inputs).

ENTSO-E Transparency Platform REST API (free token, repository secret ENTSOE_TOKEN; without it the script skips):
  documentType A65 (system total load), processType A01 = day-ahead forecast, A16 = realised (actual)
Writes data/entsoe/load_<zone>_<year>.csv: ts_utc (interval start), forecast_mw, actual_mw (hourly means; quarter-hour
zones averaged). Past years are fetched once; the current and previous year are refreshed every run.

Timing for the 07:00 Swiss issue on D−1: the day-ahead load forecast for D is published by ENTSO-E rules before the
day-ahead gate (~10:00 on D−1 at the latest, often D−2), so a model must still treat it as known only if a vintage
check confirms it; actual load of D−2 and earlier hours of D−1 is published within about an hour.

    python collect_entsoe_load.py                 # 2023 .. current year
    python collect_entsoe_load.py 2024 2025       # given years
"""
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import entsoe_api

import os as _os
# Data folder: the checked-out private data repo (env DATA_ROOT); default = next to this script
DATA_HOME = Path(_os.environ.get("DATA_ROOT", Path(__file__).resolve().parent))

OUT = DATA_HOME / "data" / "entsoe"
FIRST_YEAR = 2023
ZONES = {"CH": "10YCH-SWISSGRIDZ", "DE_LU": "10Y1001A1001A82H", "FR": "10YFR-RTE------C",
         "IT_NORTH": "10Y1001A1001A73I"}
PROCESS = {"forecast_mw": "A01", "actual_mw": "A16"}


def fetch(token, eic, process, start, end):
    return entsoe_api.fetch(token, {"documentType": "A65", "processType": process, "outBiddingZone_Domain": eic,
                                    "periodStart": start.strftime("%Y%m%d%H%M"), "periodEnd": end.strftime("%Y%m%d%H%M")})


def parse(xml_bytes) -> pd.Series:
    return entsoe_api.parse_series(xml_bytes)


def year(token, zone, eic, y):
    # the API allows at most one year per request: [Jan 1 00:00 UTC, Jan 1 00:00 UTC next year)
    start = datetime(y, 1, 1, tzinfo=timezone.utc)
    end = min(datetime(y + 1, 1, 1, tzinfo=timezone.utc),
              datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=2))
    cols = {}
    for col, proc in PROCESS.items():
        parts = []
        for m in (1, 4, 7, 10):          # quarters: whole-year requests time out on the server (FR 2025, error 599)
            q0 = datetime(y, m, 1, tzinfo=timezone.utc)
            q1 = min(end, datetime(y + (m == 10), 1 if m == 10 else m + 3, 1, tzinfo=timezone.utc))
            if q0 >= q1:
                break
            try:
                parts.append(parse(fetch(token, eic, proc, q0, q1)))
            except Exception as e:  # keep the other quarters and series; the merge below keeps older values
                print("  FAILED", zone, y, col, q0.date(), e)
            time.sleep(1)
        parts = [x for x in parts if len(x)]
        cols[col] = pd.concat(parts).sort_index() if parts else pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
        cols[col] = cols[col][~cols[col].index.duplicated(keep="last")]
    df = pd.DataFrame(cols)
    if df.empty:
        return df
    df.index = pd.DatetimeIndex(df.index)
    df = df[(df.index >= pd.Timestamp(f"{y}-01-01", tz="UTC")) & (df.index < pd.Timestamp(f"{y + 1}-01-01", tz="UTC"))]
    df.index.name = "ts_utc"
    return df.round(1)


def main(years):
    token = os.environ.get("ENTSOE_TOKEN")
    if not token:
        print("SKIPPED: set the ENTSOE_TOKEN secret (free API token from the ENTSO-E Transparency Platform)")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    this = date.today().year
    for y in years:
        for zone, eic in ZONES.items():
            f = OUT / f"load_{zone}_{y}.csv"
            if f.exists() and y < this - 1:
                continue
            df = year(token, zone, eic, y)
            if f.exists():                     # never lose values a failed request did not return
                prev = pd.read_csv(f, index_col="ts_utc", parse_dates=["ts_utc"])
                df = df.reindex(columns=list(PROCESS)).combine_first(prev) if len(df) else prev
                df.index.name = "ts_utc"
            if len(df):
                df.to_csv(f)
            print("load", zone, y, len(df), "hours,", df.notna().sum().to_dict())
    return 0


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:]]
    sys.exit(main(args or list(range(FIRST_YEAR, date.today().year + 1))))
