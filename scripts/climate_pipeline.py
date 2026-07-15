"""
ClimatePulse v2 — Bronze → Silver → Gold pipeline
13 stations: Houston, Newark, Delhi, London, Dallas, Denver, Helsinki, Rome, Brussels, Paris, Amsterdam, Chicago, and Los Angeles
Fetches year by year, handles Celsius→Fahrenheit for international stations.
Writes public_data_climate_gold.json at repo root.
"""

import os, json, time, requests, numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime

NOAA_TOKEN = os.environ.get("NOAA_TOKEN", "")
START_YEAR = 1970
END_YEAR   = datetime.utcnow().year
OUT_PATH   = Path(__file__).parent.parent / "public_data_climate_gold.json"

STATIONS = {
    "IAH": {
        "id":       "USW00012960",
        "name":     "Houston, TX",
        "metric":   False,
        "color":    "#f87171",
    },
    "EWR": {
        "id":       "USW00014734",
        "name":     "Newark, NJ",
        "metric":   False,
        "color":    "#60a5fa",
    },
    "DAL": {
        "id":       "USW00013960",
        "name":     "Dallas, TX",
        "metric":   False,
        "color":    "#facc15",
    },
    "DEN": {
        "id":       "USW00003017",
        "name":     "Denver, CO",
        "metric":   False,
        "color":    "#4ade80",
    },
    "LAX": {
        "id":       "USW00023174",
        "name":     "Los Angeles, CA",
        "metric":   False,
        "color":    "#f472b6",
    },
    "HEL": {
        "id":       "FI000000304",
        "name":     "Helsinki, Finland",
        "metric":   True,
        "color":    "#22d3ee",
    },
    "ORD": {
        "id":       "USW00094846",
        "name":     "Chicago, IL",
        "metric":   False,
        "color":    "#2dd4bf",
    },
    "LHR": {
        "id":       "UKM00003772",
        "name":     "London, UK",
        "metric":   True,   # NOAA returns tenths-of-Celsius for this station
        "color":    "#c084fc",
    },
    "AMS": {
        "id":       "NLE00152485",     # Schiphol
        "name":     "Amsterdam, NL",
        "metric":   True,
        "color":    "#a3e635",
    },
    "CDG": {
        "id":       "FRM00007149",     # Orly
        "name":     "Paris, France",
        "metric":   True,
        "color":    "#818cf8",
    },
    "BRU": {
        "id":       "BE000006447",     # Uccle (Royal Observatory)
        "name":     "Brussels, Belgium",
        "metric":   True,
        "color":    "#e879f9",
    },
    "FCO": {
        "id":       "IT000016239",     # Roma Ciampino
        "name":     "Rome, Italy",
        "metric":   True,
        "color":    "#fb7185",
    },
    "DEL": {
        "id":       "IN022021900",
        "name":     "Delhi, India",
        "metric":   True,
        "color":    "#fb923c",
    },
}

def c_to_f(c): return round(c * 9/5 + 32, 2)


def compute_heat_ytd(daily, latest_year):
    """Heat-days year-to-date for latest_year, paced by day-of-year vs prior years.
    Returns None if the latest year is effectively complete or too sparse."""
    import numpy as np
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"])
    d["doy"]  = d["date"].dt.dayofyear
 
    ld = d[d["year"] == latest_year]
    if ld.empty:
        return None
    cutoff_doy = int(ld["doy"].max())
    # if the latest year is basically complete, there's no "pace" to show
    if cutoff_doy >= 365:
        return None
 
    so_far = int((ld["tmax"] >= 80).sum())
 
    prior       = d[(d["year"] < latest_year) & (d["doy"] <= cutoff_doy)]
    same_window = prior[prior["tmax"] >= 80].groupby("year").size()
    full        = (d[d["year"] < latest_year]
                   .assign(hot=lambda x: x["tmax"] >= 80)
                   .groupby("year")["hot"].sum())
 
    last_year = latest_year - 1
    vs_last = (so_far - int(same_window.loc[last_year])
               if last_year in same_window.index else None)
 
    ratios = [full.loc[y] / same_window.loc[y]
              for y in same_window.index
              if same_window.loc[y] >= 5 and y in full.index]
    projected = round(so_far * float(np.mean(ratios))) if ratios else None
 
    return {
        "year":                   latest_year,
        "count_so_far":           so_far,
        "through_doy":            cutoff_doy,
        "last_date":              ld["date"].max().strftime("%Y-%m-%d"),
        "vs_last_year_same_date": vs_last,
        "last_year_same_date":    (int(same_window.loc[last_year])
                                   if last_year in same_window.index else None),
        "projected_full_year":    projected,
        "partial":                True,
    }

