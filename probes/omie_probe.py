"""Probe OMIE public files (curva_pbc = aggregated day-ahead bid curves, marginalpdbc = prices): URL patterns,
encoding, columns, before and after the 15-min switch (1 Oct 2025). Prints heads and line counts only."""
import io, zipfile, requests
U = "https://www.omie.es/es/file-download?parents%5B0%5D={p}&filename={p}_{d}.1"
for p, d in (("marginalpdbc", "20240501"), ("curva_pbc", "20240501"), ("curva_pbc", "20251102"), ("marginalpdbc", "20251102")):
    r = requests.get(U.format(p=p, d=d), timeout=120)
    print(p, d, r.status_code, r.headers.get("content-type"), len(r.content), "bytes")
    raw = r.content
    if raw[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(raw)); print(" zip:", z.namelist()[:3]); raw = z.read(z.namelist()[0])
    t = raw.decode("latin-1")
    lines = t.splitlines()
    print(" lines", len(lines)); print("\n".join(lines[:6])); print(" ...", lines[len(lines)//2][:200]); print(" last:", lines[-2:])
