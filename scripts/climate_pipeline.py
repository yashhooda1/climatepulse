"""
ClimatePulse v2 — Bronze → Silver → Gold pipeline
13 stations: Houston, Newark, Delhi, London, Dallas, Denver, Helsinki, Rome,
Brussels, Paris, Amsterdam, Chicago, and Los Angeles.

Incremental by default: years <= STABLE_THROUGH are read from cache/*.parquet,
only the trailing 2-year window is refetched from NOAA CDO. Set FULL_REFRESH=true
to ignore the cache and refetch 1970–present (runs monthly to pick up revisions).

Writes public_data_climate_gold.json at repo root.
"""

import os, sys, json, time, requests, numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime, timezone

NOAA_TOKEN = os.environ.get("NOAA_TOKEN", "")
START_YEAR = 1970
END_YEAR   = datetime.now(timezone.utc).year

ROOT       = Path(__file__).parent.parent
OUT_PATH   = ROOT / "public_data_climate_gold.json"
CACHE_DIR  = ROOT / "cache"

FULL_REFRESH   = os.environ.get("FULL_REFRESH", "").lower() == "true"
STABLE_THROUGH = END_YEAR - 2          # cached; anything after is refetched every run
MAX_LAG_DAYS   = 75                    # international: covers normal EU exchange latency
MAX_LAG_US     = 14                    # U.S. ASOS sites update within days — anything more is a fault
KNOWN_STALE = {"FCO", "DEL"} # dead upstream since Aug 2025 — shrink as fixed
MIN_DAYS_PER_MONTH = 12   # a monthly mean needs ~40% of the month to be stable

# ── NOAA quota guard ─────────────────────────────────────────────────────────
# CDO allows 1,000 requests/day. A per-second 429 clears in seconds; a daily-cap
# 429 does not clear at all. We back off 5s → 20s → 60s; if it's still 429 the
# cap is gone, and we stop fetching for the rest of the run instead of
# silently caching partial years.
QUOTA_BACKOFF_S = (5, 20, 60)
QUOTA_EXHAUSTED = False
NOAA_CALLS      = 0

class QuotaExhausted(Exception):
    pass

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
        "id":       "USW00003017",     # Denver Intl — GHCN-D record starts 1996
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
        "id":       "UKE00105900",
        "name":     "London, UK",
        "metric":   True,
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

DAILY_COLS = ["date", "year", "month", "tmax", "tmin", "tmean"]

GHCN_COUNTRY = {
    "US": "United States", "UK": "United Kingdom", "NL": "Netherlands",
    "BE": "Belgium",       "FR": "France",         "IT": "Italy",
    "FI": "Finland",       "IN": "India",
}
 
# Curated, human-readable context. Kept short: the UI shows this under the row.
STATION_NOTES = {
    "DEN": "GHCN-D record begins 1996 — 29 complete years vs 56 elsewhere.",
    "BRU": "Uccle: record since 1833, but 19 years rejected — trend fits 38 of 56.",
    "HEL": "Kaisaniemi, 60°N — the high-latitude amplification case.",
    "FCO": "Ciampino: last complete year 2011, feed dead since Aug 2025.",
    "DEL": "0.79 coverage, 12 years rejected; NCEI relay dead since Aug 2025.",
    "LHR": "Swapped to UKE00105900 after the Heathrow feed thinned out.",
}
 
CATALOG_URL = ("https://www.ncei.noaa.gov/access/search/data-search/"
               "daily-summaries?stations={sid}")
 
 
