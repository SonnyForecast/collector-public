"""Shared ENTSO-E Transparency Platform REST helpers (token from the ENTSOE_TOKEN secret).

fetch()        GET with retries (429, 5xx, 599 gateway timeouts); returns bytes, or None for "no matching data".
               Zip answers (outage documents) are returned as a list of XML byte strings.
parse_series() TimeSeries/Period/Point documents (load, flows, capacities) -> hourly pd.Series (UTC, interval start).
"""
import io
import re
import time
import zipfile
import xml.etree.ElementTree as ET

import pandas as pd
import requests

API = "https://web-api.tp.entsoe.eu/api"
EIC = {"CH": "10YCH-SWISSGRIDZ", "DE_LU": "10Y1001A1001A82H", "FR": "10YFR-RTE------C",
       "IT_NORTH": "10Y1001A1001A73I", "AT": "10YAT-APG------L", "IT": "10YIT-GRTN-----B"}


def _scrub(msg):
    """Never let the security token reach a log (the logs of the public collector repo are public)."""
    return re.sub(r"securityToken=[^&\s'\"]+", "securityToken=***", str(msg))


def fetch(token, params, tries=6):
    params = dict(params, securityToken=token)
    last = ""
    for i in range(tries):
        try:
            r = requests.get(API, params=params, timeout=180)
        except requests.RequestException as e:
            last = _scrub(e); time.sleep(10 * (i + 1)); continue
        if r.status_code == 200:
            if r.content[:2] == b"PK":                       # zip of XML documents (outages)
                z = zipfile.ZipFile(io.BytesIO(r.content))
                return [z.read(n) for n in z.namelist()]
            if b"Acknowledgement_MarketDocument" in r.content[:400]:
                return None if b"No matching data" in r.content else _raise(r)
            return r.content
        if b"No matching data" in r.content:
            return None
        if r.status_code in (429, 500, 502, 503, 504, 599):
            last = f"{r.status_code}"; time.sleep(15 * (i + 1)); continue
        _raise(r)
    raise RuntimeError(f"ENTSO-E: gave up after {tries} tries ({last})")


def _raise(r):
    reason = re.findall(r"<text>(.*?)</text>", r.text, re.S)
    raise RuntimeError(_scrub(f"ENTSO-E {r.status_code}: {reason[0].strip()[:300] if reason else r.text[-300:]}"))


def local(tag):
    return tag.rsplit("}", 1)[-1]


def find(el, name):
    """First descendant whose local tag name is `name` (namespace-agnostic)."""
    for x in el.iter():
        if local(x.tag) == name:
            return x
    return None


def children(el, name):
    return [x for x in el if local(x.tag) == name]


def parse_series(xml_bytes) -> pd.Series:
    if not xml_bytes:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    root = ET.fromstring(xml_bytes)
    vals = {}
    for ts in children(root, "TimeSeries"):
        for per in children(ts, "Period"):
            start = pd.Timestamp(find(per, "start").text)
            res = find(per, "resolution").text                    # PT15M / PT30M / PT60M
            step = pd.Timedelta(minutes=int(res[2:-1]))
            for p in children(per, "Point"):
                t = start + (int(find(p, "position").text) - 1) * step
                q = find(p, "quantity")
                vals[t] = float(q.text)
    s = pd.Series(vals, dtype=float).sort_index()
    return s.resample("1h").mean() if len(s) else s
