#!/usr/bin/env python3
"""ENTSO-E market inputs for the Swiss spot forecast (spot-ensemble): outages, cross-border flows, day-ahead NTC.

  outages   Unavailability of generation units (A80) and production units (A77) in CH, FR, DE_LU, IT_NORTH.
            One row per (document mRID, revision, unit, available-capacity block):
            created_utc (publication time of that revision: use only rows created before the issue time),
            status (A05 active, A09 cancelled, A13 withdrawn), business_type (A53 planned, A54 forced),
            unit_eic, unit_name, psr (production type, B14 = nuclear), nominal_mw, start_utc, end_utc, available_mw.
            The API returns the latest revision of each outage only; every run adds the revisions published since
            the last run, so from now on all revisions are kept (point-in-time archive). For the history a backtest
            must drop outages whose (latest) revision was created after the issue time.
            -> data/entsoe/outages_<zone>.csv.gz
  revisions Older revisions of the outages that matter for a backtest (the plain query returns only the latest one;
            "Older versions of an outage is returned only when mRID parameter is used", ENTSO-E API guide): outages
            with more than one revision whose latest revision was published after they started, lasting >= 1 day,
            >= 300 MW, ending after June 2024. One request per outage; resumable (outages whose revisions are all in
            the archive are skipped), stops after REV_MAX_MIN minutes (default 100) so the run can commit.
  flows     Physical flows (A11), hourly MW, CH <-> DE_LU, FR, IT_NORTH, AT (both directions); realised values,
            usable with a lag (published about an hour after real time) -> data/entsoe/flows_ch_<year>.csv
  ntc       Day-ahead forecasted transfer capacities (A61, contract A01), same borders; check the publication time
            against the 07:00 / 10:00 issue before using them -> data/entsoe/ntc_ch_<year>.csv

Past years are fetched once; the current year (outages: last 120 days and the year ahead) every run.

    python collect_entsoe_market.py                    # everything, 2023 .. now
    python collect_entsoe_market.py outages flows      # some parts
    python collect_entsoe_market.py --first-year 2024
    python collect_entsoe_market.py outages --zone FR   # one zone
"""
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import entsoe_api
from entsoe_api import EIC, children, find, local

import os as _os
# Data folder: the checked-out private data repo (env DATA_ROOT); default = next to this script
DATA_HOME = Path(_os.environ.get("DATA_ROOT", Path(__file__).resolve().parent))

OUT = DATA_HOME / "data" / "entsoe"
FIRST_YEAR = 2023
OUTAGE_ZONES = ["CH", "FR"]               # DE_LU, IT_NORTH: too many documents for this approach (later, filtered)
OUTAGE_DOCS = {"A80": "generation_unit", "A77": "production_unit"}
BORDERS = ["DE_LU", "FR", "IT_NORTH", "AT"]
PAGE = 200                     # documents per answer; the API accepts offsets up to 4800
UTC = timezone.utc


def ymdhm(t):
    return t.strftime("%Y%m%d%H%M")


# ---------------------------------------------------------------- outages
def parse_outage(xml_bytes, zone, doc):
    root = ET.fromstring(xml_bytes)
    head = {local(x.tag): x.text for x in root if len(x) == 0}
    st = find(root, "docStatus")
    status = find(st, "value").text if st is not None else "A05"
    rows = []
    for ts in children(root, "TimeSeries"):
        g = {local(x.tag): x.text for x in ts.iter() if len(x) == 0 and x.text and x.text.strip()}
        nominal = next((v for k, v in g.items() if k.endswith("nominalP")), None)
        for per in [x for x in ts.iter() if local(x.tag) == "Available_Period"]:
            p0 = pd.Timestamp(find(per, "start").text); p1 = pd.Timestamp(find(per, "end").text)
            res = pd.Timedelta(find(per, "resolution").text)        # ISO 8601: PT1M, PT60M, P1D
            pts = sorted((int(find(p, "position").text), float(find(p, "quantity").text))
                         for p in children(per, "Point"))
            for k, (pos, q) in enumerate(pts):      # curve type A03: a point holds until the next one
                s = p0 + (pos - 1) * res
                e = p0 + (pts[k + 1][0] - 1) * res if k + 1 < len(pts) else p1
                rows.append(dict(zone=zone, doc=doc, mrid=head.get("mRID"), revision=int(head.get("revisionNumber", 1)),
                                 created_utc=head.get("createdDateTime"), status=status,
                                 business_type=g.get("businessType"),
                                 unit_eic=g.get("production_RegisteredResource.mRID") or g.get("mRID"),
                                 unit_name=g.get("production_RegisteredResource.name") or g.get("name"),
                                 psr=next((v for k, v in g.items() if k.endswith("psrType")), None), nominal_mw=float(nominal) if nominal else None,
                                 start_utc=s, end_utc=min(e, p1), available_mw=q))
    return rows


