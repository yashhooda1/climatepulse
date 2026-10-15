"""TEMP probe: find Toronto GHCN-D stations via CDO catalog, then measure coverage
of the top candidates through access-services."""
import io, os, json, requests, pandas as pd
cat = requests.get("https://www.ncei.noaa.gov/cdo-web/api/v2/stations",
    headers={"token": os.environ["NOAA_TOKEN"]},
    params={"datasetid": "GHCND", "datatypeid": "TMAX,TMIN",
            "extent": "43.40,-79.90,43.95,-79.10", "startdate": "1970-01-01", "limit": 1000},
    timeout=60)
print("catalog HTTP", cat.status_code, flush=True)
rows = [{"id": s["id"].replace("GHCND:", ""), "name": s["name"], "min": s["mindate"],
         "max": s["maxdate"], "cov": s.get("datacoverage")} for s in cat.json().get("results", [])]
rows.sort(key=lambda r: (r["max"], r["cov"] or 0), reverse=True)
for r in rows[:25]: print(r, flush=True)
URL = "https://www.ncei.noaa.gov/access/services/data/v1"
cands = [r for r in rows if r["min"][:4] <= "1975" and r["max"][:4] >= "2026"][:6]
out = {"catalog": rows[:25], "coverage": {}}
for c in cands:
    r = requests.get(URL, params={"dataset": "daily-summaries", "stations": c["id"],
        "startDate": "1970-01-01", "endDate": "2026-10-15", "dataTypes": "TMAX,TMIN",
        "format": "csv", "units": "standard"}, timeout=180)
    rec = {"name": c["name"], "http": r.status_code}
    if r.status_code == 200 and r.text.strip():
        df = pd.read_csv(io.StringIO(r.text)); df.columns = [x.lower() for x in df.columns]
        if not df.empty and {"tmax", "tmin"} <= set(df.columns):
            df["date"] = pd.to_datetime(df["date"]); both = df.dropna(subset=["tmax", "tmin"])
            py = both.groupby(both["date"].dt.year).size()
            rec.update({"first": str(both["date"].min().date()), "last": str(both["date"].max().date()),
                        "years_1970_2025_330plus": int(py[(py.index >= 1970) & (py.index <= 2025)].ge(330).sum()),
                        "short_years": {int(y): int(n) for y, n in py.items() if n < 330}})
    out["coverage"][c["id"]] = rec
    print(c["id"], json.dumps(rec)[:500], flush=True)
json.dump(out, open("probe_result.json", "w"), indent=1)
