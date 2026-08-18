"""
ClimatePulse — CO2 emissions → warming pipeline

Links three independent public records and quantifies the relationship between
them, then joins the result back to the 13 station series climate_pipeline.py
already produces.

  Emissions      Global Carbon Budget via OWID   (GtCO2/yr + cumulative, 1750–)
  Concentration  NOAA GML Mauna Loa annual mean  (ppm, 1959–)
  Temperature    NASA GISTEMP L-OTI global       (°C anomaly, 1880–)

Writes public_data_co2_gold.json at repo root. Stdlib only, no API keys.

Two honest-framing decisions are baked in and surfaced in the output:

1. The headline number is TCRE — warming per 1000 GtCO2 *cumulative emitted* —
   not warming per ppm. TCRE is the relationship the physics actually makes
   near-linear, and IPCC AR6 publishes an assessed range we can check the
   observational fit against. A ppm regression is also computed, but as
   context, not as the claim.

2. Per-station "sensitivity" is labeled correlation and shipped alongside a
   collinearity check. Cumulative CO2 rises monotonically with time, so
   regressing a local temperature series on it is arithmetically almost the
   same as regressing it on the year — the fit cannot separate CO2 from any
   other monotonic trend at a single station. `collinearity` and
   `implied_from_time_trend` make that explicit instead of hiding it.

Cadence: monthly. Emissions update once a year (Global Carbon Budget, ~Nov);
concentration and GISTEMP update monthly. Nothing here benefits from the daily
climate cron, and OWID is a 14 MB pull.
"""

import csv, io, json, math, statistics, urllib.request
from pathlib import Path
from datetime import datetime, timezone

ROOT        = Path(__file__).parent.parent
OUT_PATH    = ROOT / "public_data_co2_gold.json"
CLIMATE_GOLD = ROOT / "public_data_climate_gold.json"

OWID_URL    = "https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv"
GML_MLO_URL = "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_annmean_mlo.txt"
GML_GL_URL  = "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_annmean_gl.txt"
GISTEMP_URL = "https://data.giss.nasa.gov/gistemp/tabledata_v4/GLB.Ts+dSST.csv"

START_YEAR = 1970          # match the station window
UA = "ClimatePulse/1.0 (portfolio analytics)"

# 1 ppm CO2 ≈ 2.13 GtC ≈ 7.82 GtCO2 of atmospheric burden.
GTCO2_PER_PPM = 7.82

# ── Curated reference constants ──────────────────────────────────────────────
# No machine-readable feed exists for assessed climate metrics, so these stay
# curated and dated (same pattern as the data-center figures). They exist so the
# UI can show the observational fit *against* the published range rather than
# presenting a regression slope as if it were a sensitivity estimate.
IPCC_AR6 = {
    "reviewed":     "2026-08",
    "source":       "IPCC AR6 WG1 SPM (2021), D.1.1 / TS.3.2.1",
    "tcre_c_per_1000_gtco2": {"best": 0.45, "likely_range": [0.27, 0.63],
                              "note": "converted from 1.65 (1.0–2.3) °C per 1000 PgC"},
    "tcr_c":                 {"best": 1.8,  "likely_range": [1.4, 2.2]},
    "ecs_c":                 {"best": 3.0,  "likely_range": [2.5, 4.0]},
}

# Small-sample t critical values (two-tailed, 95%). n>=32 falls back to 1.96.
T95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31,
       9: 2.26, 10: 2.23, 12: 2.18, 15: 2.13, 20: 2.09, 25: 2.06, 30: 2.04}


def _get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ── stats ────────────────────────────────────────────────────────────────────
def pearson(xs, ys):
    if len(xs) < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (dx * dy)


