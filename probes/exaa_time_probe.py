"""When are tomorrow's EXAA 10:15 auction prices published, and where first? Run every ~5 min from 10:00 to 14:00
Zurich time for a few days (started by a Modal cron in spot-ensemble, CH/modal/app.py exaa_probe_cron). Decides
whether a 10:30 issue with EXAA is possible from free sources (spot-ensemble task 8).

One line per source and zone:  PROBE <now UTC> <source> <zone> delivery <D> values <n> [first prices]
Sources: exaa.at public JSON (www.exaa.at/data/trading-results, the site's own data endpoint), ENTSO-E A44
sequence 2 (what our collector uses), APG transparency (AT; endpoint tried, raw answer logged until it is known)."""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import requests

sys.path.insert(0, "collectors")
import entsoe_api  # noqa: E402
from entsoe_api import EIC  # noqa: E402

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
D = date.today() + timedelta(days=1)
UA = {"User-Agent": "Mozilla/5.0 (research probe; office contact via GitHub SonnyForecast)"}


def say(source, zone, n, first="", note=""):
    print(f"PROBE {NOW} {source} {zone} delivery {D} values {n} {first} {note}".rstrip(), flush=True)


def exaa_site():
    try:
        idx = requests.get("https://www.exaa.at/data/trading-results", headers=UA, timeout=30).json()
        days = idx.get("days", [])
        print("exaa.at delivery days offered:", days[:3])
    except Exception as e:
        say("exaa.at", "-", -1, note=f"index failed {str(e)[:80]}"); return
    for zone in ("AT", "DE"):
        try:
            j = requests.get("https://www.exaa.at/data/trading-results", headers=UA, timeout=30,
                             params={"delivery_day": str(D), "market": zone, "auction": "grey"}).json()
            h = (j or {}).get("data", {}).get("h", []) if isinstance(j, dict) else []
            say("exaa.at", zone, len(h), [x.get("Price") for x in h[:3]] if h else "",
                f"auction_day {j.get('auction_day')}" if isinstance(j, dict) else "")
        except Exception as e:
            say("exaa.at", zone, -1, note=f"failed {str(e)[:80]}")


def entsoe():
    tok = os.environ.get("ENTSOE_TOKEN", "").strip()
    if not tok:
        say("entsoe", "-", -1, note="no token"); return
    for zone in ("AT", "DE_LU"):
        p = {"documentType": "A44", "in_Domain": EIC[zone], "out_Domain": EIC[zone],
             "contract_MarketAgreement.type": "A01", "classificationSequence_AttributeInstanceComponent.position": "2",
             "periodStart": D.strftime("%Y%m%d") + "0000", "periodEnd": (D + timedelta(days=1)).strftime("%Y%m%d") + "0000"}
        try:
            x = entsoe_api.fetch(tok, p, tries=2)
            if not x:
                say("entsoe", zone, 0); continue
            txt = x.decode() if isinstance(x, bytes) else str(x)
            import re
            q = re.findall(r"<price\.amount>([^<]+)</price\.amount>", txt)
            say("entsoe", zone, len(q), q[:3])
        except Exception as e:
            say("entsoe", zone, -1, note=f"failed {str(e)[:100]}")


def apg():
    # Endpoint not confirmed yet: try the transparency API pattern and log the raw answer briefly.
    d0, d1 = D.strftime("%Y-%m-%dT000000"), (D + timedelta(days=1)).strftime("%Y-%m-%dT000000")
    for url in (f"https://transparency.apg.at/api/v1/EXAAD1P/Data/English/M15/{d0}/{d1}",
                f"https://transparency.apg.at/transparency-api/api/v1/Data/EXAAD1P/English/M15/{d0}/{d1}"):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            body = r.text
            n = body.count("\"V\"") if r.ok else -1
            say("apg", "AT", n, note=f"HTTP {r.status_code} {url.split('apg.at')[1][:60]} | {body[:160]!r}")
        except Exception as e:
            say("apg", "AT", -1, note=f"failed {str(e)[:80]}")


if __name__ == "__main__":
    print("now UTC", NOW, "| delivery", D)
    exaa_site()
    entsoe()
    apg()
