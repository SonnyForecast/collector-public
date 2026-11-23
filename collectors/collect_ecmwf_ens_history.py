#!/usr/bin/env python3
"""ECMWF IFS ENS forecast history (dynamical.org "ecmwf-ifs-ens-forecast-15-day-0-25-degree", 00 UTC inits from
Oct 2020, 51 members, 3-hourly to 144 h; ECMWF IFS ENS forecast data processed by dynamical.org from ECMWF Open
Data, CC BY 4.0 + ECMWF Terms of Use: attribution required).

Step 1 (this version): inspect the dataset and time a read, before choosing what to store.

  python collect_ecmwf_ens_history.py inspect
"""
import time

DATASET = "ecmwf-ifs-ens-forecast-15-day-0-25-degree"
BOX = dict(lat=(58.0, 36.0), lon=(-6.0, 25.0))     # DE/FR/IT/AT/CH + BE/NL/DK/PL/CZ


def open_ds(chunks=None):
    import dynamical_catalog as dc
    return dc.open(DATASET, chunks=chunks)


def inspect():
    t0 = time.time()
    ds = open_ds()
    print(ds, flush=True)
    for v in ds.data_vars:
        print(f"{v}: dims={ds[v].dims} chunks={ds[v].encoding.get('chunks')} shards={ds[v].encoding.get('shards')} "
              f"units={ds[v].attrs.get('units')}", flush=True)
    for c in ("init_time", "lead_time", "ensemble_member", "latitude", "longitude"):
        if c in ds.coords:
            x = ds[c].values
            print(f"{c}: {x[0]} .. {x[-1]} ({len(x)})", flush=True)
    print(f"opened in {time.time() - t0:.0f}s", flush=True)
    # timing: one init, all members, leads 0-96 h, the box, two variables
    import numpy as np
    import pandas as pd
    lat = ds.latitude.values
    lats = slice(*BOX["lat"]) if lat[0] > lat[-1] else slice(*BOX["lat"][::-1])
    sub = ds[["wind_u_100m", "downward_short_wave_radiation_flux_surface"]].sel(
        init_time=pd.Timestamp("2023-04-30"), lead_time=pd.to_timedelta(np.arange(0, 97, 3), unit="h"),
        latitude=lats, longitude=slice(*BOX["lon"]))
    print("subset", dict(sub.sizes), f"{sub.nbytes / 1e6:.0f} MB", flush=True)
    t1 = time.time()
    sub.load()
    print(f"loaded in {time.time() - t1:.0f}s", flush=True)
    print("ctl u100 mean", float(sub.wind_u_100m.isel(ensemble_member=0).mean()),
          "ssrd mean", float(sub.downward_short_wave_radiation_flux_surface.mean()), flush=True)
    # data coverage per year (non-NaN share of one cell, control member, lead 24 h)
    s = ds["temperature_2m"].sel(latitude=47.5, longitude=8.5, method="nearest").isel(ensemble_member=0) \
        .sel(lead_time=pd.Timedelta(hours=24)).load().to_series()
    print("coverage by year (share of inits with data):", s.notna().groupby(s.index.year).mean().round(3).to_dict())


if __name__ == "__main__":
    import sys
    {"inspect": inspect}[sys.argv[1] if len(sys.argv) > 1 else "inspect"]()
