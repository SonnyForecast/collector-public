"""Weather behind a live forecast day: ECMWF (Open-Meteo previous-runs API) at the 37 price points, daily means per
area for a few delivery days and leads. Lead 2 (previous_day2) is what the 07:00 run on D−1 uses; lead 1 is the run of
D−1 00Z (out by ~07:40, after our issue, before the 11:00 gate); lead 0 the newest. Diagnostic only.
    python probes/weather_check_days.py 2026-11-24 2026-11-26"""
import sys
import pandas as pd
import requests

PREV = "https://previous-runs-api.open-meteo.com/v1/forecast"
d0, d1 = sys.argv[1], sys.argv[2]
pts = pd.read_csv("data-repo/data/weather_fc/points.csv")
V = ["temperature_2m", "wind_speed_100m", "shortwave_radiation"]
hourly = [v for v in V] + [f"{v}_previous_day{k}" for v in V for k in (1, 2)]
rows = []
for r in pts.itertuples():
    for t in range(3):
        try:
            j = requests.get(PREV, params=dict(latitude=r.lat, longitude=r.lon, hourly=",".join(hourly),
                                               models="ecmwf_ifs025", start_date=d0, end_date=d1, timezone="Europe/Zurich"),
                             timeout=60).json()
            h = pd.DataFrame(j["hourly"]); break
        except Exception as e:
            h = None; print("retry", r.point, e)
    if h is None:
        continue
    h["time"] = pd.to_datetime(h.time); h["day"] = h.time.dt.date.astype(str); h["hour"] = h.time.dt.hour
    h["point"], h["area"], h["role"] = r.point, r.area, r.role
    rows.append(h)
d = pd.concat(rows)
pd.set_option("display.width", 250)
for v in V:
    print(f"\n== {v}: daily mean by area (lead2 = what the 07:00 run used, lead1 = D−1 00Z, lead0 = newest)")
    out = {}
    for lab, col in (("lead2", f"{v}_previous_day2"), ("lead1", f"{v}_previous_day1"), ("lead0", v)):
        out[lab] = d.groupby(["area", "day"])[col].mean().unstack("day")
    print(pd.concat(out, axis=1).round(2).to_string())
    if v == "temperature_2m":
        ev = d[d.hour.between(16, 19)]
        print("evening 16-19 h:")
        print(pd.concat({lab: ev.groupby(["area", "day"])[col].mean().unstack("day") for lab, col in
                         (("lead2", f"{v}_previous_day2"), ("lead0", v))}, axis=1).round(2).to_string())
    if v == "wind_speed_100m":
        ev = d[(d.hour.between(16, 19)) & d.role.str.contains("wind")]
        print("evening 16-19 h, wind points only:")
        print(pd.concat({lab: ev.groupby(["area", "day"])[col].mean().unstack("day") for lab, col in
                         (("lead2", f"{v}_previous_day2"), ("lead0", v))}, axis=1).round(2).to_string())
