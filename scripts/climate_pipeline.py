"""
ClimatePulse — Bronze → Silver → Gold pipeline
Fetches NOAA daily data for Houston (IAH) and Newark (EWR),
computes warming trends, and writes public_data_climate_gold.json.

Safety: validates output before writing — never commits bad JSON.
"""

import os, json, requests, numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime

NOAA_TOKEN = os.environ.get("NOAA_TOKEN", "")
STATIONS   = {"IAH": "USW00012960", "EWR": "USW00014734"}
START_YEAR = 1970
END_YEAR   = 2025
OUT_PATH   = Path(__file__).parent.parent / "public_data_climate_gold.json"  # root of repo

# ── Bronze: fetch from NOAA CDO API ─────────────────────────────────────────
def fetch_noaa(station_id, start, end):
    if not NOAA_TOKEN:
        print("No NOAA_TOKEN — skipping live fetch, using existing gold data.")
        return None

    url    = "https://www.ncei.noaa.gov/cdo-web/api/v2/data"
    params = {
        "datasetid":  "GHCND",
        "stationid":  f"GHCND:{station_id}",
        "datatypeid": "TMAX,TMIN",
        "startdate":  f"{start}-01-01",
        "enddate":    f"{end}-12-31",
        "limit":      1000,
        "units":      "standard",
    }
    headers = {"token": NOAA_TOKEN}
    rows, offset = [], 1

    while True:
        params["offset"] = offset
        r = requests.get(url, params=params, headers=headers, timeout=30)
        if r.status_code != 200:
            print(f"NOAA error {r.status_code}: {r.text[:200]}")
            return None
        data = r.json().get("results", [])
        if not data:
            break
        rows.extend(data)
        offset += 1000
        if offset > 100000:
            break

    return pd.DataFrame(rows) if rows else None

# ── Silver: clean and reshape ────────────────────────────────────────────────
def process(df):
    df["date"]  = pd.to_datetime(df["date"])
    df["year"]  = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    tmax = df[df["datatype"] == "TMAX"][["date","year","month","value"]].rename(columns={"value":"tmax"})
    tmin = df[df["datatype"] == "TMIN"][["date","year","month","value"]].rename(columns={"value":"tmin"})
    daily = tmax.merge(tmin, on=["date","year","month"], how="inner")
    daily["tmean"] = (daily["tmax"] + daily["tmin"]) / 2
    return daily

# ── Gold: compute stats ──────────────────────────────────────────────────────
def compute_gold(daily, station_id):
    years = sorted(daily["year"].unique())

    # Yearly aggregates
    yearly = []
    for y in years:
        yd = daily[daily["year"] == y]
        yearly.append({
            "year":       int(y),
            "avg_tmean":  round(float(yd["tmean"].mean()), 2),
            "avg_tmax":   round(float(yd["tmax"].mean()),  2),
            "avg_tmin":   round(float(yd["tmin"].mean()),  2),
            "count_80f":  int((yd["tmax"] >= 80).sum()),
        })
    ydf = pd.DataFrame(yearly)

    # OLS trend on annual mean
    x = np.array(ydf["year"])
    y = np.array(ydf["avg_tmean"])
    m, b = np.polyfit(x - x.mean(), y, 1)
    ydf["trend"] = np.round(m * (x - x.mean()) + y.mean(), 2)
    slope_annual = round(float(m) * 10, 3)

    # Winter lows (Dec–Feb) trend
    winter = daily[daily["month"].isin([12, 1, 2])]
    wyr = winter.groupby("year")["tmin"].mean().reset_index()
    wyr.columns = ["year", "avg_tmin"]
    wx = np.array(wyr["year"])
    wy = np.array(wyr["avg_tmin"])
    wm, _ = np.polyfit(wx - wx.mean(), wy, 1)
    wyr["trend"] = np.round(wm * (wx - wx.mean()) + wy.mean(), 2)
    slope_winter = round(float(wm) * 10, 3)

    # Feb–Mar 80°F heat days trend
    febmar = daily[daily["month"].isin([2, 3])]
    fm_yr = febmar.groupby("year").apply(lambda d: (d["tmax"] >= 80).sum()).reset_index()
    fm_yr.columns = ["year", "count_80f"]
    if len(fm_yr) > 1:
        fx = np.array(fm_yr["year"])
        fy = np.array(fm_yr["count_80f"], dtype=float)
        fm_slope, _ = np.polyfit(fx - fx.mean(), fy, 1)
        slope_80f = round(float(fm_slope) * 10, 3)
    else:
        slope_80f = 0.0

    # Monthly climatology
    monthly = []
    for mo in range(1, 13):
        md = daily[daily["month"] == mo]
        monthly.append({
            "month":     mo,
            "avg_tmax":  round(float(md["tmax"].mean()),  2),
            "avg_tmin":  round(float(md["tmin"].mean()),  2),
            "avg_tmean": round(float(md["tmean"].mean()), 2),
        })

    return {
        "station":          station_id,
        "slope_annual":     slope_annual,
        "slope_winter":     slope_winter,
        "slope_80f_febmar": slope_80f,
        "yearly":           ydf.to_dict(orient="records"),
        "monthly":          monthly,
        "winter":           wyr.to_dict(orient="records"),
    }

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    # Load existing gold as fallback
    existing = None
    if OUT_PATH.exists():
        try:
            existing = json.loads(OUT_PATH.read_text())
            print(f"Loaded existing gold data ({OUT_PATH})")
        except Exception as e:
            print(f"Could not load existing gold: {e}")

    result = {}
    for label, station_id in STATIONS.items():
        print(f"\nProcessing {label} ({station_id})...")
        df_raw = fetch_noaa(station_id, START_YEAR, END_YEAR)

        if df_raw is None or df_raw.empty:
            if existing and label in existing:
                print(f"  Using existing gold data for {label}")
                result[label] = existing[label]
            else:
                print(f"  No data for {label} — skipping")
            continue

        daily = process(df_raw)
        result[label] = compute_gold(daily, station_id)
        print(f"  {label}: {len(daily)} daily rows processed")

    if not result:
        print("No data computed — aborting write.")
        return

    for label in STATIONS:
        if label not in result:
            print(f"Missing {label} — aborting write.")
            return
        if not result[label].get("yearly"):
            print(f"Empty yearly data for {label} — aborting write.")
            return

    result["generated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    OUT_PATH.write_text(json.dumps(result, separators=(',', ':')))
    print(f"\n✅ Written to {OUT_PATH}")
    print(f"   IAH: {result['IAH']['slope_annual']}°F/decade")
    print(f"   EWR: {result['EWR']['slope_annual']}°F/decade")

if __name__ == "__main__":
    main()
