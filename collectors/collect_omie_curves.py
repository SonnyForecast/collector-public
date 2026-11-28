"""OMIE day-ahead aggregated bid curves (public files curva_pbc_YYYYMMDD.1) -> compact hourly curve features, plus
OMIE Spanish prices (marginalpdbc) and the ENTSO-E day-ahead forecasts for Spain (load A65/A01, wind and solar
A69/A01). Research input for the solar-crash curve test (spot-ensemble CH/results/solar_crash_20261128).

Writes into DATA_ROOT (the HPFC-data checkout):
  data/omie/curve_features.csv  day, hour (1..25, OMIE local delivery hour), price_es, and per hour (mean of the four
                                quarters since the 15-min switch on 1 Oct 2025), from the OFFERED (O) curves:
                                q_clear   matched sell volume (MW)
                                v_le0     sell volume offered at <= 0 EUR/MWh (must-sell + zero bids)
                                v_0_5, v_5_30, v_30_60, v_60_120   sell volume in those price bands
                                d_inel    buy volume at >= 180 EUR/MWh (price-insensitive demand); d_tot all buy
                                margin0   v_le0 - d_inel (> 0: zero-priced supply alone covers inelastic demand)
                                p_m1gw, p_p1gw   supply-curve price 1 GW below / above the clearing volume
  data/omie/es_forecasts.csv    ts_utc, load_fc, wind_fc, solar_fc (MW, ENTSO-E day-ahead; NB the wind/solar
                                forecasts are published ~18:00 on D-1, after the 12:00 gate: use for relative tests)
Resumable: days already in the file are skipped; stops cleanly after --max-min minutes so the workflow can push.
OMIE data: public, attribution "OMIE"; ENTSO-E: token from the ENTSOE_TOKEN secret, never printed."""
import argparse
import io
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
import entsoe_api  # noqa: E402

U = "https://www.omie.es/es/file-download?parents%5B0%5D={p}&filename={p}_{d}.1"
ES = "10YES-REE------0"
COLS = ["day", "hour", "price_es", "q_clear", "v_le0", "v_0_5", "v_5_30", "v_30_60", "v_60_120", "d_inel", "d_tot",
        "margin0", "p_m1gw", "p_p1gw"]


def get(p, d, s):
    for i in range(4):
        try:
            r = s.get(U.format(p=p, d=d.strftime("%Y%m%d")), timeout=180)
            if r.status_code == 200 and len(r.content) > 200:
                return r.content.decode("latin-1")
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(5 * (i + 1))
    return None


def curve_features(txt):
    df = pd.read_csv(io.StringIO(txt), sep=";", skiprows=2, header=None, dtype=str, usecols=range(8))
    df = df[df[4].isin(["C", "V"])]
    per = df[0].astype(str)
    if per.str.startswith("H").any():                         # 15-min: H{hour}Q{quarter}
        hour = per.str.extract(r"H(\d+)Q")[0].astype(int)
    else:
        hour = per.astype(int)
    num = lambda c: pd.to_numeric(df[c].str.replace(".", "", regex=False).str.replace(",", ".", regex=False))  # noqa: E731
    x = pd.DataFrame({"per": per.values, "hour": hour.values, "side": df[4].values, "e": num(5).values,
                      "p": num(6).values, "oc": df[7].values})
    out = []
    for (h, pr), g in x.groupby(["hour", "per"], sort=False):
        sell = g[(g.side == "V") & (g.oc == "O")].sort_values("p")
        buy = g[(g.side == "C") & (g.oc == "O")]
        qc = g[(g.side == "V") & (g.oc == "C")].e.sum()
        cum = sell.e.cumsum().to_numpy(); sp = sell.p.to_numpy()
        f = dict(hour=h, q_clear=qc,
                 v_le0=sell.e[sell.p <= 0].sum(), v_0_5=sell.e[(sell.p > 0) & (sell.p <= 5)].sum(),
                 v_5_30=sell.e[(sell.p > 5) & (sell.p <= 30)].sum(), v_30_60=sell.e[(sell.p > 30) & (sell.p <= 60)].sum(),
                 v_60_120=sell.e[(sell.p > 60) & (sell.p <= 120)].sum(),
                 d_inel=buy.e[buy.p >= 180].sum(), d_tot=buy.e.sum())
        f["margin0"] = f["v_le0"] - f["d_inel"]
        if len(cum):
            f["p_m1gw"] = float(sp[np.searchsorted(cum, max(qc - 1000, 0), side="left").clip(0, len(sp) - 1)])
            f["p_p1gw"] = float(sp[np.searchsorted(cum, qc + 1000, side="left").clip(0, len(sp) - 1)])
        out.append(f)
    return pd.DataFrame(out).groupby("hour").mean(numeric_only=True)