def resource_fields(ts):
    """Names inside production_RegisteredResource are not unique (mRID, name): read them explicitly."""
    out = {}
    for x in ts:
        if local(x.tag) == "production_RegisteredResource":
            for y in x:
                if local(y.tag) in ("mRID", "name"):
                    out[f"production_RegisteredResource.{local(y.tag)}"] = y.text
    return out


def outage_window(token, zone, doc, t0, t1, depth=0):
    rows, offset = [], 0
    while True:
        params = {"documentType": doc, "BiddingZone_Domain": EIC[zone], "periodStart": ymdhm(t0),
                  "periodEnd": ymdhm(t1), "offset": offset}
        docs = entsoe_api.fetch(token, params)
        if not docs:
            break
        if not isinstance(docs, list):
            docs = [docs]
        for d in docs:
            r = parse_outage_full(d, zone, OUTAGE_DOCS[doc])
            rows += r
        if len(docs) < PAGE:
            break
        offset += PAGE
        if offset % 1000 == 0:
            print(f"    {zone} {doc} {t0:%Y-%m-%d}: {offset} documents so far", flush=True)
        if offset > 4800:                     # too many documents: split the window
            if depth > 3 or t1 - t0 < timedelta(days=4):
                print(f"  WARNING {zone} {doc} {t0:%Y-%m-%d}: more than 5000 documents, some missing", flush=True)
                break
            mid = t0 + (t1 - t0) / 2
            return outage_window(token, zone, doc, t0, mid, depth + 1) + outage_window(token, zone, doc, mid, t1, depth + 1)
        time.sleep(0.5)
    return rows


def parse_outage_full(xml_bytes, zone, doc):
    rows = parse_outage(xml_bytes, zone, doc)
    if rows:
        root = ET.fromstring(xml_bytes)
        ts = children(root, "TimeSeries")
        if ts:
            rf = resource_fields(ts[0])
            for r in rows:
                r["unit_eic"] = rf.get("production_RegisteredResource.mRID", r["unit_eic"])
                r["unit_name"] = rf.get("production_RegisteredResource.name", r["unit_name"])
    return rows


KEY = ["mrid", "revision", "unit_eic", "start_utc", "available_mw"]