def build_station_roster(result):
    """UI-ready per-station status list. Call AFTER the freshness audit, so
    last_data_date / lag_days / stale are already populated on each block."""
    roster = []
    for code, cfg in STATIONS.items():
        blk = result.get(code)
        if not blk or not blk.get("yearly"):
            continue
 
        yrs = blk["yearly"]
        cov = [y["coverage"] for y in yrs if y.get("coverage") is not None]
 
        # Three states, not a boolean. "stale" is an alert; "known_stale" is a
        # documented dead upstream we've already triaged — the UI should not
        # flag it red every run, but it must not read as healthy either.
        if blk.get("stale"):
            status = "known_stale" if code in KNOWN_STALE else "stale"
        else:
            status = "active"
 
        sid = blk["station"]
        roster.append({
            "code":            code,
            "ghcn_id":         sid,
            "name":            blk["name"],
            "country":         GHCN_COUNTRY.get(sid[:2], sid[:2]),
            "color":           blk["color"],
            "status":          status,
            "active":          status == "active",
            "last_data_date":  blk["last_data_date"],
            "lag_days":        blk["lag_days"],
            "record_start":    yrs[0]["year"],
            "record_end":      yrs[-1]["year"],
            "complete_years":  blk["n_years"],
            "rejected_years":  len(blk.get("rejected_years") or []),
            "mean_coverage":   round(sum(cov) / len(cov), 3) if cov else None,
            "slope_annual":    blk["slope_annual"],
            "trend_reliable":  blk.get("trend_reliable", False),
            "note":            STATION_NOTES.get(code),
            "catalog_url":     CATALOG_URL.format(sid=sid),
        })
 
    # Healthy first, then by how far behind they are — the list doubles as a
    # triage queue.
    roster.sort(key=lambda r: (r["status"] != "active", r["lag_days"]))
    return roster
 
 
def summarize_roster(roster):
    counts = {"active": 0, "stale": 0, "known_stale": 0}
    for r in roster:
        counts[r["status"]] += 1
    return {
        "total":           len(roster),
        **counts,
        "countries":       len({r["country"] for r in roster}),
        "record_start":    min(r["record_start"] for r in roster),
        "record_end":      max(r["record_end"] for r in roster),
        "station_years":   sum(r["complete_years"] for r in roster),
        "max_lag_days":    max(r["lag_days"] for r in roster),
        "freshest":        min(roster, key=lambda r: r["lag_days"])["code"],
        "lag_threshold":   MAX_LAG_DAYS,
    }


# ── Cache ────────────────────────────────────────────────────────────────────
def load_cache(code, station_id):
    """Return cached daily rows for years <= STABLE_THROUGH, or None.
    Invalidates the cache if it was built from a different station ID."""
    p = CACHE_DIR / f"{code}.parquet"
    if FULL_REFRESH or not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        cached_id = df["station_id"].iloc[0] if "station_id" in df.columns else None
        if cached_id != station_id:
            print(f"  cache built from {cached_id!r}, config says {station_id!r} "
                  f"— discarding and refetching")
            return None
        df["date"] = pd.to_datetime(df["date"])
        df = df[df["year"] <= STABLE_THROUGH]
        return df if not df.empty else None
    except Exception as e:
        print(f"  cache unreadable for {code}: {e} — full refetch")
        return None


def save_cache(code, daily, station_id):
    """Persist only the stable (immutable) portion of the series."""
    CACHE_DIR.mkdir(exist_ok=True)
    stable = daily[daily["year"] <= STABLE_THROUGH].sort_values("date").copy()
    if stable.empty:
        return
    stable["station_id"] = station_id
    stable[DAILY_COLS + ["station_id"]].to_parquet(CACHE_DIR / f"{code}.parquet",
                                                    index=False)

## Bronze layer
def fetch_year(station_id, year):
    global NOAA_CALLS
    url     = "https://www.ncei.noaa.gov/cdo-web/api/v2/data"
    headers = {"token": NOAA_TOKEN}
    rows, offset = [], 1
    rate_limited = 0

    while True:
        params = {
            "datasetid":  "GHCND",
            "stationid":  f"GHCND:{station_id}",
            "datatypeid": "TMAX,TMIN",
            "startdate":  f"{year}-01-01",
            "enddate":    f"{year}-12-31",
            "limit":      1000,
            "offset":     offset,
            "units":      "standard",   # CDO returns °F for all stations
        }

        page = None
        for attempt in range(3):
            try:
                NOAA_CALLS += 1
                r = requests.get(url, params=params, headers=headers, timeout=30)
                if r.status_code == 429:
                    if rate_limited >= len(QUOTA_BACKOFF_S):
                        raise QuotaExhausted(f"{station_id} {year}: still 429 after "
                                             f"{sum(QUOTA_BACKOFF_S)}s backoff — daily cap likely hit")
                    wait = QUOTA_BACKOFF_S[rate_limited]
                    rate_limited += 1
                    print(f"    NOAA 429 — backing off {wait}s ({rate_limited}/{len(QUOTA_BACKOFF_S)})")
                    time.sleep(wait)
                    continue
                if r.status_code != 200:
                    print(f"    NOAA {r.status_code} for {year}")
                    return rows
                page = r.json().get("results", [])
                break
            except QuotaExhausted:
                raise
            except Exception as e:
                print(f"    Retry {attempt + 1} for {year}: {e}")
                time.sleep(2)

        # Bounded: 3 failed attempts ends the year instead of looping forever.
        if page is None:
            print(f"    Giving up on {year} after 3 attempts")
            return rows

        rows.extend(page)
        if len(page) < 1000:
            return rows
        offset += 1000
        time.sleep(0.25)


