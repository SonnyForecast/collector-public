#!/usr/bin/env python3
"""NOAA GFS forecast history at the weather points of the spot forecast, from dynamical.org's cloud-optimised archive
(Zarr, init times from May 2021, hourly steps to 120 h; NOAA NWS NCEP GFS data processed by dynamical.org, CC BY 4.0).

Real archived forecasts with their lead times, so models can be trained point-in-time on years before the Open-Meteo
lead-time archive starts (2024). Kept: the 00 UTC run of each day (available about 04:00 UTC, before the 07:00 Swiss
issue of the next day's forecast), lead times 0-72 h, hourly.

  python collect_gfs_history.py inspect                 # print dimensions, variables, chunks (first run)
  python collect_gfs_history.py history [--start 2021-05-01] [--end yyyy-mm-dd]
        -> $DATA_ROOT/data/weather_fc/history_gfs/<point>.csv.gz
           columns: init_utc, lead_h, valid_utc, temp_c, wind100_ms, ghi_wm2, cloud_pct (where available)
Resumable: months already stored for all points are skipped; stops after GFS_MAX_MIN minutes (default 300).
"""
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

DATA_HOME = Path(os.environ.get("DATA_ROOT", Path(__file__).resolve().parent))
POINTS = DATA_HOME / "data" / "weather_fc" / "points.csv"
OUT = DATA_HOME / "data" / "weather_fc" / "history_gfs"
URL = "https://data.dynamical.org/noaa/gfs/forecast/latest.zarr?email=spot-forecast@users.noreply.github.com"
LEADS = np.arange(0, 73)                      # hours
MAX_MIN = float(os.environ.get("GFS_MAX_MIN", 300))
# dynamical.org variable names -> our columns (resolved against the dataset at run time)
WANT = {"temperature_2m": "temp_c",
        "wind_u_100m": "u100", "wind_v_100m": "v100",
        "downward_short_wave_radiation_flux_surface": "ghi_wm2",
        "total_cloud_cover_atmosphere": "cloud_pct"}


def open_ds():
    import xarray as xr
    return xr.open_zarr(URL, chunks=None, decode_timedelta=True)


def inspect():
    ds = open_ds()
    print(ds)
    for v in list(ds.data_vars)[:80]:
        print(f"{v}: dims={ds[v].dims} chunks={ds[v].encoding.get('chunks')} units={ds[v].attrs.get('units')}")
    print("init_time", str(ds.init_time.values[0]), "..", str(ds.init_time.values[-1]), len(ds.init_time))
    print("lead_time", ds.lead_time.values[:5], "..", ds.lead_time.values[-1])


def history(start, end):
    import xarray as xr
    t0 = time.time()
    ds = open_ds()
    have = {k: v for k, v in WANT.items() if k in ds.data_vars}
    missing = [k for k in WANT if k not in ds.data_vars]
    if missing:
        print("not in dataset (skipped):", missing, flush=True)
    pts = pd.read_csv(POINTS)
    OUT.mkdir(parents=True, exist_ok=True)
    lat = xr.DataArray(pts.lat.values, dims="point"); lon = xr.DataArray(pts.lon.values % 360 if float(ds.longitude.max()) > 180 else pts.lon.values, dims="point")
    lead = pd.to_timedelta(LEADS, unit="h")
    inits = pd.DatetimeIndex(ds.init_time.values)
    inits = inits[(inits.hour == 0) & (inits >= pd.Timestamp(start)) & (inits <= pd.Timestamp(end))]
    done = {}
    for p in pts.point:
        f = OUT / f"{p}.csv.gz"
        done[p] = set(pd.read_csv(f, usecols=["init_utc"]).init_utc.str[:7]) if f.exists() else set()
    months = sorted({i.strftime("%Y-%m") for i in inits})
    for m in months:
        if all(m in done[p] for p in pts.point):
            continue
        if time.time() - t0 > MAX_MIN * 60:
            print(f"time limit of {MAX_MIN} min reached; the rest next run", flush=True); break
        mi = inits[inits.strftime("%Y-%m") == m]
        t1 = time.time()
        sub = ds[list(have)].sel(init_time=mi, lead_time=lead).sel(latitude=lat, longitude=lon, method="nearest").load()
        frames = []
        for k, p in enumerate(pts.point):
            d = sub.isel(point=k).to_dataframe().reset_index()
            d = d.rename(columns=have)
            if "u100" in d and "v100" in d:
                d["wind100_ms"] = np.hypot(d.u100, d.v100)
            d["lead_h"] = (d.lead_time / pd.Timedelta(hours=1)).astype(int)
            d["init_utc"] = pd.to_datetime(d.init_time).dt.strftime("%Y-%m-%d %H:%M")
            d["valid_utc"] = (pd.to_datetime(d.init_time) + d.lead_time).dt.strftime("%Y-%m-%d %H:%M")
            cols = ["init_utc", "lead_h", "valid_utc"] + [c for c in ["temp_c", "wind100_ms", "ghi_wm2", "cloud_pct"] if c in d]
            frames.append((p, d[cols].round(2)))
        for p, d in frames:
            f = OUT / f"{p}.csv.gz"
            old = pd.read_csv(f) if f.exists() else None
            new = d if old is None else pd.concat([old, d]).drop_duplicates(["init_utc", "lead_h"], keep="last")
            new.sort_values(["init_utc", "lead_h"]).to_csv(f, index=False, compression={"method": "gzip", "mtime": 0})
        print(f"{m}: {len(mi)} runs x {len(pts)} points ({time.time() - t1:.0f} s)", flush=True)
    print(f"done ({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "inspect":
        inspect()
    else:
        st = a[a.index("--start") + 1] if "--start" in a else "2021-05-01"
        en = a[a.index("--end") + 1] if "--end" in a else pd.Timestamp.today().strftime("%Y-%m-%d")
        history(st, en)
