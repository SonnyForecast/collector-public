"""Live check (23 Nov 2026): does the Open-Meteo previous-runs API return lead-2 (previous_day2) values for the
NEXT day already today, at the 37 points? That is what the 07:00 live run needs (same definition as the backtest)."""
import datetime as dt
import pandas as pd, requests
PREV = "https://previous-runs-api.open-meteo.com/v1/forecast"
p = pd.read_csv("data-repo/data/weather_fc/points.csv").head(3)
today = dt.date.today(); tom = today + dt.timedelta(days=1); d2 = today + dt.timedelta(days=2)
v = ["wind_speed_100m", "shortwave_radiation", "temperature_2m"]
hourly = [f"{x}_previous_day{d}" for x in v for d in (1, 2, 3)]
for r in p.itertuples():
    j = requests.get(PREV, params=dict(latitude=r.lat, longitude=r.lon, hourly=",".join(hourly), models="ecmwf_ifs025",
                                       start_date=today.isoformat(), end_date=d2.isoformat(), timezone="GMT"), timeout=60).json()
    h = pd.DataFrame(j["hourly"]); h["time"] = pd.to_datetime(h.time)
    print(r.point, "now UTC", dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M"))
    for c in [c for c in h.columns if c.startswith("wind")]:
        ok = h[h[c].notna()]
        print(f"  {c}: last valid time with a value {ok.time.max()} | share of {tom} hours with a value "
              f"{h[h.time.dt.date == tom][c].notna().mean():.2f}")

# values for the next two days at the first point, to compare later with the history the collector stores once the
# hours are past (identical = the live lead-2 values are the true lead-2 forecast)
r = p.iloc[0]
j = requests.get(PREV, params=dict(latitude=r.lat, longitude=r.lon, hourly=",".join(hourly), models="ecmwf_ifs025",
                                   start_date=tom.isoformat(), end_date=d2.isoformat(), timezone="GMT"), timeout=60).json()
h = pd.DataFrame(j["hourly"])
print("VALUES", r.point)
print(h[["time"] + [c for c in h.columns if c.startswith("wind")]].to_csv(index=False))
