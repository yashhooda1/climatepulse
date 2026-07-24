"""
sealevel_pipeline.py — ClimatePulse Rising Seas pipeline.

Fetches NOAA STAR satellite-altimetry global mean sea level (updates continually),
rebuilds the observed GMSL curve, and re-anchors the 2100 forecast fan to the latest
data. The NOAA 2022 scenario TARGETS stay curated (they only change when NOAA issues a
new technical report). City tide-gauge trends and ice sea-level equivalents are curated
reference values. Writes public_data_sealevel_gold.json at repo root. Stdlib only.

Source: NOAA/NESDIS/STAR Laboratory for Satellite Altimetry
  https://www.star.nesdis.noaa.gov/socd/lsa/SeaLevelRise/slr/slr_sla_gbl_free_ref_90.csv
  "Altimetry data are provided by the NOAA Laboratory for Satellite Altimetry."
"""

import json, math, statistics, time, urllib.request
from pathlib import Path
from datetime import datetime

OUT_PATH  = Path(__file__).parent.parent / "public_data_sealevel_gold.json"
STAR_URL  = "https://www.star.nesdis.noaa.gov/socd/lsa/SeaLevelRise/slr/slr_sla_gbl_free_ref_90.csv"
UA        = "ClimatePulse/1.0 (portfolio analytics)"

# Curated reference data (tide-gauge trends, ice equivalents, NOAA-2022 scenario targets)
CURATED = {
    "cities": [
        {
            "name": "New Orleans / Grand Isle, LA",
            "region": "Mississippi Delta",
            "lat": 29.263,
            "lon": -89.957,
            "station_id": "8761724",
            "trend_mm_yr": 9.1,
            "tier": "Extreme",
            "note": "Highest relative rise in the U.S. \u2014 delta subsidence plus levee-dependent, much of metro below sea level."
        },
        {
            "name": "Houston / Galveston, TX",
            "region": "Upper Texas Coast",
            "lat": 29.31,
            "lon": -94.793,
            "station_id": "8771450",
            "trend_mm_yr": 6.6,
            "tier": "Very High",
            "note": "Groundwater/oil-withdrawal subsidence compounds Gulf rise; hurricane storm surge multiplier."
        },
        {
            "name": "Miami / Virginia Key, FL",
            "region": "Southeast Florida",
            "lat": 25.731,
            "lon": -80.162,
            "station_id": "8723214",
            "trend_mm_yr": 4.0,
            "tier": "Extreme",
            "note": "Porous limestone bedrock lets water up through the ground \u2014 seawalls can't stop it; <2 m elevation, dense."
        },
        {
            "name": "New York / The Battery, NY",
            "region": "NY\u2013NJ Harbor",
            "lat": 40.7,
            "lon": -74.014,
            "station_id": "8518750",
            "trend_mm_yr": 2.9,
            "tier": "High",
            "note": "Enormous exposed population and infrastructure; Sandy showed surge-on-rise risk to transit and utilities."
        },
        {
            "name": "San Francisco Bay / Delta, CA",
            "region": "Bay\u2013Delta",
            "lat": 37.807,
            "lon": -122.465,
            "station_id": "9414290",
            "trend_mm_yr": 2.0,
            "tier": "Moderate\u2013High",
            "note": "Rise near global average, but Bay-Delta levees guard the state's water supply, SFO/OAK, and low-lying fill."
        }
    ],
    "ice": [
        {
            "name": "West Antarctica (unstable sector)",
            "sle_m": 5.0,
            "sle_ft": 16,
            "note": "The realistically vulnerable part this millennium \u2014 Thwaites/WAIS marine-based ice."
        },
        {
            "name": "Greenland Ice Sheet",
            "sle_m": 7.4,
            "sle_ft": 24,
            "note": "BedMachine v3: 7.42 \u00b1 0.05 m if it all melted."
        },
        {
            "name": "Antarctic Ice Sheet (all)",
            "sle_m": 58.3,
            "sle_ft": 191,
            "note": "Full Antarctic ice \u2014 the overwhelming majority of Earth's land ice."
        },
        {
            "name": "All land ice (everything)",
            "sle_m": 65.7,
            "sle_ft": 216,
            "note": "Both ice sheets + all glaciers. USGS cites ~70 m (230 ft) with every mountain glacier included."
        }
    ],
    "context": "Full-melt scenarios unfold over centuries to millennia, not this century. Observed rise this century is ~0.3\u20131 m by 2100 (up to ~2 m on high-emission, rapid-ice-loss pathways for the U.S. coast). The ice bars show the ultimate ceiling each reservoir holds, not a 2100 forecast.",
    "forecast_meta": {
        "baseline_year": 2000,
        "unit": "m above 2000",
        "source": "NOAA 2022 Interagency Sea Level Rise Technical Report (Sweet et al. 2022); IPCC AR6 cross-reference",
        "note": "Five NOAA scenarios keyed to their 2100 global target. Paths stay close through ~2050 (~0.25-0.3 m, largely locked in) and diverge after mid-century based on emissions and ice-sheet response. IPCC AR6 SSP5-8.5 likely range for 2100 is 0.6-1.0 m, with a low-confidence high end approaching 2 m \u2014 consistent with NOAA's Intermediate-High/High.",
        "scenarios": [
            {
                "key": "low",
                "name": "Low",
                "target_2100": 0.3,
                "color": "#38bdf8"
            },
            {
                "key": "intlow",
                "name": "Intermediate-Low",
                "target_2100": 0.5,
                "color": "#4ade80"
            },
            {
                "key": "int",
                "name": "Intermediate",
                "target_2100": 1.0,
                "color": "#facc15"
            },
            {
                "key": "inthigh",
                "name": "Intermediate-High",
                "target_2100": 1.5,
                "color": "#fb923c"
            },
            {
                "key": "high",
                "name": "High",
                "target_2100": 2.0,
                "color": "#ef4444"
            }
        ]
    }
}