def fetch_noaa(station_cfg, years):
    if not NOAA_TOKEN:
        print("  No NOAA_TOKEN — skipping fetch.")
        return None

    global QUOTA_EXHAUSTED
    if QUOTA_EXHAUSTED:
        print("  NOAA quota exhausted earlier this run — skipping fetch.")
        return None

    all_rows = []
    for year in years:
        try:
            rows = fetch_year(station_cfg["id"], year)
        except QuotaExhausted as e:
            # Discard this station's partial rows entirely: a half-fetched year
            # must never reach the cache or the gold file.
            print(f"  ✗ {e}")
            QUOTA_EXHAUSTED = True
            return None
        all_rows.extend(rows)
        print(f"    {year}: {len(rows)} records")
        time.sleep(0.5)

    return pd.DataFrame(all_rows) if all_rows else None


# ── Silver: clean, reshape ───────────────────────────────────────────────────
# We request units=standard from NOAA, which returns Fahrenheit for ALL
# stations including international ones. No manual conversion needed — the
# per-station "metric" flag is retained for documentation only.
def process(df):
    df = df.copy()
    df["date"]  = pd.to_datetime(df["date"])
    df["year"]  = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    cols  = ["date", "year", "month", "value"]
    tmax  = df[df["datatype"] == "TMAX"][cols].rename(columns={"value": "tmax"})
    tmin  = df[df["datatype"] == "TMIN"][cols].rename(columns={"value": "tmin"})
    daily = tmax.merge(tmin, on=["date", "year", "month"], how="inner")
    daily["tmean"] = (daily["tmax"] + daily["tmin"]) / 2

    # Sanity guard: drop physically impossible values (bad NOAA records)
    daily = daily[(daily["tmax"] > -60) & (daily["tmax"] < 140)]
    daily = daily[(daily["tmin"] > -80) & (daily["tmin"] < 120)]
    return daily[DAILY_COLS]


def compute_heat_ytd(daily, latest_year):
    """Heat-days year-to-date for latest_year, paced by day-of-year vs prior years.
    Returns None if the latest year is effectively complete or too sparse."""
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

def monthly_stats(yd):
    """Per-month aggregates for one calendar year."""
    return yd.groupby("month").agg(
        days      = ("tmean", "size"),
        avg_tmean = ("tmean", "mean"),
        avg_tmax  = ("tmax",  "mean"),
        avg_tmin  = ("tmin",  "mean"),
        hot_days  = ("tmax",  lambda s: int((s >= 80).sum())),
    )


def year_is_usable(ms):
    return len(ms) == 12 and bool((ms["days"] >= MIN_DAYS_PER_MONTH).all())


