#!/usr/bin/env python3
"""Monthly gas and CO2 history for the structural shape model.

  World Bank Pink Sheet (CC BY 4.0): monthly 'Natural gas, Europe' (TTF, USD/mmbtu) -> data/fuels/CMO-Historical-Data-Monthly.xlsx
  EEX EUA primary auction results (current year, daily)                         -> data/fuels/eua_auctions_<year>.xlsx
  ECB reference rate EUR/USD, monthly average (EXR M.USD.EUR.SP00.A)            -> appended to data/fuels/eurusd_monthly.csv
     (completed months after the last one in the file only: the existing rows are never rewritten, so backtests
     stay reproducible)
The World Bank file URL carries a hash that changes monthly, so it is read from the commodity-markets page."""
import re
import sys
from datetime import date
from pathlib import Path

import requests

import os as _os
# Data folder: the checked-out private data repo (env DATA_ROOT); default = next to this script
DATA_HOME = Path(_os.environ.get("DATA_ROOT", Path(__file__).resolve().parent))

OUT = DATA_HOME / "data" / "fuels"
UA = {"User-Agent": "hpfc-data-archiver/0.1 (personal research)"}


def worldbank():
    page = requests.get("https://www.worldbank.org/en/research/commodity-markets", headers=UA, timeout=60).text
    url = re.search(r'https://thedocs\.worldbank\.org/[^"]+CMO-Historical-Data-Monthly\.xlsx', page).group(0)
    r = requests.get(url, headers=UA, timeout=120); r.raise_for_status()
    (OUT / "CMO-Historical-Data-Monthly.xlsx").write_bytes(r.content)
    print("world bank", url, len(r.content))


EUA_FIRST_YEAR = 2023   # daily auction prices from here on (daily CO2 input for the spot ensemble)


def eua():
    """Current year every run; earlier years since EUA_FIRST_YEAR once (their reports are final)."""
    this = date.today().year
    for y in range(EUA_FIRST_YEAR, this + 1):
        f = OUT / f"eua_auctions_{y}.xlsx"
        if y < this and f.exists():
            continue
        url = f"https://public.eex-group.com/eex/eua-auction-report/emission-spot-primary-market-auction-report-{y}-data.xlsx"
        r = requests.get(url, headers=UA, timeout=120); r.raise_for_status()
        f.write_bytes(r.content)
        print("eua auctions", y, len(r.content))


def eurusd():
    import csv
    import io
    f = OUT / "eurusd_monthly.csv"
    lines = f.read_text(encoding="utf-8").splitlines()
    last = [l for l in lines if l and not l.startswith("#")][-1].split(",")[0]
    r = requests.get("https://data-api.ecb.europa.eu/service/data/EXR/M.USD.EUR.SP00.A",
                     params={"format": "csvdata", "startPeriod": last}, headers=UA, timeout=60)
    r.raise_for_status()
    this_month = date.today().strftime("%Y-%m")
    new = []
    for row in csv.DictReader(io.StringIO(r.text)):
        m, v = row.get("TIME_PERIOD", ""), row.get("OBS_VALUE", "")
        if len(m) == 7 and last < m < this_month and v:
            x = float(v)
            if not 0.5 < x < 2.5:
                raise ValueError(f"implausible EUR/USD {x} for {m}")
            new.append(f"{m},{x:.4f}")
    if new:
        f.write_text("\n".join(lines + new) + "\n", encoding="utf-8")
    print("eur/usd", "added " + ", ".join(new) if new else "nothing new after " + last)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    ok = True
    for f in (worldbank, eua, eurusd):
        try:
            f()
        except Exception as e:  # keep the other source even if one fails
            print("FAILED", f.__name__, e); ok = False
    sys.exit(0 if ok else 1)
