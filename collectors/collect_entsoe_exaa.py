#!/usr/bin/env python3
"""EXAA day-ahead auction prices (10:15 CET auction, ENTSO-E sequence 2) for AT and DE_LU, native resolution.

The EXAA auction clears before the Swiss day-ahead gate (11:00), so its prices for day D are known at a ~10:30 issue
on D−1 (spot-ensemble: candidate issue time). ENTSO-E documentType A44 with
classificationSequence_AttributeInstanceComponent.position=2 (sequence 1 = SDAC).
Writes data/entsoe/exaa_<zone>.csv: ts_utc (interval start), resolution_min, price_eur_mwh. Months already stored
completely are skipped; the last 45 days are refreshed every run. Logs show counts and dates only.

    python collect_entsoe_exaa.py                  # from 2024-01 to tomorrow
    python collect_entsoe_exaa.py 2025-10          # from that month
"""
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

import entsoe_api
from entsoe_api import EIC, children, find

DATA_HOME = Path(os.environ.get("DATA_ROOT", Path(__file__).resolve().parent))
OUT = DATA_HOME / "data" / "entsoe"
ZONES = ["AT", "DE_LU"]


def parse_prices(xml_bytes):
    rows = []
    if not xml_bytes:
        return rows
    root = ET.fromstring(xml_bytes)
    for ts in children(root, "TimeSeries"):
        for per in children(ts, "Period"):
            start = pd.Timestamp(find(per, "start").text)
            res = int(find(per, "resolution").text[2:-1])
            pts = {int(find(p, "position").text): float(find(p, "price.amount").text) for p in children(per, "Point")}
            end = pd.Timestamp(find(per, "end").text)
            n = int((end - start) / pd.Timedelta(minutes=res))
            last = None
            for i in range(1, n + 1):                      # A03 curve: a missing position repeats the previous price
                last = pts.get(i, last)
                if last is not None:
                    rows.append((start + (i - 1) * pd.Timedelta(minutes=res), res, last))
    return rows


def fetch_month(token, zone, m0):
    m1 = m0 + pd.offsets.MonthBegin(1)
    p = {"documentType": "A44", "in_Domain": EIC[zone], "out_Domain": EIC[zone],
         "contract_MarketAgreement.type": "A01", "classificationSequence_AttributeInstanceComponent.position": "2",
         "periodStart": m0.strftime("%Y%m%d0000"), "periodEnd": m1.strftime("%Y%m%d0000")}
    return parse_prices(entsoe_api.fetch(token, p))


def main():
    token = os.environ.get("ENTSOE_TOKEN", "").strip()
    if not token:
        print("ENTSOE_TOKEN missing: skipped"); return
    first = pd.Timestamp(sys.argv[1] + "-01" if len(sys.argv) > 1 else "2024-01-01")
    today = pd.Timestamp.utcnow().tz_localize(None).normalize()
    OUT.mkdir(parents=True, exist_ok=True)
    for zone in ZONES:
        f = OUT / f"exaa_{zone}.csv"
        old = pd.read_csv(f, parse_dates=["ts_utc"]) if f.exists() else pd.DataFrame(columns=["ts_utc", "resolution_min", "price_eur_mwh"])
        have = set(pd.to_datetime(old.ts_utc).dt.strftime("%Y-%m")) if len(old) else set()
        new = []
        for m0 in pd.date_range(first, today + pd.Timedelta(days=1), freq="MS"):
            recent = m0 >= (today - pd.Timedelta(days=45)).replace(day=1)
            if m0.strftime("%Y-%m") in have and not recent:
                continue
            try:
                r = fetch_month(token, zone, m0)
            except Exception as e:                         # entsoe_api scrubs the token from messages
                print(f"{zone} {m0:%Y-%m}: failed ({str(e)[:120]})"); continue
            print(f"{zone} {m0:%Y-%m}: {len(r)} prices")
            new += r
        if new:
            n = pd.DataFrame(new, columns=["ts_utc", "resolution_min", "price_eur_mwh"])
            n["ts_utc"] = pd.to_datetime(n.ts_utc, utc=True).dt.tz_localize(None)
            old["ts_utc"] = pd.to_datetime(old.ts_utc)
            allr = pd.concat([old, n]).drop_duplicates(["ts_utc", "resolution_min"], keep="last").sort_values("ts_utc")
            allr.to_csv(f, index=False, date_format="%Y-%m-%dT%H:%M")
            print(f"{zone}: {len(allr)} rows, {allr.ts_utc.min():%Y-%m-%d} .. {allr.ts_utc.max():%Y-%m-%d}")


if __name__ == "__main__":
    main()
