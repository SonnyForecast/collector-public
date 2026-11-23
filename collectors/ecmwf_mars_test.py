"""ECMWF MARS Web API access test (23 Nov 2026): one small archive request per dataset class, to see what the key
in secrets ECMWF_API_KEY / ECMWF_API_EMAIL may retrieve. Prints only the outcome (bytes or the ECMWF error text),
never the credentials.

    python collectors/ecmwf_mars_test.py
"""
import json
import os
import traceback
from pathlib import Path

rc = Path.home() / ".ecmwfapirc"
rc.write_text(json.dumps({"url": "https://api.ecmwf.int/v1", "key": os.environ["ECMWF_API_KEY"].strip(),
                          "email": os.environ["ECMWF_API_EMAIL"].strip()}))
rc.chmod(0o600)

from ecmwfapi import ECMWFDataServer, ECMWFService  # noqa: E402

AREA = "48/5/45/11"          # N/W/S/E box around CH
TESTS = {
    # operational IFS high-res forecast, 00Z run of 1 Apr 2023, +24 h and +48 h, 2 m temperature (what we need)
    "mars_oper_fc": ("mars", {"class": "od", "stream": "oper", "type": "fc", "expver": "1", "levtype": "sfc",
                              "date": "2023-04-01", "time": "00", "step": "24/48", "param": "167.128",
                              "area": AREA, "grid": "0.25/0.25"}),
    # TIGGE (free for research, ECMWF control forecast), same day, as a fallback
    "tigge_ecmf_cf": ("tigge", {"class": "ti", "dataset": "tigge", "origin": "ecmf", "type": "cf", "expver": "prod",
                                "levtype": "sfc", "date": "2023-04-01", "time": "00:00:00", "step": "24/48",
                                "param": "167", "area": AREA, "grid": "0.5/0.5"}),
}

for name, (kind, req) in TESTS.items():
    target = f"/tmp/{name}.grib"
    print(f"=== {name}", flush=True)
    try:
        if kind == "mars":
            ECMWFService("mars").execute(req, target)
        else:
            ECMWFDataServer().retrieve({**req, "target": target})
        print(f"OK {name}: {os.path.getsize(target)} bytes")
    except Exception as ex:
        print(f"FAILED {name}: {repr(ex)[:600]}")
        traceback.print_exc(limit=1)
rc.unlink()
