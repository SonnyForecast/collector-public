"""Print the CH day-ahead prices of one delivery day (Energy-Charts API, quarter-hours averaged to hours)."""
import sys, requests, pandas as pd
d = sys.argv[1]
j = requests.get("https://api.energy-charts.info/price", params={"bzn": "CH", "start": d, "end": d}, timeout=60).json()
s = pd.Series(j["price"], index=pd.to_datetime(j["unix_seconds"], unit="s", utc=True).tz_convert("Europe/Zurich"))
s = s[s.index.strftime("%Y-%m-%d") == d]
h = s.groupby(s.index.hour).mean()
print("PRICES", d, ",".join(f"{v:.2f}" for v in h.values), "n", len(s))
