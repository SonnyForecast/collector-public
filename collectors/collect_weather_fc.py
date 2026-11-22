#!/usr/bin/env python3
"""Weather forecasts for the Swiss spot forecast (days 2-7 ahead), by lead time, at 37 points in DE, CH, FR, IT, AT
(data/weather_fc/points.csv: wind regions, solar regions, load centres).

  history  Open-Meteo Previous Runs API, ECMWF IFS 0.25 deg: for every valid hour the value of the run issued
           1..7 days earlier (`previous_dayN`), so a backtest sees only what was forecast at the time. Available
           from about March 2024. -> data/weather_fc/history/<point>.csv.gz
  live     Open-Meteo forecast API, ECMWF IFS, next 15 days, newest run -> data/weather_fc/live/<yyyy-mm>/<fetch time>.csv.gz
  --model icon  same history from DWD ICON (icon_seamless; CC BY 4.0) -> data/weather_fc/history_icon/<point>.csv.gz,
           a second weather source with different errors for the ensemble (M1 on ICON).
Variables: wind speed 100 m (km/h), global horizontal irradiance (W/m2, mean of the hour ending at the time),
2 m temperature (C), cloud cover (%). Times UTC, hour of the valid time.

    python collect_weather_fc.py history [--start 2024-03-01] [--end yyyy-mm-dd] [--force] [--model icon]
    python collect_weather_fc.py live
    python collect_weather_fc.py --status
Free Open-Meteo tier: non-commercial use, about 10,000 weighted calls a day; the history needs ~5,000 the first time
and then only appends the missing days. ECMWF data CC BY 4.0. Written on 11 Nov 2026 (sandbox cannot reach
Open-Meteo: first tested in a browser, then run on GitHub).
"""
import io
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

import os as _os
# Data folder: the checked-out private data repo (env DATA_ROOT); default = next to this script
DATA_HOME = Path(_os.environ.get("DATA_ROOT", Path(__file__).resolve().parent))

ROOT = DATA_HOME
DIR = ROOT / "data" / "weather_fc"
PREV = "https://previous-runs-api.open-meteo.com/v1/forecast"
LIVE = "https://api.open-meteo.com/v1/forecast"
VARS = {"wind_speed_100m": "wind100_kmh", "shortwave_radiation": "ghi_wm2", "temperature_2m": "temp_c",
        "cloud_cover": "cloud_pct"}
LEADS = range(1, 8)
MODEL = "ecmwf_ifs025"
MODELS = {"ecmwf": ("ecmwf_ifs025", "history"), "icon": ("icon_seamless", "history_icon")}
UA = {"User-Agent": "hpfc-data/0.1 (personal research)"}
MAX_MIN = float(os.environ.get("HIST_MAX_MIN", 8))   # stop the history after this many minutes so the workflow can still commit
BUDGET = 4000      # weighted Open-Meteo calls per run (limits: 600/min, 5,000/h, 10,000/day); the rest next run


def weight(nvars, days):
    """Open-Meteo counts a call as (variables / 10) x (days / 14), at least 1."""
    return max(1.0, nvars / 10) * max(1.0, days / 14)


def get(url, params, tries=6):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=60)   # requests over the minute limit hang instead of getting a 429
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:            # minute / hour limit: wait and retry
                print("  rate limited, waiting", 60 * (i + 1), "s", flush=True); time.sleep(60 * (i + 1)); continue
            print("  HTTP", r.status_code, r.text[:200], flush=True)
        except requests.RequestException as e:
            print("  error", e, flush=True)
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"failed {url} {params.get('latitude')},{params.get('longitude')} {params.get('start_date')}")


def points():
    return pd.read_csv(DIR / "points.csv")