def prices(txt):
    rows = [l.split(";") for l in txt.splitlines()[1:] if l[:2].isdigit()]
    p = pd.DataFrame({"per": [int(r[3]) for r in rows], "es": [float(r[5]) for r in rows]})
    n = len(p)
    p["hour"] = (p.per - 1) // 4 + 1 if n > 25 else p.per            # 92/96/100 quarters since Oct 2025
    return p.groupby("hour").es.mean()


def forecasts(start, end, out):
    tok = os.environ.get("ENTSOE_TOKEN")
    if not tok:
        print("no ENTSOE_TOKEN: forecasts skipped"); return
    parts = {}
    for nm, prm in (("load_fc", dict(documentType="A65", processType="A01", outBiddingZone_Domain=ES)),
                    ("wind_fc", dict(documentType="A69", processType="A01", in_Domain=ES, psrType="B19")),
                    ("solar_fc", dict(documentType="A69", processType="A01", in_Domain=ES, psrType="B16"))):
        ss = []
        a = datetime(start.year, start.month, 1)
        while a.date() <= end:
            b = min(a + timedelta(days=180), datetime.combine(end + timedelta(days=2), datetime.min.time()))
            p = dict(prm, periodStart=a.strftime("%Y%m%d0000"), periodEnd=b.strftime("%Y%m%d0000"))
            try:
                ss.append(entsoe_api.parse_series(entsoe_api.fetch(tok, p)))
            except RuntimeError as e:
                print(nm, a.date(), "failed:", entsoe_api._scrub(e))
            a = b
        parts[nm] = pd.concat(ss).sort_index() if ss else pd.Series(dtype=float)
        parts[nm] = parts[nm][~parts[nm].index.duplicated(keep="last")]
        print(nm, len(parts[nm]), "hours")
    f = pd.DataFrame(parts); f.index.name = "ts_utc"
    f.to_csv(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01"); ap.add_argument("--end", default="2026-10-31")
    ap.add_argument("--max-min", type=float, default=300)
    a = ap.parse_args()
    t0 = time.time()
    root = Path(os.environ.get("DATA_ROOT", ".")) / "data" / "omie"; root.mkdir(parents=True, exist_ok=True)
    fn = root / "curve_features.csv"
    old = pd.read_csv(fn) if fn.exists() else pd.DataFrame(columns=COLS)
    have = set(old.day.astype(str))
    d0, d1 = date.fromisoformat(a.start), date.fromisoformat(a.end)
    s = requests.Session(); s.headers.update({"User-Agent": "research (SonnyForecast collector-public)"})
    new, n_ok, n_miss, d = [], 0, 0, d0
    while d <= d1 and (time.time() - t0) / 60 < a.max_min:
        if str(d) not in have:
            c, p = get("curva_pbc", d, s), get("marginalpdbc", d, s)
            if c and p:
                try:
                    f = curve_features(c); f["price_es"] = prices(p); f["day"] = str(d)
                    new.append(f.reset_index()); n_ok += 1
                except Exception as e:                      # one bad file must not stop the backfill
                    print(d, "parse failed:", type(e).__name__, str(e)[:120]); n_miss += 1
            else:
                n_miss += 1
            if n_ok and n_ok % 100 == 0:
                print(d, n_ok, "days", round((time.time() - t0) / 60, 1), "min", flush=True)
            time.sleep(0.3)
        d += timedelta(days=1)
    allf = pd.concat([old] + new, ignore_index=True)[COLS] if new else old
    allf = allf.drop_duplicates(["day", "hour"], keep="last").sort_values(["day", "hour"])
    allf.round(3).to_csv(fn, index=False)
    print(f"curves: {n_ok} new days, {n_miss} missing/failed, {allf.day.nunique()} days in {fn}; stopped at {d}")
    if d > d1:                                               # backfill complete -> forecasts once
        forecasts(d0, d1, root / "es_forecasts.csv")


if __name__ == "__main__":
    main()
