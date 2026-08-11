"""Discover GHCN-D candidates near a city. Usage: py scripts\find_stations.py rome"""
import os, sys, requests

TOKEN = os.environ["NOAA_TOKEN"]

BOXES = {  # south, west, north, east — ~50km around each city
    "rome":   (41.60, 12.10, 42.05, 12.75),
    "delhi":  (28.35, 76.85, 28.90, 77.45),
    "london": (51.30, -0.55, 51.70, 0.20),
}

city = sys.argv[1].lower()
r = requests.get(
    "https://www.ncei.noaa.gov/cdo-web/api/v2/stations",
    headers={"token": TOKEN},
    params={
        "datasetid": "GHCND",
        "datatypeid": "TMAX,TMIN",
        "extent": ",".join(str(v) for v in BOXES[city]),
        "startdate": "1970-01-01",
        "limit": 1000,
    },
    timeout=30,
)
r.raise_for_status()

rows = []
for s in r.json().get("results", []):
    sid = s["id"].replace("GHCND:", "")
    mind, maxd = s["mindate"][:4], s["maxdate"][:10]
    span = int(s["maxdate"][:4]) - int(mind)
    rows.append((s.get("datacoverage", 0), span, maxd, mind, sid, s["name"]))

rows.sort(key=lambda x: (x[2], x[0]), reverse=True)  # freshest first, then coverage
print(f"{'coverage':>9} {'yrs':>4} {'last data':>11}  {'from':>5}  id / name")
for cov, span, maxd, mind, sid, name in rows[:20]:
    print(f"{cov:>9.3f} {span:>4} {maxd:>11}  {mind:>5}  {sid}  {name}")
