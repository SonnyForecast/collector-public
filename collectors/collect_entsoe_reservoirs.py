#!/usr/bin/env python3
"""ENTSO-E hydro reservoir storage (documentType A72, processType A16, weekly) for FR, AT, IT_NORTH and CH.

Writes data/entsoe/reservoirs_<zone>.csv: week_start_utc (start of the reference week), stored_energy_mwh.
ENTSO-E publishes each week within about three working days after it ends, so a model may use a week only from
week_start + 10 days (conservative). The CH series changes definition around the end of 2024 (level jump); use the
BFE data for CH. Full history once, then the last 120 days every run. Logs show counts and dates only.

    python collect_entsoe_reservoirs.py
"""
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

import entsoe_api
from entsoe_api import EIC, children, find

DATA_HOME = Path(os.environ.get("DATA_ROOT", Path(__file__).resolve().parent))
OUT = DATA_HOME / "data" / "entsoe"
ZONES = ["FR", "AT", "IT_NORTH", "CH"]
FIRST = pd.Timestamp("2015-01-01")


def parse(xml_bytes):
    rows = []
    if not xml_bytes:
        return rows
    for ts in children(ET.fromstring(xml_bytes), "TimeSeries"):
        for per in children(ts, "Period"):
            start = pd.Timestamp(find(per, "start").text)
            for p in children(per, "Point"):
                pos = int(find(p, "position").text)
                rows.append((start + pd.Timedelta(days=7 * (pos - 1)), float(find(p, "quantity").text)))
    return rows


def main():
    token = os.environ.get("ENTSOE_TOKEN", "").strip()
    if not token:
        print("ENTSOE_TOKEN missing: skipped"); return
    OUT.mkdir(parents=True, exist_ok=True)
    today = pd.Timestamp.utcnow().tz_localize(None).normalize()
    for zone in ZONES:
        f = OUT / f"reservoirs_{zone}.csv"
        old = pd.read_csv(f, parse_dates=["week_start_utc"]) if f.exists() else None
        start = (today - pd.Timedelta(days=120)) if old is not None and len(old) else FIRST
        rows = []
        for y0 in pd.date_range(start.replace(month=1, day=1), today, freq="YS"):   # one request per year
            s, e = max(y0, start), min(y0 + pd.offsets.YearBegin(1), today + pd.Timedelta(days=1))
            try:
                r = parse(entsoe_api.fetch(token, {"documentType": "A72", "processType": "A16", "in_Domain": EIC[zone],
                                                   "periodStart": s.strftime("%Y%m%d0000"), "periodEnd": e.strftime("%Y%m%d0000")}))
            except Exception as ex:
                print(f"{zone} {y0:%Y}: failed ({str(ex)[:120]})"); continue
            rows += r
        n = pd.DataFrame(rows, columns=["week_start_utc", "stored_energy_mwh"])
        n["week_start_utc"] = pd.to_datetime(n.week_start_utc, utc=True).dt.tz_localize(None)
        allr = pd.concat([old, n]) if old is not None else n
        allr = allr.drop_duplicates("week_start_utc", keep="last").sort_values("week_start_utc")
        allr.to_csv(f, index=False, date_format="%Y-%m-%dT%H:%M")
        print(f"{zone}: {len(allr)} weeks, {allr.week_start_utc.min():%Y-%m-%d} .. {allr.week_start_utc.max():%Y-%m-%d}")


if __name__ == "__main__":
    main()
