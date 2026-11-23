#!/usr/bin/env python3
"""ECMWF IFS ENS forecast history from dynamical.org ("ecmwf-ifs-ens-forecast-15-day-0-25-degree": 00 UTC inits from
15 Oct 2020, 51 members, 0.25°, 3-hourly to 144 h). Attribution (CC BY 4.0 + ECMWF Terms of Use): "ECMWF IFS ENS
forecast data processed by dynamical.org from ECMWF Open Data".

Point-in-time weather for training before the Open-Meteo lead-time archive (2024) and the forecast spread. Per 00 UTC
run, leads 0-96 h (3-hourly), stored in HPFC-data under data/weather_fc/history_ens/:

  points/<point>.csv.gz    the 37 points of points.csv (nearest cell): control, member mean and sd of
                           wind100_ms, ghi_wm2, temp_c; control and mean of precip_mmh
  regions/<region>.csv.gz  area averages over the grid cells of a region (cos-latitude weights; uniform within the
                           region in this version, capacity weights later): for each member the region value of
                           wind_cf (power curve of the 100 m wind, cut-in 3 m/s, rated 12 m/s, as spot_fc),
                           ghi_wm2, temp_c, precip_mmh; stored as control, mean, sd, p10, p90 over the 51 members
  columns: init_utc, lead_h, valid_utc, <var>_<stat>. ghi is the mean flux over the 3 h ending at valid_utc,
  precipitation the mean rate over the same 3 h (kg m-2 s-1 -> mm/h).

  python collect_ecmwf_ens_history.py inspect
  python collect_ecmwf_ens_history.py history [--start 2020-10-15] [--end yyyy-mm-dd]
Resumable by month (months stored for every point and region are skipped); stops after ENS_MAX_MIN minutes.
"""
import argparse
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

DATASET = "ecmwf-ifs-ens-forecast-15-day-0-25-degree"
DATA_HOME = Path(os.environ.get("DATA_ROOT", Path(__file__).resolve().parent))
POINTS = DATA_HOME / "data" / "weather_fc" / "points.csv"
OUT = DATA_HOME / "data" / "weather_fc" / "history_ens"
BOX = dict(lat=(58.0, 36.0), lon=(-6.0, 25.0))     # DE/FR/IT/AT/CH + BE/NL/DK/PL/CZ and the North Sea / Baltic
LEADS = pd.to_timedelta(np.arange(0, 97, 3), unit="h")
MAX_MIN = float(os.environ.get("ENS_MAX_MIN", 300))
VARS = {"wind_u_100m": "u", "wind_v_100m": "v", "downward_short_wave_radiation_flux_surface": "ghi",
        "temperature_2m": "temp", "precipitation_surface": "pr"}
COUNTRIES = {"DE": "Germany", "FR": "France", "IT": "Italy", "AT": "Austria", "CH": "Switzerland",
             "BE": "Belgium", "NL": "Netherlands", "DK": "Denmark", "PL": "Poland", "CZ": "Czechia"}
# boxes on top of the country masks: IT-North bidding zone (≈ north of 43.8° N), Swiss Alps (CH south of 46.85° N:
# Valais, Ticino, Graubünden; hydro), German offshore wind (sea cells only)
SUBREGIONS = {"IT_N": ("IT", (43.8, 48.0), (6.0, 14.0)), "CH_ALPS": ("CH", (45.7, 46.85), (5.9, 10.6))}
SEA = {"DE_OFF_NORTH": ((53.6, 55.6), (5.5, 8.6)), "DE_OFF_BALTIC": ((54.2, 55.0), (12.0, 14.6))}


def open_ds(parallel=False):
    import dynamical_catalog as dc
    return dc.open(DATASET, chunks={} if parallel else None)


def inspect():
    ds = open_ds()
    print(ds)
    print("init_time", str(ds.init_time.values[0]), "..", str(ds.init_time.values[-1]), len(ds.init_time))


def power_curve(ms):
    return np.clip((ms ** 3 - 27.0) / (1728.0 - 27.0), 0, 1)


def region_weights(lat, lon):
    """{region: (lat x lon) weights summing to 1}: country masks (Natural Earth 1:50m via regionmask) x cos(lat)."""
    import regionmask
    ne = regionmask.defined_regions.natural_earth_v5_0_0.countries_50
    mask = ne.mask(lon, lat).values                       # region number or NaN (sea)
    num = {name: i for i, name in zip(ne.numbers, ne.names)}
    LA, LO = np.meshgrid(lat, lon, indexing="ij")
    cosw = np.cos(np.deg2rad(LA))
    out = {}
    for k, name in COUNTRIES.items():
        out[k] = np.where(mask == num[name], cosw, 0.0)
    for k, (base, (la0, la1), (lo0, lo1)) in SUBREGIONS.items():
        out[k] = np.where((out[base] > 0) & (LA >= la0) & (LA <= la1) & (LO >= lo0) & (LO <= lo1), cosw, 0.0)
    for k, ((la0, la1), (lo0, lo1)) in SEA.items():
        out[k] = np.where(np.isnan(mask) & (LA >= la0) & (LA <= la1) & (LO >= lo0) & (LO <= lo1), cosw, 0.0)
    for k in list(out):
        s = out[k].sum()
        if s == 0:
            print("no cells for", k); out.pop(k); continue
        out[k] = out[k] / s
        print(f"region {k}: {int((out[k] > 0).sum())} cells", flush=True)
    return out