def ols(xs, ys):
    """Least-squares fit with a 95% CI on the slope. Returns None if degenerate."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    m = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    b = my - m * mx

    dof = n - 2
    se = ci = None
    if dof > 0:
        s2 = sum((y - (m * x + b)) ** 2 for x, y in zip(xs, ys)) / dof
        se = math.sqrt(s2 / sxx)
        t = 1.96 if dof > 30 else T95.get(dof, T95[min(T95, key=lambda k: abs(k - dof))])
        ci = [m - t * se, m + t * se]

    r = pearson(xs, ys)
    return {"slope": m, "intercept": b, "se": se, "ci95": ci,
            "r": r, "r2": (r ** 2 if r is not None else None), "n": n}


def _fit(xs, ys, scale, nd=3):
    """Run a fit and rescale the slope into reporting units (e.g. per 100 ppm)."""
    f = ols(xs, ys)
    if not f:
        return None
    return {
        "slope":  round(f["slope"] * scale, nd),
        "se":     round(f["se"] * scale, nd) if f["se"] is not None else None,
        "ci95":   [round(c * scale, nd) for c in f["ci95"]] if f["ci95"] else None,
        "r":      round(f["r"], 3) if f["r"] is not None else None,
        "r2":     round(f["r2"], 3) if f["r2"] is not None else None,
        "n":      f["n"],
    }


# ── Parsers ──────────────────────────────────────────────────────────────────
def parse_owid_world(text):
    """World rows only. OWID reports CO2 in Mt; we emit Gt."""
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("country") != "World":
            continue
        try:
            yr = int(row["year"])
        except (ValueError, TypeError, KeyError):
            continue

        def f(key):
            v = row.get(key, "")
            try:
                return float(v) / 1000.0        # Mt → Gt
            except (ValueError, TypeError):
                return None

        out[yr] = {
            "year":             yr,
            "fossil_gt":        f("co2"),
            "luc_gt":           f("land_use_change_co2"),
            "total_gt":         f("co2_including_luc"),
            "cum_fossil_gt":    f("cumulative_co2"),
            "cum_total_gt":     f("cumulative_co2_including_luc"),
            "coal_gt":          f("coal_co2"),
            "oil_gt":           f("oil_co2"),
            "gas_gt":           f("gas_co2"),
            "cement_gt":        f("cement_co2"),
            "flaring_gt":       f("flaring_co2"),
        }
    if not out:
        raise RuntimeError("no World rows in OWID CO2 dataset")
    return out


def parse_gml_annmean(text):
    """NOAA GML annual mean files: comment lines start with '#', then
    `year mean unc` whitespace-separated."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            out[int(float(parts[0]))] = float(parts[1])
        except ValueError:
            continue
    if not out:
        raise RuntimeError("no rows parsed from GML annual mean file")
    return out


def parse_gistemp(text):
    """Same parser as hurricane_pipeline.py — J-D column, °C anomaly vs 1951–80."""
    out, header = {}, None
    for row in csv.reader(io.StringIO(text)):
        if row and row[0].strip() == "Year":
            header = [c.strip() for c in row]
            continue
        if header and row and row[0].strip().isdigit() and len(row[0].strip()) == 4:
            try:
                out[int(row[0])] = float(row[header.index("J-D")].strip())
            except (ValueError, IndexError):
                pass
    if not out:
        raise RuntimeError("no rows parsed from GISTEMP")
    return out


# ── Analysis ─────────────────────────────────────────────────────────────────
def paired(a, b, start=START_YEAR):
    yrs = sorted(y for y in a if y in b and y >= start)
    return yrs, [a[y] for y in yrs], [b[y] for y in yrs]