def _get(url, timeout=180, retries=4, backoff=10):
    """Fetch with retries — NOAA STAR intermittently stalls mid-download."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            last_err = e
            if attempt < retries:
                wait = backoff * attempt
                print(f"  fetch attempt {attempt}/{retries} failed "
                      f"({type(e).__name__}: {e}) — retrying in {wait}s")
                time.sleep(wait)
            else:
                print(f"  fetch failed after {retries} attempts: {type(e).__name__}: {e}")
    raise last_err


# ── Parse NOAA STAR altimetry CSV → annual GMSL (mm above 1993) ───────────────
def parse_star_csv(text):
    """Return (annual:{year: mm_above_1993}, star_trend_mm_yr)."""
    star_trend = None
    monthly = {}   # year -> list of SLA values (mm, relative to record mean)
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            if "trend" in s.lower():
                for tok in s.replace("=", " ").split():
                    try:    star_trend = float(tok); break
                    except ValueError: pass
            continue
        if s.lower().startswith("year"):     # header row
            continue
        parts = s.split(",")
        try:    frac_year = float(parts[0])
        except (ValueError, IndexError):
            continue
        # value = first non-empty mission column
        val = None
        for c in parts[1:]:
            c = c.strip()
            if c:
                try:    val = float(c); break
                except ValueError: pass
        if val is None:
            continue
        monthly.setdefault(int(frac_year), []).append(val)

    annual_raw = {y: statistics.mean(v) for y, v in monthly.items() if v}
    if 1993 not in annual_raw:
        raise RuntimeError("NOAA STAR data missing 1993 baseline")
    base = annual_raw[1993]
    annual = {y: round(annual_raw[y] - base, 1) for y in sorted(annual_raw)}   # mm above 1993
    return annual, star_trend


def _slope_mm_yr(annual, years):
    xs = [y for y in years if y in annual]
    if len(xs) < 2:
        return None
    ys = [annual[y] for y in xs]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom, 2) if denom else None


# ── Rebuild the 2100 forecast fan anchored to the latest observed data ───────
def build_forecast(annual):
    meta = CURATED["forecast_meta"]
    yrs = sorted(annual)
    latest = yrs[-1]
    mm2000 = annual.get(2000, 0.0)
    to_m2000 = lambda mm: round((mm - mm2000) / 1000.0, 3)
    observed = [{"year": y, "m": to_m2000(annual[y])} for y in yrs if y >= 2000]

    v2020 = to_m2000(annual.get(2020, annual[latest]))
    v_latest = to_m2000(annual[latest])
    rate = (_slope_mm_yr(annual, range(latest - 9, latest + 1)) or 4.5) / 1000.0   # m/yr, recent decade
    proj_years = [latest, 2030, 2040, 2050, 2060, 2070, 2080, 2090, 2100]
    scenarios = []
    for s in meta["scenarios"]:
        target, v0, t0 = s["target_2100"], v2020, 2020
        a2 = (target - v0 - rate * (2100 - t0)) / ((2100 - t0) ** 2)
        series = []
        for y in proj_years:
            t = y - t0
            val = v_latest if y == latest else round(v0 + rate * t + a2 * t * t, 3)
            series.append({"year": y, "m": val})
        scenarios.append({**s, "series": series})

    return {"baseline_year": meta["baseline_year"], "unit": meta["unit"], "source": meta["source"],
            "observed": observed, "scenarios": scenarios, "note": meta["note"]}


# ── Main ─────────────────────────────────────────────────────────────────────
def main(fetch=_get):
    annual, star_trend = parse_star_csv(fetch(STAR_URL))
    yrs = sorted(annual)
    latest = yrs[-1]
    rate_now = _slope_mm_yr(annual, range(latest - 9, latest + 1))
    rate_1993 = _slope_mm_yr(annual, range(1993, 2000))

    gmsl = {
        "unit": "mm above 1993", "latest_year": latest, "latest_mm": annual[latest],
        "rate_1993": rate_1993, "rate_now": rate_now,
        "star_trend_mm_yr": star_trend, "total_since_1993_mm": annual[latest],
        "series": [{"year": y, "mm": annual[y]} for y in yrs],
    }

    result = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": ("NOAA/NESDIS/STAR satellite altimetry (live) · NOAA Tides & Currents tide-gauge "
                   "trends · USGS/NSIDC ice equivalents · NOAA 2022 SLR scenarios"),
        "gmsl": gmsl,
        "cities": CURATED["cities"],
        "ice": CURATED["ice"],
        "forecast": build_forecast(annual),
        "context": CURATED["context"],
    }

    if len(gmsl["series"]) < 20:
        print("Too few years parsed — aborting."); return
    OUT_PATH.write_text(json.dumps(result, separators=(",", ":")))
    print(f"OK {len(gmsl['series'])} yrs -> {OUT_PATH.name} | "
          f"latest {latest}: +{annual[latest]}mm | rate_now {rate_now} mm/yr | STAR trend {star_trend}")


if __name__ == "__main__":
    main()