def outages(token, first_year, full=False, zones=None):
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    for zone in zones or OUTAGE_ZONES:
        f = OUT / f"outages_{zone}.csv.gz"
        old = pd.read_csv(f) if f.exists() and not full else None
        if old is not None and old.psr.isna().all():      # written before the psrType fix: fetch the history again
            print(f"outages {zone}: no production types in the archive, refetching from {first_year}", flush=True)
            old = None
        start = datetime(first_year, 1, 1, tzinfo=UTC) if old is None else now - timedelta(days=120)
        end = now + timedelta(days=366)
        rows, t0 = [], start
        while t0 < end:                       # windows up to a year (API limit); split when > 5000 documents
            t1 = min(end, t0 + timedelta(days=365))
            for doc in OUTAGE_DOCS:
                try:
                    tw = time.time(); n0 = len(rows)
                    rows += outage_window(token, zone, doc, t0, t1)
                    print(f"  {zone} {doc} {t0:%Y-%m-%d}..{t1:%Y-%m-%d}: {len(rows) - n0} rows, {time.time() - tw:.0f} s", flush=True)
                except Exception as e:
                    print("  FAILED outages", zone, doc, t0.date(), e, flush=True)
                time.sleep(0.5)
            t0 = t1
        new = pd.DataFrame(rows)
        if new.empty and old is None:
            print("outages", zone, "nothing returned"); continue
        if len(new):
            new["start_utc"] = pd.to_datetime(new.start_utc, utc=True).dt.strftime("%Y-%m-%d %H:%M")
            new["end_utc"] = pd.to_datetime(new.end_utc, utc=True).dt.strftime("%Y-%m-%d %H:%M")
        df = new if old is None else pd.concat([old, new], ignore_index=True)
        df = df.drop_duplicates(KEY, keep="last").sort_values(["created_utc", "mrid", "revision", "start_utc"])
        df.to_csv(f, index=False, compression={"method": "gzip", "compresslevel": 9, "mtime": 0})
        multi = df.groupby("mrid").revision.nunique().gt(1).sum()
        print(f"outages {zone}: {len(df)} rows, {df.mrid.nunique()} outages, {multi} with >1 revision kept, "
              f"nuclear rows {int((df.psr == 'B14').sum())}, created {df.created_utc.min()} .. {df.created_utc.max()}",
              flush=True)


def revisions(token, zones=None, max_min=None):
    max_min = float(os.environ.get("REV_MAX_MIN", 100)) if max_min is None else max_min
    t0 = time.time()
    for zone in zones or OUTAGE_ZONES:
        f = OUT / f"outages_{zone}.csv.gz"
        if not f.exists():
            continue
        o = pd.read_csv(f)
        c = pd.to_datetime(o.created_utc, utc=True); st = pd.to_datetime(o.start_utc, utc=True)
        en = pd.to_datetime(o.end_utc, utc=True)
        g = pd.DataFrame({"mrid": o.mrid, "doc": o.doc, "rev": o.revision, "created": c, "start": st, "end": en,
                          "nom": o.nominal_mw}).groupby("mrid").agg(
            doc=("doc", "first"), rev=("rev", "max"), nrev=("rev", "nunique"), created=("created", "max"),
            start=("start", "min"), end=("end", "max"), nom=("nom", "max"))
        g["dur"] = (g.end - g.start).dt.total_seconds() / 86400
        todo = g[(g.rev > 1) & (g.nrev < g.rev) & (g.created > g.start) & (g.dur >= 1) &
                 (g.end > pd.Timestamp("2024-06-01", tz="UTC")) & (g.nom >= 300)]
        print(f"revisions {zone}: {len(todo)} outages to complete", flush=True)
        doc_code = {v: k for k, v in OUTAGE_DOCS.items()}
        rows, done = [], 0
        for mrid, r in todo.iterrows():
            if time.time() - t0 > max_min * 60:
                print(f"  time limit of {max_min} min reached; the rest next run", flush=True); break
            p0 = r.start - timedelta(days=1)
            p1 = min(r.end + timedelta(days=1), p0 + timedelta(days=365))
            params = {"documentType": doc_code[r.doc], "BiddingZone_Domain": EIC[zone], "mRID": mrid,
                      "periodStart": ymdhm(p0), "periodEnd": ymdhm(p1)}
            try:
                off = 0
                while True:
                    docs = entsoe_api.fetch(token, dict(params, offset=off))
                    if not docs:
                        break
                    docs = docs if isinstance(docs, list) else [docs]
                    for d in docs:
                        rows += parse_outage_full(d, zone, r.doc)
                    if len(docs) < PAGE:
                        break
                    off += PAGE
            except Exception as e:
                print("  FAILED", zone, mrid, e, flush=True)
            done += 1
            if done % 100 == 0:
                print(f"  {zone}: {done}/{len(todo)} outages, {len(rows)} rows, {time.time() - t0:.0f} s", flush=True)
            time.sleep(0.3)
        if rows:
            new = pd.DataFrame(rows)
            new["start_utc"] = pd.to_datetime(new.start_utc, utc=True).dt.strftime("%Y-%m-%d %H:%M")
            new["end_utc"] = pd.to_datetime(new.end_utc, utc=True).dt.strftime("%Y-%m-%d %H:%M")
            df = pd.concat([o, new], ignore_index=True).drop_duplicates(KEY, keep="last")
            df = df.sort_values(["created_utc", "mrid", "revision", "start_utc"])
            df.to_csv(f, index=False, compression={"method": "gzip", "compresslevel": 9, "mtime": 0})
            print(f"revisions {zone}: +{len(df) - len(o)} rows from {done} outages; "
                  f"{int(df.groupby('mrid').revision.nunique().gt(1).sum())} outages with >1 revision", flush=True)
        if time.time() - t0 > max_min * 60:
            break


