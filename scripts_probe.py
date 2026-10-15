"""TEMP probe: per-candidate station coverage straight from NOAA (no parsing guesses)."""
import io, json, requests, pandas as pd
URL = "https://www.ncei.noaa.gov/access/services/data/v1"
CANDIDATES = {
    "USW00012815": "Orlando Intl (MCO)",
    "USW00012841": "Orlando Executive",
    "USW00026451": "Anchorage Ted Stevens Intl",
    "CA006158733": "Toronto Pearson (old ID)",
    "CA006158731": "Toronto Pearson (alt ID)",
    "CA006158355": "Toronto (city)",
    "CA006158359": "Toronto City Centre",
}
out = {}
for sid, label in CANDIDATES.items():
    r = requests.get(URL, params={"dataset": "daily-summaries", "stations": sid,
        "startDate": "1970-01-01", "endDate": "2026-10-15", "dataTypes": "TMAX,TMIN",
        "format": "csv", "units": "standard", "includeStationName": "true"}, timeout=180)
    rec = {"label": label, "http": r.status_code}
    if r.status_code == 200 and r.text.strip():
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = [c.lower() for c in df.columns]
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"]); df["year"] = df["date"].dt.year
            both = df.dropna(subset=["tmax", "tmin"])
            per_year = both.groupby("year").size()
            rec.update({
                "noaa_name": str(df["name"].iloc[0]) if "name" in df else None,
                "first": str(both["date"].min().date()) if len(both) else None,
                "last": str(both["date"].max().date()) if len(both) else None,
                "years_with_330plus_days": int((per_year >= 330).sum()),
                "years_1970_2025_with_330plus": int(per_year[(per_year.index >= 1970) & (per_year.index <= 2025)].ge(330).sum()),
                "thin_years": {int(y): int(n) for y, n in per_year.items() if n < 330},
            })
    out[sid] = rec
    print(sid, json.dumps(rec)[:400], flush=True)
json.dump(out, open("probe_result.json", "w"), indent=1)