# ── Bronze: fetch one year at a time ────────────────────────────────────────
def fetch_year(station_id, year, metric=False):
    url     = "https://www.ncei.noaa.gov/cdo-web/api/v2/data"
    headers = {"token": NOAA_TOKEN}
    rows, offset = [], 1

    while True:
        params = {
            "datasetid":  "GHCND",
            "stationid":  f"GHCND:{station_id}",
            "datatypeid": "TMAX,TMIN",
            "startdate":  f"{year}-01-01",
            "enddate":    f"{year}-12-31",
            "limit":      1000,
            "offset":     offset,
            "units":      "standard",  # always request standard — we handle conversion manually
        }
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, headers=headers, timeout=30)
                if r.status_code == 429:
                    time.sleep(5)
                    continue
                if r.status_code != 200:
                    print(f"    NOAA {r.status_code} for {year}")
                    return rows
                data = r.json().get("results", [])
                rows.extend(data)
                if len(data) < 1000:
                    return rows
                offset += 1000
                time.sleep(0.25)
                break
            except Exception as e:
                print(f"    Retry {attempt+1} for {year}: {e}")
                time.sleep(2)
    return rows

def fetch_noaa(station_cfg):
    if not NOAA_TOKEN:
        print("  No NOAA_TOKEN — using existing gold data.")
        return None

    all_rows = []
    for year in range(START_YEAR, END_YEAR + 1):
        rows = fetch_year(station_cfg["id"], year, station_cfg["metric"])
        all_rows.extend(rows)
        print(f"    {year}: {len(rows)} records")
        time.sleep(0.5)

    return pd.DataFrame(all_rows) if all_rows else None

# ── Silver: clean, reshape ──────────────────────────────────────────────────
# NOTE: we request units=standard from NOAA, which returns Fahrenheit for
# ALL stations including international ones. No manual conversion needed.
def process(df, metric=False):
    df["date"]  = pd.to_datetime(df["date"])
    df["year"]  = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    tmax  = df[df["datatype"] == "TMAX"][["date","year","month","value"]].rename(columns={"value":"tmax"})
    tmin  = df[df["datatype"] == "TMIN"][["date","year","month","value"]].rename(columns={"value":"tmin"})
    daily = tmax.merge(tmin, on=["date","year","month"], how="inner")
    daily["tmean"] = (daily["tmax"] + daily["tmin"]) / 2

    # Sanity guard: drop physically impossible values (bad NOAA records)
    daily = daily[(daily["tmax"] > -60) & (daily["tmax"] < 140)]
    daily = daily[(daily["tmin"] > -80) & (daily["tmin"] < 120)]
    return daily