def history(start, end, force=False, model="ecmwf"):
    om_model, sub = MODELS[model]
    out = DIR / sub; out.mkdir(parents=True, exist_ok=True)
    hourly = [f"{v}_previous_day{d}" for v in VARS for d in LEADS]
    used, t0, failed = 0.0, time.time(), 0
    pts = points()
    last = {}                                   # stalest points first: with server timeouts the time limit is hit
    for q in pts.point:                         # early, and a fixed order left the last points behind (Nov 2026)
        f = out / f"{q}.csv.gz"
        last[q] = pd.read_csv(f, usecols=["time_utc"]).time_utc.max() if f.exists() and not force else ""
    pts = pts.assign(_last=pts.point.map(last)).sort_values("_last", kind="stable")
    for p in pts.itertuples():
        if time.time() - t0 > MAX_MIN * 60:
            print(f"time limit of {MAX_MIN} min reached; the rest next run", flush=True); break
        f = out / f"{p.point}.csv.gz"
        old = pd.read_csv(f, parse_dates=["time_utc"]) if f.exists() and not force else None
        s = start if old is None else max(start, (old.time_utc.max() - pd.Timedelta(days=8)).date())
        if s > end:
            continue
        parts, d0 = [], s
        while d0 <= end:            # 2-month chunks: a year at once often times out on the server
            nxt = date(d0.year + (d0.month + 1) // 12, (d0.month + 1) % 12 + 1, 1)
            d1 = min(end, nxt - timedelta(days=1))
            w = weight(len(hourly), (d1 - d0).days + 1)
            if used + w > BUDGET or time.time() - t0 > MAX_MIN * 60:
                break
            t1 = time.time()
            try:
                j = get(PREV, dict(latitude=p.lat, longitude=p.lon, hourly=",".join(hourly), models=om_model,
                                   start_date=d0.isoformat(), end_date=d1.isoformat(), timezone="GMT"), tries=4)
            except RuntimeError as e:
                print("  ", e, "- keeping what this point has so far", flush=True); failed += 1
                break
            h = pd.DataFrame(j["hourly"])
            h["time_utc"] = pd.to_datetime(h.pop("time"))
            print(f"  {p.point} {d0}..{d1}: {time.time() - t1:.0f} s", flush=True)
            ren = {f"{v}_previous_day{d}": f"{VARS[v]}_d{d}" for v in VARS for d in LEADS}
            parts.append(h.rename(columns=ren)); d0 = d1 + timedelta(days=1)
            used += w
            time.sleep(max(3.0, w * 0.25))      # ~240 weighted calls a minute; faster runs hit hangs every ~3 min
        if not parts and failed:
            continue
        if not parts:
            print(f"budget of {BUDGET} weighted calls used; the rest next run", flush=True); break
        new = pd.concat(parts)
        new = new.dropna(how="all", subset=[c for c in new.columns if c != "time_utc"])
        df = new if old is None else pd.concat([old[old.time_utc < new.time_utc.min()], new])
        df = df.sort_values("time_utc").drop_duplicates("time_utc", keep="last")
        df.round(2).to_csv(f, index=False, compression={"method": "gzip", "compresslevel": 9, "mtime": 0})
        if failed > 15:
            print("too many failures, stopping (server busy?)", flush=True); break
        print(f"{p.point}: {len(df)} hours {df.time_utc.min()} .. {df.time_utc.max()} (weighted calls {used:.0f})", flush=True)


def live():
    ps = points()
    j = get(LIVE, dict(latitude=",".join(map(str, ps.lat)), longitude=",".join(map(str, ps.lon)),
                       hourly=",".join(VARS), models=MODEL, forecast_days=15, timezone="GMT"))
    js = j if isinstance(j, list) else [j]
    now = datetime.now(timezone.utc)
    rows = []
    for p, x in zip(ps.itertuples(), js):
        h = pd.DataFrame(x["hourly"]).rename(columns=VARS)
        h["time_utc"] = pd.to_datetime(h.pop("time")); h.insert(0, "point", p.point)
        rows.append(h)
    df = pd.concat(rows); df.insert(0, "fetched_utc", now.strftime("%Y-%m-%d %H:%M"))
    f = DIR / "live" / f"{now:%Y-%m}" / f"{now:%Y%m%d_%H%M}.csv.gz"
    f.parent.mkdir(parents=True, exist_ok=True)
    df.round(2).to_csv(f, index=False, compression={"method": "gzip", "compresslevel": 9, "mtime": 0})
    print(f"live: {len(ps)} points, {df.time_utc.min()} .. {df.time_utc.max()} -> {f.relative_to(ROOT)}")


def status():
    for f in sorted((DIR / "history").glob("*.csv.gz")) + sorted((DIR / "history_icon").glob("*.csv.gz")):
        d = pd.read_csv(f, usecols=["time_utc", "wind100_kmh_d3"])
        print(f"{f.parent.name[8:] or 'ecmwf':6s} {f.name[:-7]:15s} {d.time_utc.min()} .. {d.time_utc.max()}  missing d3 {d.wind100_kmh_d3.isna().mean():.1%}")
    lv = sorted((DIR / "live").rglob("*.csv.gz"))
    print(f"live runs: {len(lv)}" + (f", last {lv[-1].name}" if lv else ""))


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--status" in a:
        status()
    elif a and a[0] == "history":
        st = date.fromisoformat(a[a.index("--start") + 1]) if "--start" in a else date(2024, 3, 1)
        en = date.fromisoformat(a[a.index("--end") + 1]) if "--end" in a else date.today() - timedelta(days=1)
        history(st, en, "--force" in a, a[a.index("--model") + 1] if "--model" in a else "ecmwf")
    elif a and a[0] == "live":
        live()
    else:
        print(__doc__)