def global_analysis(emis, ppm, gist):
    """Everything that does not depend on a station: TCRE, airborne fraction,
    concentration response."""
    cum = {y: e["cum_total_gt"] for y, e in emis.items() if e["cum_total_gt"]}
    out = {}

    # TCRE — the headline. °C per 1000 GtCO2 cumulative.
    if gist:
        yrs, xs, ys = paired(cum, gist)
        out["tcre_observed"] = _fit(xs, ys, 1000.0)
        if out["tcre_observed"]:
            out["tcre_observed"]["window"] = [yrs[0], yrs[-1]]
            out["tcre_observed"]["unit"]   = "°C per 1000 GtCO2"
            best = IPCC_AR6["tcre_c_per_1000_gtco2"]
            lo, hi = best["likely_range"]
            out["tcre_observed"]["within_ar6_likely_range"] = (
                lo <= out["tcre_observed"]["slope"] <= hi)

    # Concentration response — context, not a sensitivity estimate.
    if ppm and gist:
        yrs, xs, ys = paired(ppm, gist)
        out["concentration_response"] = _fit(xs, ys, 100.0)
        if out["concentration_response"]:
            out["concentration_response"]["window"] = [yrs[0], yrs[-1]]
            out["concentration_response"]["unit"]   = "°C per 100 ppm"

    # Airborne fraction — what share of emitted CO2 stayed in the air.
    if ppm:
        yrs, xs, ys = paired(cum, ppm)
        if len(yrs) >= 3:
            f = ols(xs, ys)                       # ppm per GtCO2 cumulative
            if f:
                out["airborne_fraction"] = {
                    "value":   round(f["slope"] * GTCO2_PER_PPM, 3),
                    "ci95":    ([round(c * GTCO2_PER_PPM, 3) for c in f["ci95"]]
                                if f["ci95"] else None),
                    "window":  [yrs[0], yrs[-1]],
                    "basis":   f"1 ppm = {GTCO2_PER_PPM} GtCO2 atmospheric burden",
                    "note":    ("fraction of cumulative emissions that remained "
                                "airborne; the rest went to ocean and land sinks"),
                }

    # Collinearity guard: how close cumulative CO2 is to a straight function of
    # time over this window. If r ≈ 1, no regression here can separate the two.
    for key, series in (("cumulative_vs_year", cum), ("ppm_vs_year", ppm)):
        if series:
            yrs = sorted(y for y in series if y >= START_YEAR)
            if len(yrs) >= 3:
                r = pearson([float(y) for y in yrs], [series[y] for y in yrs])
                out.setdefault("collinearity", {})[key] = round(r, 4) if r else None

    return out


def station_analysis(gold, emis, ppm):
    """Per-city local warming vs cumulative emissions, with a cross-check
    against the pipeline's own time-based trend."""
    cum = {y: e["cum_total_gt"] for y, e in emis.items() if e["cum_total_gt"]}
    ppm_per_decade = None
    if ppm:
        yrs = sorted(y for y in ppm if y >= START_YEAR)
        f = ols([float(y) for y in yrs], [ppm[y] for y in yrs])
        ppm_per_decade = f["slope"] * 10 if f else None

    rows = []
    for code in gold.get("station_codes", []):
        blk = gold.get(code)
        if not blk or not blk.get("yearly"):
            continue
        tmean = {y["year"]: y["avg_tmean"] for y in blk["yearly"]}

        row = {
            "code":            code,
            "name":            blk["name"],
            "color":           blk["color"],
            "slope_annual":    blk["slope_annual"],       # °F/decade, from climate gold
            "trend_reliable":  blk.get("trend_reliable", False),
        }

        yrs, xs, ys = paired(cum, tmean)
        if len(yrs) >= 10:
            fit = _fit(xs, ys, 1000.0)                    # °F per 1000 GtCO2
            if fit:
                fit["unit"]   = "°F per 1000 GtCO2 cumulative"
                fit["window"] = [yrs[0], yrs[-1]]
                row["vs_cumulative_emissions"] = fit
                row["warming_over_window_f"] = round(
                    fit["slope"] * (xs[-1] - xs[0]) / 1000.0, 2)
                row["cum_emissions_over_window_gt"] = round(xs[-1] - xs[0], 1)

        if ppm:
            yrs, xs, ys = paired(ppm, tmean)
            if len(yrs) >= 10:
                fit = _fit(xs, ys, 100.0)                 # °F per 100 ppm
                if fit:
                    fit["unit"]   = "°F per 100 ppm"
                    fit["window"] = [yrs[0], yrs[-1]]
                    # The cross-check that proves these are the same number:
                    # (°F/decade) ÷ (ppm/decade) × 100 should reproduce the fit.
                    if ppm_per_decade:
                        implied = blk["slope_annual"] / ppm_per_decade * 100
                        fit["implied_from_time_trend"] = round(implied, 3)
                        fit["divergence"] = round(fit["slope"] - implied, 3)
                    row["vs_concentration"] = fit

        if "vs_cumulative_emissions" in row or "vs_concentration" in row:
            rows.append(row)

    rows.sort(key=lambda r: -(r.get("vs_cumulative_emissions") or {}).get("slope", 0))
    return rows