def one_init(sub, W, pts_idx, init):
    """sub: loaded Dataset (lead, member, lat, lon) for one init -> (point rows, region rows) as DataFrames."""
    u, v = sub["u"].values, sub["v"].values                   # (lead, member, lat, lon)
    ws = np.sqrt(u * u + v * v)
    fields = {"wind100_ms": ws, "ghi_wm2": sub["ghi"].values, "temp_c": sub["temp"].values,
              "precip_mmh": sub["pr"].values * 3600.0}
    nl = len(LEADS)
    base = {"init_utc": np.repeat(pd.Timestamp(init).strftime("%Y-%m-%d %H:%M"), nl),
            "lead_h": (LEADS / pd.Timedelta(hours=1)).astype(int),
            "valid_utc": (pd.Timestamp(init) + LEADS).strftime("%Y-%m-%d %H:%M")}
    prow = {}
    for p, (i, j) in pts_idx.items():
        d = dict(base)
        for k, f in fields.items():
            x = f[:, :, i, j]                                  # (lead, member)
            d[f"{k}_ctl"] = x[:, 0]
            d[f"{k}_mean"] = x.mean(axis=1)
            if k != "precip_mmh":
                d[f"{k}_sd"] = x.std(axis=1)
        prow[p] = pd.DataFrame(d)
    rfields = {"wind_cf": power_curve(ws), "ghi_wm2": fields["ghi_wm2"], "temp_c": fields["temp_c"],
               "precip_mmh": fields["precip_mmh"]}
    rrow = {}
    for r, w in W.items():
        d = dict(base)
        for k, f in rfields.items():
            x = np.einsum("lmij,ij->lm", np.nan_to_num(f), w)  # region value per lead and member
            d[f"{k}_ctl"] = x[:, 0]
            d[f"{k}_mean"] = x.mean(axis=1)
            d[f"{k}_sd"] = x.std(axis=1)
            d[f"{k}_p10"] = np.percentile(x, 10, axis=1)
            d[f"{k}_p90"] = np.percentile(x, 90, axis=1)
        rrow[r] = pd.DataFrame(d)
    return prow, rrow


def append(path, new):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = pd.read_csv(path)
        new = pd.concat([old, new]).drop_duplicates(["init_utc", "lead_h"], keep="last")
    new = new.sort_values(["init_utc", "lead_h"])
    num = new.select_dtypes("number").columns.drop("lead_h")
    new[num] = new[num].round(4)
    new.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})


def history(start, end):
    import dask
    dask.config.set(scheduler="threads", num_workers=16)
    t0 = time.time()
    ds = open_ds(parallel=True)
    lat = ds.latitude.values
    lats = slice(*BOX["lat"]) if lat[0] > lat[-1] else slice(*BOX["lat"][::-1])
    box = ds[list(VARS)].rename(VARS).sel(latitude=lats, longitude=slice(*BOX["lon"]), lead_time=LEADS)
    blat, blon = box.latitude.values, box.longitude.values
    W = region_weights(blat, blon)
    pts = pd.read_csv(POINTS)
    pts_idx = {p.point: (int(np.abs(blat - p.lat).argmin()), int(np.abs(blon - p.lon).argmin()))
               for p in pts.itertuples()}
    inits = pd.DatetimeIndex(ds.init_time.values)
    inits = inits[(inits >= pd.Timestamp(start)) & (inits <= pd.Timestamp(end))]
    files = [OUT / "points" / f"{p}.csv.gz" for p in pts_idx] + [OUT / "regions" / f"{r}.csv.gz" for r in W]
    done = None
    for f in files:
        m = set(pd.read_csv(f, usecols=["init_utc"]).init_utc.str[:7]) if f.exists() else set()
        done = m if done is None else done & m
    last = max(done) if done else None
    for m in sorted({i.strftime("%Y-%m") for i in inits}):
        # finished months are skipped, except the latest one (it may still have been incomplete)
        if m in done and m != last:
            continue
        if time.time() - t0 > MAX_MIN * 60:
            print(f"time limit of {MAX_MIN} min reached; the rest next run", flush=True); break
        t1 = time.time()
        P, R = {}, {}
        mi = inits[inits.strftime("%Y-%m") == m]
        for init in mi:
            try:
                sub = box.sel(init_time=init).load()
            except Exception as ex:                               # a broken run must not stop the backfill
                print(f"{init}: {repr(ex)[:200]}", flush=True); continue
            prow, rrow = one_init(sub, W, pts_idx, init)
            for k, v in prow.items():
                P.setdefault(k, []).append(v)
            for k, v in rrow.items():
                R.setdefault(k, []).append(v)
        for k, v in P.items():
            append(OUT / "points" / f"{k}.csv.gz", pd.concat(v))
        for k, v in R.items():
            append(OUT / "regions" / f"{k}.csv.gz", pd.concat(v))
        print(f"{m}: {len(mi)} runs in {time.time() - t1:.0f}s (total {(time.time() - t0) / 60:.0f} min)", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["inspect", "history"])
    ap.add_argument("--start", default="2020-10-15")
    ap.add_argument("--end", default=pd.Timestamp.utcnow().strftime("%Y-%m-%d"))
    a = ap.parse_args()
    inspect() if a.mode == "inspect" else history(a.start, a.end)