# ---------------------------------------------------------------- flows and NTC
def border_series(token, kind, a, b, y0, y1):
    """kind: flows (A11) or ntc (A61 day-ahead). From a to b, quarterly requests."""
    parts, t0 = [], y0
    while t0 < y1:
        t1 = min(y1, (t0.replace(day=1) + timedelta(days=95)).replace(day=1))
        p = {"in_Domain": EIC[b], "out_Domain": EIC[a], "periodStart": ymdhm(t0), "periodEnd": ymdhm(t1)}
        p.update({"documentType": "A11"} if kind == "flows" else
                 {"documentType": "A61", "contract_MarketAgreement.Type": "A01"})
        try:
            parts.append(entsoe_api.parse_series(entsoe_api.fetch(token, p)))
        except Exception as e:
            print(f"  FAILED {kind} {a}>{b} {t0.date()}: {e}", flush=True)
        time.sleep(0.5); t0 = t1
    parts = [x for x in parts if len(x)]
    s = pd.concat(parts).sort_index() if parts else pd.Series(dtype=float)
    return s[~s.index.duplicated(keep="last")]


def borders(token, kind, first_year):
    this = date.today().year
    for y in range(first_year, this + 1):
        f = OUT / f"{kind}_ch_{y}.csv"
        if f.exists() and y < this:          # past years once (minutes are limited); the current year every run
            continue
        y0 = datetime(y, 1, 1, tzinfo=UTC)
        y1 = min(datetime(y + 1, 1, 1, tzinfo=UTC), datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
                 + timedelta(days=2))
        cols = {}
        for nb in BORDERS:
            for a, b in (("CH", nb), (nb, "CH")):
                cols[f"{a}>{b}"] = border_series(token, kind, a, b, y0, y1)
        df = pd.DataFrame(cols)
        if f.exists():
            prev = pd.read_csv(f, index_col="ts_utc", parse_dates=["ts_utc"])
            df = df.combine_first(prev) if len(df) else prev
        if len(df):
            df.index.name = "ts_utc"
            df.round(1).to_csv(f)
        print(kind, y, len(df), "hours,", df.notna().sum().to_dict(), flush=True)


def main(argv):
    token = os.environ.get("ENTSOE_TOKEN")
    if not token:
        print("SKIPPED: set the ENTSOE_TOKEN secret"); return 0
    first = int(argv[argv.index("--first-year") + 1]) if "--first-year" in argv else FIRST_YEAR
    parts = [a for a in argv if a in ("outages", "revisions", "flows", "ntc")] or ["flows", "ntc", "outages", "revisions"]
    OUT.mkdir(parents=True, exist_ok=True)
    for p in parts:
        t = time.time()
        zones = argv[argv.index("--zone") + 1].split(",") if "--zone" in argv else None
        if p == "outages":
            outages(token, first, "--full" in argv, zones)
        elif p == "revisions":
            revisions(token, zones)
        else:
            borders(token, p, first)
        print(f"{p}: {time.time() - t:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