def build_effects(emis, ppm, gist, glob, stations):
    """Structured facts for the dashboard list. Values only — the UI formats
    them, so nothing here can drift from what was computed."""
    eff = []
    yrs = sorted(emis)
    latest, base = yrs[-1], START_YEAR

    def add(**kw):
        eff.append(kw)

    if base in emis and latest in emis:
        e0, e1 = emis[base], emis[latest]
        add(id="emissions_annual", label="Annual global CO2 emissions",
            value=round(e1["total_gt"], 1), unit="GtCO2/yr", year=latest,
            baseline=round(e0["total_gt"], 1), baseline_year=base,
            change_pct=round((e1["total_gt"] / e0["total_gt"] - 1) * 100, 1),
            basis="observed", source="Global Carbon Budget via OWID")
        add(id="emissions_cumulative", label="Cumulative CO2 emitted, all time",
            value=round(e1["cum_total_gt"], 0), unit="GtCO2", year=latest,
            detail=round(e1["cum_total_gt"] - e0["cum_total_gt"], 0),
            detail_label=f"emitted since {base}",
            basis="observed", source="Global Carbon Budget via OWID")
        add(id="emissions_since_1970_share",
            label="Share of all CO2 ever emitted that was emitted since 1970",
            value=round((e1["cum_total_gt"] - e0["cum_total_gt"])
                        / e1["cum_total_gt"] * 100, 1),
            unit="%", basis="observed")

    if ppm and base in ppm:
        py = max(y for y in ppm)
        add(id="concentration", label="Atmospheric CO2 concentration",
            value=round(ppm[py], 1), unit="ppm", year=py,
            baseline=round(ppm[base], 1), baseline_year=base,
            detail=round(ppm[py] - ppm[base], 1), detail_label=f"ppm added since {base}",
            basis="observed", source="NOAA GML Mauna Loa")

    if gist and base in gist:
        gy = max(y for y in gist)
        add(id="global_temp", label="Global temperature anomaly",
            value=round(gist[gy], 2), unit="°C vs 1951–80", year=gy,
            baseline=round(gist[base], 2), baseline_year=base,
            detail=round(gist[gy] - gist[base], 2), detail_label=f"warming since {base}",
            basis="observed", source="NASA GISTEMP")

    if glob.get("tcre_observed"):
        t = glob["tcre_observed"]
        add(id="tcre", label="Warming per 1000 GtCO2 emitted (TCRE)",
            value=t["slope"], unit="°C per 1000 GtCO2", ci95=t["ci95"], r2=t["r2"],
            reference=IPCC_AR6["tcre_c_per_1000_gtco2"]["likely_range"],
            reference_label="IPCC AR6 likely range",
            agrees_with_reference=t.get("within_ar6_likely_range"),
            basis="regression", confidence="high",
            note="near-linear by construction; the relationship IPCC assesses directly")

    if glob.get("airborne_fraction"):
        a = glob["airborne_fraction"]
        add(id="airborne_fraction", label="Share of emissions that stayed airborne",
            value=round(a["value"] * 100, 1), unit="%", ci95=a["ci95"],
            basis="regression", note=a["note"])

    if glob.get("concentration_response"):
        c = glob["concentration_response"]
        add(id="concentration_response", label="Global warming per 100 ppm CO2",
            value=c["slope"], unit="°C per 100 ppm", ci95=c["ci95"], r2=c["r2"],
            basis="regression", confidence="medium",
            note="context only — not a climate-sensitivity estimate")

    ranked = [s for s in stations if s.get("vs_cumulative_emissions")]
    if ranked:
        top, bot = ranked[0], ranked[-1]
        add(id="local_spread", label="Local warming per 1000 GtCO2 — city spread",
            value=top["vs_cumulative_emissions"]["slope"],
            unit="°F per 1000 GtCO2",
            detail=bot["vs_cumulative_emissions"]["slope"],
            top=top["name"], bottom=bot["name"], n=len(ranked),
            basis="correlation", confidence="low",
            note=("single-station fits cannot separate CO2 from any other "
                  "monotonic trend — see collinearity"))

    return eff