# ── Gold: compute stats ──────────────────────────────────────────────────────
def compute_gold(daily, station_cfg):
    years = sorted(daily["year"].unique())

    yearly = []
    for y in years:
        yd = daily[daily["year"] == y]
        if len(yd) < 30:
            continue
        # An annual mean needs a full year. Without this, the in-progress current
        # year (and any station whose feed stops mid-year, e.g. Rome/Ciampino)
        # produces a biased average — a fake "cooling" dip at the end of the trend
        # line that also drags the regression slope down.
        if yd["month"].nunique() < 12:
            continue
        yearly.append({
            "year":      int(y),
            "avg_tmean": round(float(yd["tmean"].mean()), 2),
            "avg_tmax":  round(float(yd["tmax"].mean()),  2),
            "avg_tmin":  round(float(yd["tmin"].mean()),  2),
            "count_80f": int((yd["tmax"] >= 80).sum()),
        })
    ydf = pd.DataFrame(yearly)

    MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    ytd = None
    all_years = sorted(int(y) for y in daily["year"].unique())
    heat_ytd = compute_heat_ytd(daily, all_years[-1]) if all_years else None
    if all_years:
        latest = all_years[-1]
        ld = daily[daily["year"] == latest]
        # incomplete = missing a month (in-progress year OR a stale feed like Rome)
        if ld["month"].nunique() < 12 and len(ld) >= 30:
            through = int(ld["month"].max())
            ytd = {
                "year":          latest,
                "avg_tmean":     round(float(ld["tmean"].mean()), 2),
                "count_80f":     int((ld["tmax"] >= 80).sum()),
                "days":          int(len(ld)),
                "through_month": through,
                "through_label": MONTH_NAMES[through - 1],
                "last_date":     ld["date"].max().strftime("%Y-%m-%d"),
                "partial":       True,
            }

    x = np.array(ydf["year"])
    y = np.array(ydf["avg_tmean"])
    m, _ = np.polyfit(x - x.mean(), y, 1)
    ydf["trend"] = np.round(m * (x - x.mean()) + y.mean(), 2)
    slope_annual = round(float(m) * 10, 3)

    winter = daily[daily["month"].isin([12, 1, 2])]
    wyr    = winter.groupby("year")["tmin"].mean().reset_index()
    wyr.columns = ["year", "avg_tmin"]
    wx, wy = np.array(wyr["year"]), np.array(wyr["avg_tmin"])
    wm, _  = np.polyfit(wx - wx.mean(), wy, 1)
    wyr["trend"] = np.round(wm * (wx - wx.mean()) + wy.mean(), 2)
    slope_winter = round(float(wm) * 10, 3)

    febmar = daily[daily["month"].isin([2, 3])]
    fm_yr  = febmar.groupby("year").apply(lambda d: int((d["tmax"] >= 80).sum())).reset_index()
    fm_yr.columns = ["year", "count_80f"]
    slope_80f = 0.0
    if len(fm_yr) > 1:
        fx, fy = np.array(fm_yr["year"]), np.array(fm_yr["count_80f"], dtype=float)
        fm_slope, _ = np.polyfit(fx - fx.mean(), fy, 1)
        slope_80f   = round(float(fm_slope) * 10, 3)

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
        "station":          station_cfg["id"],
        "name":             station_cfg["name"],
        "color":            station_cfg["color"],
        "slope_annual":     slope_annual,
        "slope_winter":     slope_winter,
        "slope_80f_febmar": slope_80f,
        "yearly":           ydf.to_dict(orient="records"),
        "ytd":              ytd,
        "heat_ytd":         heat_ytd, 
        "monthly":          monthly,
        "winter":           wyr.to_dict(orient="records"),
    }

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    existing = None
    if OUT_PATH.exists():
        try:
            existing = json.loads(OUT_PATH.read_text())
            print(f"Loaded existing gold ({OUT_PATH})")
        except Exception as e:
            print(f"Could not load existing: {e}")

    result = {}
    for code, cfg in STATIONS.items():
        print(f"\n── {code} — {cfg['name']} ──")
        df_raw = fetch_noaa(cfg)

        if df_raw is None or df_raw.empty:
            if existing and code in existing:
                print(f"  Keeping existing data for {code}")
                result[code] = existing[code]
            else:
                print(f"  No data for {code} — skipping")
            continue

        daily = process(df_raw, cfg["metric"])
        result[code] = compute_gold(daily, cfg)
        years = sorted(daily["year"].unique())
        print(f"  {code}: {len(daily)} rows, {years[0]}–{years[-1]}")

    if not result:
        print("Nothing computed — aborting.")
        return

    # Validate all stations present
    for code in STATIONS:
        if code not in result:
            print(f"Missing {code} — aborting.")
            return
        if not result[code].get("yearly"):
            print(f"Empty yearly for {code} — aborting.")
            return

    result["generated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    result["station_codes"] = list(STATIONS.keys())
    OUT_PATH.write_text(json.dumps(result, separators=(',', ':')))
    print(f"\n✅ Written to {OUT_PATH}")
    for code in STATIONS:
        last = result[code]['yearly'][-1]['year']
        print(f"   {code}: {result[code]['slope_annual']}°F/decade, latest year: {last}")

if __name__ == "__main__":
    main()
