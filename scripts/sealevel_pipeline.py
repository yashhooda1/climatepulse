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

Houston Coast block (live, NOAA CO-OPS — no key):
  monthly mean sea level   https://api.tidesandcurrents.noaa.gov/api/prod/datagetter (product=monthly_mean)
  official linear trends   .../dpapi/prod/webapi/product/sealvltrends.json
  2022 local projections   .../dpapi/prod/webapi/product/slr_projections.json
Fails soft per gauge; if CO-OPS is entirely down the Houston block is omitted.
"""

import json, math, statistics, time, urllib.request
from pathlib import Path
from datetime import datetime

OUT_PATH  = Path(__file__).parent.parent / "public_data_sealevel_gold.json"
STAR_URL  = "https://www.star.nesdis.noaa.gov/socd/lsa/SeaLevelRise/slr/slr_sla_gbl_free_ref_90.csv"
UA        = "ClimatePulse/1.0 (portfolio analytics)"

COOPS        = "https://api.tidesandcurrents.noaa.gov"
COOPS_MONTHLY = (COOPS + "/api/prod/datagetter?begin_date={begin}0101&end_date={end}1231&station={sid}"
                 "&product=monthly_mean&datum=MSL&units=metric&time_zone=gmt&format=json&application=ClimatePulse")
COOPS_TRENDS = COOPS + "/dpapi/prod/webapi/product/sealvltrends.json?affil=US"
COOPS_PROJ   = COOPS + "/dpapi/prod/webapi/product/slr_projections.json?station={sid}&report_year=2022"
IKE_YEAR     = 2008
MIN_MONTHS   = 10     # a year counts toward the annual series with >= 10 monthly means

# Tide gauges for the Houston Coast tab. Galveston Pier 21 is one of the longest
# U.S. records (1904–); Morgans Point sits where the Houston Ship Channel — the
# dredged lower Buffalo Bayou — meets Galveston Bay; Eagle Point, on the west
# shore of the bay, shows what subsidence adds on top of the Gulf's rise.
HOUSTON_GAUGES = [
    {"id": "8771450", "name": "Galveston Pier 21", "role": "Gulf entrance · record since 1904", "projections": True},
    {"id": "8770613", "name": "Morgans Point",     "role": "Mouth of the Houston Ship Channel"},
    {"id": "8771013", "name": "Eagle Point",       "role": "West Galveston Bay · subsiding shore"},
]

# Curated, sourced place notes. Numbers in the dashboard come from the gauges
# above; these carry the context a gauge can't.
HOUSTON_PLACES = [
    {
        "key": "galveston", "name": "Galveston Island", "gauge": "8771450",
        "notes": [
            {"text": "Ike put 10–15 ft of surge on Galveston Island and west Galveston Bay in 2008, and 15–20 ft across the "
                     "channel on Bolivar Peninsula. Every inch the gauge gains is an inch the next surge starts from.",
             "source": "NHC Tropical Cyclone Report AL092008",
             "url": "https://www.nhc.noaa.gov/data/tcr/AL092008_Ike.pdf"},
            {"text": "NOAA's 2022 report expects the western Gulf — Texas and Louisiana — to see the most sea-level rise of any "
                     "U.S. coast, because the land there is subsiding while the sea rises.",
             "source": "NOAA 2022 Sea Level Rise Technical Report, via Route Fifty",
             "url": "https://www.route-fifty.com/management/2022/02/coastal-cities-expecting-greatest-sea-level-rise/362094"},
            {"text": "The planned defense is the Coastal Texas Project (\"Ike Dike\"): a gate across Bolivar Roads plus dunes on Bolivar "
                     "and west Galveston, authorized in 2022 and now estimated near $57 billion.",
             "source": "ConstructConnect (Feb 2026)",
             "url": "https://news.constructconnect.com/massive-galveston-bay-barrier-system-moves-closer-to-construction-challenges-remain"},
        ],
    },
    {
        "key": "buffalo_bayou", "name": "Buffalo Bayou & the Ship Channel", "gauge": "8770613",
        "notes": [
            {"text": "Buffalo Bayou is tidal well inland — downstream of a point just west of the Shepherd Drive bridge, through "
                     "downtown and Allen's Landing to the Turning Basin, where it becomes the Ship Channel. A higher Gulf raises "
                     "the baseline of that whole tidal reach.",
             "source": "Buffalo Bayou — Wikipedia (USGS runs a tidal gauge at the Turning Basin, 08074710)",
             "url": "https://en.wikipedia.org/wiki/Buffalo_Bayou"},
            {"text": "Harvey showed how the bayou floods: water peaked 3 m above predicted at Buffalo Bayou's upper bay reach after "
                     "a \"triple punch\" — ocean surge, then Buffalo Bayou runoff, then the San Jacinto. Higher mean sea level "
                     "was one of the ocean-side factors.",
             "source": "Valle-Levinson et al. 2020, Science of the Total Environment",
             "url": "https://repository.library.noaa.gov/view/noaa/50032/noaa_50032_DS1.pdf"},
            {"text": "The 52-mile Ship Channel is lined by 10 major refineries and dozens of chemical plants — and is also a "
                     "conduit that can carry storm surge straight into a densely populated industrial corridor.",
             "source": "ProPublica / Texas Tribune, \"Hell and High Water\"",
             "url": "https://www.propublica.org/article/hell-and-high-water-text"},
        ],
    },
    {
        "key": "houston", "name": "Houston", "gauge": None,
        "notes": [
            {"text": "City officials say only a small portion of Houston is at risk for major storm surge. The exposure "
                     "concentrates along the bay and the Ship Channel corridor — where surge and bayou flooding can combine, "
                     "as they did in Harvey.",
             "source": "ProPublica / Texas Tribune, \"Hell and High Water\"",
             "url": "https://www.propublica.org/article/hell-and-high-water-text"},
            {"text": "The land is sinking too. Groundwater pumping dropped some areas more than 9 ft within four decades, and "
                     "Baytown's Brownwood subdivision lost so much elevation it was abandoned. Subsidence is irreversible.",
             "source": "Harris-Galveston Subsidence District (Brownwood: ABC13 interview with HGSD, 2021)",
             "url": "https://hgsubsidence.org/?p=646"},
            {"text": "Sinking has moved inland with growth: Katy has subsided more than a foot in about two decades, the fastest "
                     "rate in the region, as northwest Harris County still leans on groundwater.",
             "source": "ABC13 / Harris-Galveston Subsidence District (May 2026)",
             "url": "https://abc13.com/post/new-data-shows-houston-area-suburb-sinks-more-foot-decades/19095321/"},
        ],
    },
]

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
# ── Houston Coast (NOAA CO-OPS tide gauges) ──────────────────────────────────
def annual_msl(payload, min_months=MIN_MONTHS, current_year=None):
    """CO-OPS monthly_mean JSON → {year: mean MSL in mm} for years with enough
    months. Values are relative to the station's MSL datum (current epoch).
    The year in progress needs all 12 months: Gulf water levels run high in
    autumn, so a Jan–Oct mean would be biased."""
    by_year = {}
    for row in (payload or {}).get("data", []):
        try:
            by_year.setdefault(int(row["year"]), []).append(float(row["MSL"]) * 1000.0)
        except (KeyError, TypeError, ValueError):
            continue
    return {y: round(statistics.mean(v), 1) for y, v in sorted(by_year.items())
            if len(v) >= (12 if y == current_year else min_months)}


def station_trend(payload, sid):
    """Official linear relative-sea-level trend for one station from the
    sealvltrends feed (which returns every U.S. station). → mm/yr dict or None."""
    for s in (payload or {}).get("SeaLvlTrends", []):
        if str(s.get("stationId")) != sid or s.get("trend") is None:
            continue
        per = 25.4 / 10.0 if "inch" in str(s.get("trendUnits", "")).lower() else 1.0
        return {
            "mm_yr": round(s["trend"] * per, 2),
            "ci_mm_yr": round((s.get("trendError") or 0) * per, 2),
            "ft_century": round(s["trend"] * per * 100 / 304.8, 2),
            "start": s.get("startDate"), "end": s.get("endDate"),
        }
    return None


def station_projections(payload):
    """NOAA 2022 local projections → {scenario: [{year, m, lo, hi}]} in metres
    above the 2005 baseline the report uses."""
    out = {}
    for p in (payload or {}).get("SlrProjections", []):
        if p.get("projectionRsl") is None or p.get("projectionYear") is None:
            continue
        cm = 1.0 if str(p.get("units", "cm")).lower() == "cm" else 100.0
        out.setdefault(p["scenario"], []).append({
            "year": int(p["projectionYear"]),
            "m": round(p["projectionRsl"] * cm / 100.0, 3),
            "lo": round((p.get("projectionCiLow") or 0) * cm / 100.0, 3),
            "hi": round((p.get("projectionCiHigh") or 0) * cm / 100.0, 3),
        })
    order = ["Low", "Intermediate-Low", "Intermediate", "Intermediate-High", "High"]
    return {k: sorted(out[k], key=lambda r: r["year"]) for k in order if k in out}


def build_houston(fetch, now_year):
    """Live gauge data for the Houston Coast tab. Each gauge fails soft on its
    own; returns None only if nothing at all came back."""
    try:
        trends = json.loads(fetch(COOPS_TRENDS))
    except Exception as e:
        print(f"  ⚠ CO-OPS trends unavailable: {e}")
        trends = None
    gauges = []
    for g in HOUSTON_GAUGES:
        rec = {k: g[k] for k in ("id", "name", "role")}
        try:
            annual = annual_msl(json.loads(fetch(COOPS_MONTHLY.format(begin=1900, end=now_year, sid=g["id"]))),
                                current_year=now_year)
        except Exception as e:
            print(f"  ⚠ {g['name']} monthly means unavailable: {e}")
            annual = {}
        rec["annual"] = [{"year": y, "mm": v} for y, v in annual.items()]
        rec["trend"] = station_trend(trends, g["id"])
        if rec["trend"] and annual:
            rate = rec["trend"]["mm_yr"]
            last = max(annual)
            # Rise implied by the official linear trend — steadier than
            # differencing two noisy annual means.
            rec["rise_since_ike_mm"] = round(rate * (last - IKE_YEAR), 0) if last > IKE_YEAR else None
            rec["rise_since_start_mm"] = round(rate * (last - min(annual)), 0)
        if g.get("projections"):
            try:
                rec["projections"] = station_projections(json.loads(fetch(COOPS_PROJ.format(sid=g["id"]))))
            except Exception as e:
                print(f"  ⚠ {g['name']} projections unavailable: {e}")
        if rec["annual"] or rec["trend"]:
            gauges.append(rec)
            t = rec["trend"]["mm_yr"] if rec["trend"] else "—"
            print(f"  {g['name']}: {len(rec['annual'])} yrs, trend {t} mm/yr")
    if not gauges:
        return None
    return {
        "source": "NOAA CO-OPS tide gauges (monthly mean sea level, official linear trends, 2022 local projections)",
        "baseline_note": "Gauge levels are relative to each station's mean sea level datum; projections are metres above the 2005 baseline used by NOAA's 2022 report.",
        "ike_year": IKE_YEAR,
        "gauges": gauges,
        "places": HOUSTON_PLACES,
    }


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

    # Houston Coast: live gauges. Each gauge retries and fails soft on its own;
    # if CO-OPS is entirely down the block is left out and the GMSL pipeline
    # still publishes (the site shows a "next run" placeholder for the tab).
    print("Houston Coast gauges:")
    houston = build_houston(fetch, datetime.utcnow().year)
    if houston:
        result["houston"] = houston

    if len(gmsl["series"]) < 20:
        print("Too few years parsed — aborting."); return
    OUT_PATH.write_text(json.dumps(result, separators=(",", ":")))
    print(f"OK {len(gmsl['series'])} yrs -> {OUT_PATH.name} | "
          f"latest {latest}: +{annual[latest]}mm | rate_now {rate_now} mm/yr | STAR trend {star_trend}")


if __name__ == "__main__":
    main()