CAVEATS = [
    "Emissions, concentration, and temperature are three independent observational "
    "records. Nothing here is a climate model run.",
    "TCRE is reported as the headline because warming scales near-linearly with "
    "cumulative emissions; warming per ppm does not, and warming per year is not a "
    "physical relationship at all.",
    "Cumulative CO2 is almost perfectly collinear with the calendar year over this "
    "window (see `global.collinearity`). Regressing any temperature series on it is "
    "arithmetically close to regressing on time — these fits establish consistency, "
    "not causation.",
    "Per-station numbers are local correlations. Regional circulation, urban heat "
    "island, and station moves all load onto the same trend, which is why the city "
    "spread is wide while the global fit is tight.",
    "IPCC AR6 values are curated constants with a review date, not a live feed. "
    "They change only when a new assessment report is published.",
]


# ── Main ─────────────────────────────────────────────────────────────────────
def main(fetch=_get):
    print("ClimatePulse — CO2 → warming")

    emis = parse_owid_world(fetch(OWID_URL))
    print(f"  emissions: {len(emis)} World rows, through {max(emis)}")

    # Fail soft: concentration and GISTEMP are nice-to-have. Emissions are not —
    # without them there is no TCRE and no reason to write the file.
    ppm = {}
    for url in (GML_MLO_URL, GML_GL_URL):
        try:
            ppm = parse_gml_annmean(fetch(url))
            print(f"  concentration: {len(ppm)} yrs from {url.rsplit('/', 1)[-1]}")
            break
        except Exception as e:
            print(f"  GML {url.rsplit('/', 1)[-1]} failed: {e}")

    try:
        gist = parse_gistemp(fetch(GISTEMP_URL))
        print(f"  GISTEMP: {len(gist)} yrs, through {max(gist)}")
    except Exception as e:
        print(f"  GISTEMP failed: {e}")
        gist = {}

    gold = {}
    if CLIMATE_GOLD.exists():
        try:
            gold = json.loads(CLIMATE_GOLD.read_text())
        except Exception as e:
            print(f"  could not read climate gold: {e}")
    if not gold:
        print("  no climate gold — skipping per-station join")

    glob     = global_analysis(emis, ppm, gist)
    stations = station_analysis(gold, emis, ppm) if gold else []
    effects  = build_effects(emis, ppm, gist, glob, stations)

    yrs = sorted(y for y in emis if y >= START_YEAR)
    latest = emis[max(emis)]
    fuel_keys = ("coal_gt", "oil_gt", "gas_gt", "cement_gt", "flaring_gt", "luc_gt")

    result = {
        "emissions": {
            "unit":   "GtCO2",
            "source": "Global Carbon Budget via Our World in Data",
            "annual": [{"year": y,
                        "total": round(emis[y]["total_gt"], 2)
                                 if emis[y]["total_gt"] else None,
                        "fossil": round(emis[y]["fossil_gt"], 2)
                                  if emis[y]["fossil_gt"] else None,
                        "cumulative": round(emis[y]["cum_total_gt"], 1)
                                      if emis[y]["cum_total_gt"] else None}
                       for y in yrs],
            "latest_year": max(emis),
            "by_source_latest": {k.replace("_gt", ""): round(latest[k], 2)
                                 for k in fuel_keys if latest.get(k) is not None},
        },
        "concentration": {
            "unit": "ppm", "source": "NOAA GML Mauna Loa annual mean",
            "annual": [{"year": y, "ppm": round(ppm[y], 2)}
                       for y in sorted(ppm) if y >= START_YEAR],
        } if ppm else None,
        "global_temp": {
            "unit": "°C anomaly vs 1951–80", "source": "NASA GISTEMP v4 L-OTI",
            "annual": [{"year": y, "anomaly": round(gist[y], 2)}
                       for y in sorted(gist) if y >= START_YEAR],
        } if gist else None,
        "global":       glob,
        "stations":     stations,
        "effects":      effects,
        "reference":    IPCC_AR6,
        "caveats":      CAVEATS,
        "window":       [START_YEAR, max(emis)],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    OUT_PATH.write_text(json.dumps(result, separators=(",", ":")))
    print(f"\n✅ Written to {OUT_PATH}")
    if glob.get("tcre_observed"):
        t = glob["tcre_observed"]
        flag = "✓ within" if t.get("within_ar6_likely_range") else "⚠ outside"
        print(f"   TCRE {t['slope']} °C/1000 GtCO2 (r²={t['r2']}) — "
              f"{flag} AR6 likely range")
    print(f"   {len(stations)} stations joined, {len(effects)} effects computed")


if __name__ == "__main__":
    main()
