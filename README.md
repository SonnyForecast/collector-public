# collector-public

Small downloaders for **public energy-market and weather data**, run on a schedule by GitHub Actions.
This repository holds **code only**: no data is stored here, and the forecasting models live elsewhere.

| Workflow | Source | Schedule (UTC) |
|---|---|---|
| `weather_ecmwf.yml` | Open-Meteo: ECMWF IFS forecasts and by-lead history | daily 20:47 |
| `weather_icon.yml` | Open-Meteo: DWD ICON by-lead history | 02:13, 14:13 |
| `energy_charts.yml` | Energy-Charts (futures, generation, capacity), World Bank gas, EEX EUA auction results, ECB EUR/USD, ENTSO-E load | Saturday 05:27 |
| `entsoe_market.yml` | ENTSO-E Transparency Platform: outages with revisions, cross-border physical flows | daily 05:41 |
| `weather_gfs.yml` | NOAA GFS archived forecasts (dynamical.org, CC BY 4.0), 00 UTC runs from May 2021 | daily 03:37 |

Collected files are written to a private data repository (`DATA_ROOT`), not to this one, and the logs show only
counts and dates. Each source keeps its own terms: Open-Meteo free tier and Energy-Charts data for non-commercial use,
ENTSO-E Transparency Platform terms of use, World Bank (CC BY 4.0), ICON data from DWD (CC BY 4.0).

Run locally: `DATA_ROOT=/path/to/data-repo python collectors/collect_weather_fc.py --status`
(ENTSO-E collectors need `ENTSOE_TOKEN`).
