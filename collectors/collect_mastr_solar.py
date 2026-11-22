#!/usr/bin/env python3
"""German PV fleet from the Marktstammdatenregister (MaStR, Bundesnetzagentur), aggregated to monthly installed
capacity by the attributes that decide whether a unit keeps producing at negative prices.

Source: MaStR daily bulk export via open-mastr (https://github.com/OpenEnergyPlatform/open-MaStR).
Licence: Marktstammdatenregister - © Bundesnetzagentur für Elektrizität, Gas, Telekommunikation, Post und Eisenbahnen |
dl-de/by-2-0 (www.govdata.de/dl-de/by-2-0), https://www.marktstammdatenregister.de/MaStR/Datendownload.
The raw export (several GB) stays on the runner; only the aggregate is written:

    data/mastr/solar_capacity_monthly.csv
      month          end of month (YYYY-MM)
      size           kW class of Nettonennleistung: le2 (plug-in), 2_30, 30_100, 100_1000, gt1000
      dv             remote control by a direct marketer (FernsteuerbarkeitDv): 1 / 0 / na
      nb             remote control by the grid operator (FernsteuerbarkeitNb): 1 / 0 / na
      feed           Einspeisungsart: full / partial / none / na
      vintage        commissioning: lt2016, 2016_2020, 2021_2022, 2023_20250224, ge20250225 (Solarspitzengesetz:
                     no payment at negative prices for new units)
      limit          Leistungsbegrenzung (feed-in cap) present: 1 / 0
      mw, units      stock in operation at month end, by commissioning date (decommissioned units removed)
      mw_known       same, but counting a unit only from its registration date (point-in-time: late registrations
                     were not known earlier)
    data/mastr/solar_meta.json   export date, columns found, row counts

    python collect_mastr_solar.py            # monthly 2019-01 .. current month
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_HOME = Path(os.environ.get("DATA_ROOT", Path(__file__).resolve().parent))
OUT = DATA_HOME / "data" / "mastr"
FIRST = "2019-01"


def load_units():
    from open_mastr import Mastr
    import time
    db = Mastr()
    for i in range(4):                              # the server sometimes drops the first connection
        try:
            db.download(method="bulk", data=["solar"])
            break
        except Exception as e:
            print(f"download attempt {i + 1} failed: {repr(e)[:200]}", flush=True)
            if i == 3:
                raise
            time.sleep(120 * (i + 1))
    cols = pd.read_sql("SELECT * FROM solar_extended LIMIT 1", db.engine).columns.tolist()
    want = ["Nettonennleistung", "Bruttoleistung", "Inbetriebnahmedatum", "DatumEndgueltigeStilllegung",
            "Registrierungsdatum", "EinheitBetriebsstatus", "FernsteuerbarkeitDv", "FernsteuerbarkeitNb",
            "Einspeisungsart", "Leistungsbegrenzung", "Land"]
    use = [c for c in want if c in cols]
    print("columns used:", use, "| missing:", [c for c in want if c not in cols], flush=True)
    df = pd.read_sql(f"SELECT {', '.join(use)} FROM solar_extended", db.engine)
    return df, cols


def yesno(s):
    s = s.astype(str).str.lower()
    return np.where(s.isin(["1", "true", "ja", "yes"]), "1", np.where(s.isin(["0", "false", "nein", "no"]), "0", "na"))


def classify(df):
    kw = pd.to_numeric(df.get("Nettonennleistung"), errors="coerce").fillna(pd.to_numeric(df.get("Bruttoleistung"), errors="coerce"))
    out = pd.DataFrame({"kw": kw})
    out["size"] = pd.cut(kw, [-np.inf, 2, 30, 100, 1000, np.inf], labels=["le2", "2_30", "30_100", "100_1000", "gt1000"]).astype(str)
    out["dv"] = yesno(df["FernsteuerbarkeitDv"]) if "FernsteuerbarkeitDv" in df else "na"
    out["nb"] = yesno(df["FernsteuerbarkeitNb"]) if "FernsteuerbarkeitNb" in df else "na"
    f = df["Einspeisungsart"].astype(str).str.lower() if "Einspeisungsart" in df else pd.Series("na", index=df.index)
    out["feed"] = np.select([f.str.contains("voll"), f.str.contains("teil"), f.str.contains("keine")], ["full", "partial", "none"], "na")
    lim = df["Leistungsbegrenzung"].astype(str).str.lower() if "Leistungsbegrenzung" in df else pd.Series("", index=df.index)
    out["limit"] = np.where(lim.str.contains("%|prozent|begrenz") & ~lim.str.contains("nein|keine"), "1", "0")
    c = pd.to_datetime(df["Inbetriebnahmedatum"], errors="coerce")
    out["comm"] = c
    out["vintage"] = np.select([c < "2016-01-01", c < "2021-01-01", c < "2023-01-01", c < "2025-02-25"],
                               ["lt2016", "2016_2020", "2021_2022", "2023_20250224"], "ge20250225")
    out["end"] = pd.to_datetime(df.get("DatumEndgueltigeStilllegung"), errors="coerce")
    reg = pd.to_datetime(df.get("Registrierungsdatum"), errors="coerce")
    out["known"] = pd.concat([c, reg], axis=1).max(axis=1)
    return out[out.comm.notna() & out.kw.notna() & (out.kw > 0)]


def monthly(u):
    months = pd.period_range(FIRST, pd.Timestamp.today().to_period("M"), freq="M")
    keys = ["size", "dv", "nb", "feed", "vintage", "limit"]
    rows = []
    for m in months:
        me = m.to_timestamp(how="end")
        alive = u.end.isna() | (u.end > me)
        a = u[(u.comm <= me) & alive]
        g = a.groupby(keys).agg(mw=("kw", lambda x: x.sum() / 1000), units=("kw", "size"))
        k = u[(u.known <= me) & alive].groupby(keys).kw.sum() / 1000
        g["mw_known"] = k.reindex(g.index).fillna(0)
        g = g.reset_index(); g.insert(0, "month", str(m))
        rows.append(g)
    return pd.concat(rows)


def main():
    t0 = pd.Timestamp.now()
    df, cols = load_units()
    print(f"units read: {len(df)}", flush=True)
    u = classify(df)
    agg = monthly(u)
    OUT.mkdir(parents=True, exist_ok=True)
    agg.round(3).to_csv(OUT / "solar_capacity_monthly.csv", index=False)
    last = agg[agg.month == agg.month.max()]
    meta = {"export_date": pd.Timestamp.today().strftime("%Y-%m-%d"), "units_read": int(len(df)),
            "units_used": int(len(u)), "columns": cols,
            "licence": "Marktstammdatenregister - © Bundesnetzagentur | dl-de/by-2-0",
            "total_gw_last_month": round(float(last.mw.sum()) / 1000, 2),
            "gw_last_month_by_dv": (last.groupby("dv").mw.sum() / 1000).round(2).to_dict(),
            "gw_last_month_by_vintage": (last.groupby("vintage").mw.sum() / 1000).round(2).to_dict(),
            "gw_last_month_by_size": (last.groupby("size").mw.sum() / 1000).round(2).to_dict()}
    (OUT / "solar_meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in meta.items() if k != "columns"}, ensure_ascii=False))
    print(f"{len(agg)} rows -> {OUT / 'solar_capacity_monthly.csv'} ({(pd.Timestamp.now() - t0).seconds}s)")


if __name__ == "__main__":
    sys.exit(main())