# ── Gold: compute stats ──────────────────────────────────────────────────────
def compute_gold(daily, station_cfg):
    years = sorted(daily["year"].unique())

    yearly, rejected = [], []
    for y in years:
        yd = daily[daily["year"] == y]
        ms = monthly_stats(yd)
        if not year_is_usable(ms):
            rejected.append(int(y))
            continue
        days = int(ms["days"].sum())
        yearly.append({
            "year":      int(y),
            # Equal weight per month, NOT per day. A year that under-samples
            # winter no longer reads as warmer than one that doesn't — this is
            # the fix for the inflated BRU/FCO slopes.
            "avg_tmean": round(float(ms["avg_tmean"].mean()), 2),
            "avg_tmax":  round(float(ms["avg_tmax"].mean()),  2),
            "avg_tmin":  round(float(ms["avg_tmin"].mean()),  2),
            "count_80f": int(ms["hot_days"].sum()),
            "days":      days,
            "coverage":  round(days / 365.25, 3),
        })
    if rejected:
        print(f"  dropped {len(rejected)} low-coverage yrs: {rejected}")

    ydf = pd.DataFrame(yearly)
    if ydf.empty:
        raise ValueError(
            f"{station_cfg['name']}: no complete calendar years — cannot fit a trend"
        )

    MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
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

    x  = np.array(ydf["year"], dtype=float)
    y  = np.array(ydf["avg_tmean"], dtype=float)
    xc = x - x.mean()

    if len(x) >= 4:
        coef, cov = np.polyfit(xc, y, 1, cov=True)
        m, se = float(coef[0]), float(np.sqrt(cov[0, 0]))
    else:
        m, se = float(np.polyfit(xc, y, 1)[0]), float("nan")

    ydf["trend"]      = np.round(m * xc + y.mean(), 2)
    slope_annual      = round(m * 10, 3)
    slope_annual_se   = None if np.isnan(se) else round(se * 10, 3)
    slope_annual_ci95 = (None if np.isnan(se)
                         else [round((m - 1.96 * se) * 10, 3),
                               round((m + 1.96 * se) * 10, 3)])

    MIN_TREND_YEARS = 25
    trend_reliable = len(ydf) >= MIN_TREND_YEARS

    w = daily[daily["month"].isin([12, 1, 2])].copy()
    # Dec belongs to the FOLLOWING year's DJF season
    w["winter_year"] = w["year"] + (w["month"] == 12).astype(int)
    wg = (w.groupby("winter_year")
            .agg(months=("month", "nunique"),
                 days=("tmin", "size"),
                 avg_tmin=("tmin", "mean")))
    wg = wg[(wg["months"] == 3) & (wg["days"] >= 3 * MIN_DAYS_PER_MONTH)]

    if len(wg) > 1:
        wyr = (wg.reset_index()
                 .rename(columns={"winter_year": "year"})[["year", "avg_tmin"]])
        wx, wy       = np.array(wyr["year"], dtype=float), np.array(wyr["avg_tmin"])
        wm, _        = np.polyfit(wx - wx.mean(), wy, 1)
        wyr["trend"] = np.round(wm * (wx - wx.mean()) + wy.mean(), 2)
        slope_winter = round(float(wm) * 10, 3)
    else:
        wyr, slope_winter = pd.DataFrame(columns=["year", "avg_tmin", "trend"]), 0.0

    good_years = set(ydf["year"])
    febmar = daily[daily["month"].isin([2, 3]) & daily["year"].isin(good_years)]
    fm_yr  = (febmar.assign(hot=febmar["tmax"] >= 80)
                    .groupby("year")["hot"].sum().reset_index())
    fm_yr.columns = ["year", "count_80f"]
    slope_80f = 0.0
    if len(fm_yr) > 1:
        fx, fy      = np.array(fm_yr["year"]), np.array(fm_yr["count_80f"], dtype=float)
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
        "slope_annual_se":   slope_annual_se,
        "slope_annual_ci95": slope_annual_ci95,
        "trend_reliable": trend_reliable,
        "n_years":           int(len(ydf)),
        "rejected_years":    rejected,
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
    mode = "FULL REFRESH" if FULL_REFRESH else f"incremental (cache ≤{STABLE_THROUGH})"
    print(f"ClimatePulse — {mode}, target year {END_YEAR}")

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
        cached = load_cache(code, cfg["id"])
        if cached is not None:
            cached = cached[DAILY_COLS]          # drop station_id before concat
            cached_max = int(cached["year"].max())
            years = list(range(cached_max + 1, END_YEAR + 1))
            print(f"  cache: {len(cached)} rows through {cached_max} — fetching {years}")
        else:
            years = list(range(START_YEAR, END_YEAR + 1))
            print(f"  cache miss — full fetch {START_YEAR}–{END_YEAR}")

        df_raw = fetch_noaa(cfg, years)

        # Quota hit: keep last run's gold block for this station untouched.
        # Nothing is cached, so the next run with quota refetches cleanly.
        if QUOTA_EXHAUSTED:
            if existing and code in existing:
                print(f"  Quota exhausted — carrying forward existing gold block for {code}")
                result[code] = dict(existing[code], quota_truncated=True)
            else:
                print(f"  Quota exhausted and no existing block for {code} — skipping")
            continue

        fresh  = (process(df_raw)
                  if df_raw is not None and not df_raw.empty else None)

        if cached is not None and fresh is not None:
            daily = pd.concat([cached, fresh], ignore_index=True)
        elif cached is not None:
            print("  no new rows — cache only (feed may be dead)")
            daily = cached
        elif fresh is not None:
            daily = fresh
        else:
            if existing and code in existing:
                print(f"  Keeping existing gold block for {code}")
                result[code] = existing[code]
            else:
                print(f"  No data for {code} — skipping")
            continue

        # Refetched rows win over cached ones, so NOAA revisions inside the
        # trailing window are applied correctly.
        daily = (daily.drop_duplicates(subset="date", keep="last")
                      .sort_values("date")
                      .reset_index(drop=True))

        try:
            result[code] = compute_gold(daily, cfg)
        except ValueError as e:
            print(f"  {e}")
            if existing and code in existing:
                print(f"  Keeping existing gold block for {code}")
                result[code] = existing[code]
            save_cache(code, daily, cfg["id"])
            continue
        save_cache(code, daily, cfg["id"])
        yrs = sorted(daily["year"].unique())
        print(f"  {code}: {len(daily)} rows, {yrs[0]}–{yrs[-1]}")

    if not result:
        sys.exit("Nothing computed — aborting.")

    for code in STATIONS:
        if code not in result:
            sys.exit(f"Missing {code} — aborting.")
        if not result[code].get("yearly"):
            sys.exit(f"Empty yearly for {code} — aborting.")

    # ── Freshness audit ──────────────────────────────────────────────────────
    today, unexpected = datetime.now(timezone.utc).date(), []
    for code in STATIONS:
        blk  = result[code]
        last = ((blk.get("ytd") or blk.get("heat_ytd") or {}).get("last_date")
                or f"{blk['yearly'][-1]['year']}-12-31")
        lag  = (today - datetime.strptime(last, "%Y-%m-%d").date()).days
        blk["last_data_date"] = last
        blk["lag_days"]       = lag
        threshold             = MAX_LAG_US if STATIONS[code]["id"].startswith("USW") else MAX_LAG_DAYS
        blk["stale"]          = lag > threshold
        if blk["stale"] and code not in KNOWN_STALE:
            unexpected.append(f"{code} ({last}, {lag}d)")

    fresh_codes = [c for c in STATIONS if not result[c]["stale"]]
    result["data_through"]   = (min(result[c]["last_data_date"] for c in fresh_codes)
                                if fresh_codes else None)
    result["stale_stations"] = [c for c in STATIONS if result[c]["stale"]]
    result["generated_at"]   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result["station_codes"]  = list(STATIONS.keys())
    result["stations"]         = build_station_roster(result)
    result["stations_summary"] = summarize_roster(result["stations"])

    result["unexpected_stale"] = [u.split()[0] for u in unexpected]
    result["quota_exhausted"]  = QUOTA_EXHAUSTED
    result["noaa_calls"]       = NOAA_CALLS

    # Write BEFORE any staleness exit — the healthy cities should still update.

    # Write BEFORE any staleness exit — the healthy cities should still update.
    OUT_PATH.write_text(json.dumps(result, separators=(",", ":")))
    print(f"\n✅ Written to {OUT_PATH} (data_through={result['data_through']})")
    for code in STATIONS:
        blk = result[code]
        print(f"   {code}: {blk['slope_annual']}°F/decade, "
              f"{len(blk['yearly'])} yrs, through "
              f"{blk['last_data_date']} ({blk['lag_days']}d)"
              f"{'  ⚠ STALE' if blk['stale'] else ''}")

    if unexpected:
        msg = "NEW STALE STATIONS: " + ", ".join(unexpected)
        print(f"::warning title=New stale stations::{msg}")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as fh:
                fh.write(f"### ⚠ {msg}\n")

    if QUOTA_EXHAUSTED:
        carried = [c for c in STATIONS if result.get(c, {}).get("quota_truncated")]
        msg = (f"NOAA QUOTA EXHAUSTED after {NOAA_CALLS} calls — "
               f"carried forward: {', '.join(carried) or 'none'}")
        print(f"::warning title=NOAA quota::{msg}")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as fh:
                fh.write(f"### ⚠ {msg}\n")


if __name__ == "__main__":
    main()
