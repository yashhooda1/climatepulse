"""
ClimatePulse — Hurricane analytics pipeline (Atlantic basin)
Correlates annual Atlantic hurricane activity with (1) tropical North Atlantic
sea-surface temperature — the water storms actually intensify over — and
(2) global temperature. Writes public_data_hurricanes_gold.json at repo root.

Independent of climate_pipeline.py. Stdlib only (no NOAA_TOKEN, no pandas).
Sources (NOAA/NASA, public domain):
  HURDAT2 Atlantic best-track   https://www.nhc.noaa.gov/data/hurdat/  (latest auto-discovered)
  ATCF operational best track    https://ftp.nhc.noaa.gov/atcf/btk/      (seasons HURDAT2 doesn't cover yet)
  TNA SST anomaly (5-25N,55-15W) https://psl.noaa.gov/data/correlation/tna.data
  GISTEMP global L-OTI anomaly   https://data.giss.nasa.gov/gistemp/tabledata_v4/GLB.Ts+dSST.csv

HURDAT2 is reanalysed and published once a year (spring), so the season in
progress — and a just-finished one — is only in NHC's operational ATCF
b-decks. Those seasons are added as `preliminary` and kept out of every
correlation until HURDAT2 replaces them.

Storm counts follow NHC convention: a storm counts as named / hurricane /
major on its peak wind while tropical or subtropical, not while extratropical.
"""

import csv, io, json, math, re, statistics, urllib.request
from pathlib import Path
from datetime import datetime, timezone

OUT_PATH     = Path(__file__).parent.parent / "public_data_hurricanes_gold.json"
HURDAT_INDEX = "https://www.nhc.noaa.gov/data/hurdat/"
TNA_URL      = "https://psl.noaa.gov/data/correlation/tna.data"
GISTEMP_URL  = "https://data.giss.nasa.gov/gistemp/tabledata_v4/GLB.Ts+dSST.csv"
BTK_INDEX    = "https://ftp.nhc.noaa.gov/atcf/btk/"

CORR_START_YEAR = 1950          # well-observed window (recon since 1940s, sats since 1966)
NORMAL_WINDOW   = (1991, 2020)  # NOAA's climate normal for season comparisons
TS_WIND, HU_WIND, MAJOR_WIND, RI_DELTA = 34, 64, 96, 30
SYNOPTIC   = {"0000", "0600", "1200", "1800"}
ACE_STATUS = {"TS", "HU", "SS"}
NAMED_STATUS = {"TS", "HU", "SS"}           # tropical/subtropical storm or hurricane
TROPICAL     = {"TD", "TS", "HU", "SD", "SS"}  # stages RI is measured over

# Storms with a Houston-area landfall worth a closer look. Track numbers are
# computed from HURDAT2 each run; the notes are curated from NHC reports and
# peer-reviewed / rapid attribution studies, each with its source.
FEATURED = {
    "AL092017": {
        "headline": "Hurricane Harvey · Aug 2017",
        "notes": [
            {"text": "Made landfall near Rockport as a Category 4, then stalled over southeast Texas for days. "
                     "Peak storm total 60.58 in near Nederland — the wettest tropical cyclone on record in the U.S.",
             "source": "NHC Tropical Cyclone Report AL092017",
             "url": "https://www.nhc.noaa.gov/data/tcr/AL092017_Harvey.pdf"},
            {"text": "At least 68 direct deaths in Texas, over half in Harris County, almost all from freshwater "
                     "flooding; about $125 billion in damage, tied with Katrina as the costliest U.S. tropical cyclone.",
             "source": "NHC Tropical Cyclone Report AL092017",
             "url": "https://www.nhc.noaa.gov/data/tcr/AL092017_Harvey.pdf"},
            {"text": "Climate signal: human-caused warming likely raised Harvey's Houston-area rainfall by at least "
                     "19% (best estimate ~38%) and made it at least 3.5× more likely.",
             "source": "Risser & Wehner 2017, Geophysical Research Letters",
             "url": "https://escholarship.org/uc/item/0k28z28b"},
            {"text": "A second, independent study found the record 3-day rainfall over Houston was about 15% more "
                     "intense and roughly 3× more likely than in the early 1900s.",
             "source": "van Oldenborgh et al. 2017 (World Weather Attribution), Environmental Research Letters",
             "url": "https://www.preventionweb.net/news/human-caused-warming-likely-intensified-hurricane-harveys-rains"},
        ],
        "caveat": "Both studies attribute Harvey's rainfall, not its formation or wind strength.",
    },
    "AL022024": {
        "headline": "Hurricane Beryl · Jul 2024",
        "notes": [
            {"text": "Earliest Category 5 on record in the Atlantic (2 July 2024) and the earliest Category 4, after "
                     "gaining 55 kt in 24 hours. Made landfall near Matagorda, Texas on 8 July as an 80-kt Category 1.",
             "source": "NHC Tropical Cyclone Report AL022024",
             "url": "https://www.nhc.noaa.gov/data/tcr/AL022024_Beryl.pdf"},
            {"text": "About 2.7 million people lost power in southeastern Texas. 14 direct U.S. deaths (11 in Texas) and "
                     "34 indirect deaths in Texas — 14 of them heat-related during the outages. ~$7.2 billion in U.S. damage.",
             "source": "NHC Tropical Cyclone Report AL022024",
             "url": "https://www.nhc.noaa.gov/data/tcr/AL022024_Beryl.pdf"},
            {"text": "Climate signal: Beryl intensified over water 2–3°F warmer than normal — temperatures usually seen in "
                     "September — that human-caused climate change made 100–400× more likely.",
             "source": "Climate Central, Climate Shift Index: Ocean (Aug 2024)",
             "url": "https://climatecentral.org/report/hurricane-beryl"},
            {"text": "A rapid analogue study found similar storms are now up to 30% wetter and up to 10% windier than "
                     "in the past, with natural variability (AMO, PDO) also playing an important role.",
             "source": "ClimaMeter (Faranda, Alberti & Camargo 2024)",
             "url": "https://www.climameter.org/20240703-hurricane-beryl"},
        ],
        "caveat": "The ocean-warmth and analogue results are rapid assessments, not peer-reviewed attribution of Beryl's intensity.",
    },
}
UA = "ClimatePulse/1.0 (portfolio analytics)"


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ── HURDAT2 ──────────────────────────────────────────────────────────────────
def latest_hurdat_url(index_html):
    files = re.findall(r'hurdat2-1851-\d{4}-\d+\.txt', index_html)
    if not files:
        raise RuntimeError("no HURDAT2 Atlantic file in index")
    key = lambda f: (int(re.match(r'hurdat2-1851-(\d{4})', f).group(1)),
                     re.match(r'hurdat2-1851-\d{4}-(\d+)', f).group(1))
    return HURDAT_INDEX + sorted(set(files), key=key)[-1]


def _int(v, default=-99):
    try:    return int(v)
    except (TypeError, ValueError): return default


def _coord(v):
    """'25.5N' / '255N' (tenths, ATCF) → signed float degrees."""
    v = v.strip()
    if not v or v[-1] not in "NSEW":
        return None
    num = float(v[:-1]) if "." in v else int(v[:-1]) / 10.0
    return round(-num if v[-1] in "SW" else num, 1)


# obs tuple: (datetime, status, wind_kt, "HHMM", record_id, pressure_mb, lat, lon)
# record_id is HURDAT2's single-letter flag; "L" marks a landfall.
def parse_hurdat2(text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    i = 0
    while i < len(lines):
        p = [x.strip() for x in lines[i].split(",")]
        if re.match(r'^[A-Z]{2}\d{6}$', p[0]):
            sid, year, n = p[0], int(p[0][4:8]), int(p[2])
            obs = []
            for j in range(1, n + 1):
                d = [x.strip() for x in lines[i + j].split(",")]
                dt = datetime.strptime(d[0] + d[1], "%Y%m%d%H%M")
                obs.append((dt, d[3], _int(d[6]), d[1], d[2], _int(d[7]),
                            _coord(d[4]), _coord(d[5])))
            yield {"id": sid, "name": p[1].title(), "year": year, "obs": obs}
            i += n + 1
        else:
            i += 1


# ── ATCF b-decks (operational best track) ────────────────────────────────────
def btk_files(index_html, year):
    """Atlantic cyclone b-decks for `year`. Numbers 01–49 are real cyclones;
    80s are tests and 90s are invests, which never count."""
    names = set(re.findall(r'bal(\d{2})%d\.dat' % year, index_html))
    return [f"bal{n}{year}.dat" for n in sorted(names) if 1 <= int(n) <= 49]


def parse_btk(text):
    """One b-deck → a storm in the same shape as parse_hurdat2's. A b-deck has
    one line per wind-radii threshold (34/50/64 kt), so times repeat; the first
    line for each time carries the same position and intensity."""
    seen, obs, sid, name = set(), [], None, ""
    for ln in text.splitlines():
        d = [x.strip() for x in ln.split(",")]
        if len(d) < 11 or d[4] != "BEST" or not re.match(r'^\d{10}$', d[2]):
            continue
        if d[2] in seen:
            continue
        seen.add(d[2])
        dt = datetime.strptime(d[2], "%Y%m%d%H")
        sid = sid or f"{d[0]}{int(d[1]):02d}{d[2][:4]}"
        if len(d) > 27 and d[27] and not d[27].upper().startswith(("INVEST", "NONAME")):
            name = d[27].title()
        obs.append((dt, d[10], _int(d[8]), dt.strftime("%H%M"), "", _int(d[9]),
                    _coord(d[6]), _coord(d[7])))
    if not obs:
        return None
    obs.sort()
    return {"id": sid, "name": name, "year": obs[0][0].year, "obs": obs}


def fetch_operational(fetch, years):
    """Storms for seasons HURDAT2 doesn't cover yet. Fails soft per season:
    a missing b-deck must never take the reanalysed history down with it."""
    out = {}
    try:
        index = fetch(BTK_INDEX)
    except Exception as e:
        print(f"⚠ ATCF b-deck index unavailable ({e}); skipping preliminary seasons")
        return out
    for y in years:
        storms = []
        for f in btk_files(index, y):
            try:
                s = parse_btk(fetch(BTK_INDEX + f))
                if s:
                    storms.append(s)
            except Exception as e:
                print(f"⚠ {f}: {e}")
        if storms:
            out[y] = storms
            print(f"ATCF b-decks: {len(storms)} cyclones for {y} (preliminary)")
    return out


# Atlantic/East Pacific divide: the Pacific coastline of Central America and
# Mexico as (lat, lon) knots, linearly interpolated. A fix west of the line is
# over Pacific water; anything east of it — including a storm crossing land —
# stays Atlantic. HURDAT2 keeps the full track of storms that cross over
# (Bonnie 2022 became a major hurricane in the East Pacific), and NHC's season
# totals and ACE leave those Pacific stages out.
ISTHMUS = [(0.0, -78.0), (7.5, -78.2), (8.3, -83.0), (9.6, -84.9), (11.0, -85.9),
           (12.2, -86.9), (13.2, -87.6), (13.5, -89.5), (14.0, -91.0), (14.6, -92.3),
           (15.7, -93.7), (16.2, -95.2), (16.6, -99.0), (17.6, -101.5), (18.2, -103.5),
           (19.5, -105.2), (21.5, -105.4), (23.2, -106.4), (24.0, -107.0)]


def in_atlantic(lat, lon):
    if lat is None or lon is None or lat >= ISTHMUS[-1][0]:
        return True
    for (la0, lo0), (la1, lo1) in zip(ISTHMUS, ISTHMUS[1:]):
        if la0 <= lat < la1:
            edge = lo0 + (lo1 - lo0) * (lat - la0) / (la1 - la0)
            return lon >= edge
    return True


def atlantic_obs(obs):
    return [o for o in obs if in_atlantic(o[6], o[7])]


def max_24h_gain(obs):
    """Largest 24-hour wind increase over tropical/subtropical synoptic fixes:
    (gain_kt, start, end) or None. A storm "underwent RI" if gain >= 30 kt."""
    syn = sorted([(o[0], o[2]) for o in obs if o[3] in SYNOPTIC and o[1] in TROPICAL and o[2] > 0])
    best = None
    for a in range(len(syn)):
        for b in range(a + 1, len(syn)):
            hrs = (syn[b][0] - syn[a][0]).total_seconds() / 3600.0
            if hrs > 24.5:
                break
            if abs(hrs - 24.0) <= 0.5:
                gain = syn[b][1] - syn[a][1]
                if best is None or gain > best[0]:
                    best = (gain, syn[a][0], syn[b][0])
    return best


def underwent_ri(obs):
    g = max_24h_gain(obs)
    return bool(g and g[0] >= RI_DELTA)


def peak_tropical(obs, statuses):
    winds = [o[2] for o in obs if o[1] in statuses and o[2] > 0]
    return max(winds) if winds else 0


def annual_metrics(storms):
    years = {}
    for s in storms:
        rec = years.setdefault(s["year"],
              {"named": 0, "hurricanes": 0, "major": 0, "ace": 0.0, "ri_storms": 0})
        obs = atlantic_obs(s["obs"])
        # Peak while tropical/subtropical only: a storm that hits 65 kt after
        # going extratropical is not a hurricane (this over-counted 2025 by one).
        rec["named"]      += peak_tropical(obs, NAMED_STATUS) >= TS_WIND
        hu_peak = peak_tropical(obs, {"HU"})
        rec["hurricanes"] += hu_peak >= HU_WIND
        rec["major"]      += hu_peak >= MAJOR_WIND
        for o in obs:
            if o[3] in SYNOPTIC and o[1] in ACE_STATUS and o[2] >= TS_WIND:
                rec["ace"] += o[2] * o[2]
        rec["ri_storms"]  += underwent_ri(obs)
    for y in years:
        years[y]["ace"] = round(years[y]["ace"] / 10000.0, 2)
    return years


def storm_ace(obs):
    return round(sum(o[2] ** 2 for o in atlantic_obs(obs)
                     if o[3] in SYNOPTIC and o[1] in ACE_STATUS and o[2] >= TS_WIND) / 10000.0, 2)


def saffir_simpson(kt):
    for cat, lo in ((5, 137), (4, 113), (3, 96), (2, 83), (1, 64)):
        if kt >= lo:
            return cat
    return 0


def featured_storm(s, season, tna, ace_by_year):
    """Track facts for one storm, computed from its best track, plus where its
    season sits in the record. Notes and sources come from FEATURED."""
    o = s["obs"]
    trop = [x for x in o if x[1] in TROPICAL]
    peak = max(trop, key=lambda x: x[2]) if trop else max(o, key=lambda x: x[2])
    pres = [x[5] for x in o if x[5] and x[5] > 0]
    gain = max_24h_gain(o)
    iso = lambda dt: dt.strftime("%Y-%m-%dT%H:%MZ")

    def rank(by_year, y):
        """1 = highest since CORR_START_YEAR (reanalysed seasons only)."""
        vals = {k: v for k, v in by_year.items() if k >= CORR_START_YEAR and v is not None}
        if y not in vals:
            return None
        return 1 + sum(v > vals[y] for v in vals.values())

    n_seasons = sum(1 for y in ace_by_year if y >= CORR_START_YEAR)
    meta = FEATURED.get(s["id"], {})
    return {
        "id": s["id"], "name": s["name"], "year": s["year"],
        "headline": meta.get("headline"),
        "first": iso(o[0][0]), "last": iso(o[-1][0]),
        "peak_kt": peak[2], "peak_category": saffir_simpson(peak[2]), "peak_time": iso(peak[0]),
        "min_pressure_mb": min(pres) if pres else None,
        "max_24h_gain_kt": gain[0] if gain else None,
        "max_24h_gain_window": [iso(gain[1]), iso(gain[2])] if gain else None,
        "rapid_intensification": bool(gain and gain[0] >= RI_DELTA),
        "storm_ace": storm_ace(o),
        "landfalls": [{"time": iso(x[0]), "kt": x[2], "category": saffir_simpson(x[2]),
                       "pressure_mb": x[5] if x[5] > 0 else None, "lat": x[6], "lon": x[7]}
                      for x in o if x[4] == "L"],
        "season": {
            "ace": season.get("ace"), "named": season.get("named"),
            "hurricanes": season.get("hurricanes"), "major": season.get("major"),
            "ace_rank_since_1950": rank(ace_by_year, s["year"]),
            "tna_sst_aso": tna.get(s["year"]),
            "tna_sst_rank_since_1950": rank(tna, s["year"]),
            "n_seasons": n_seasons,
        },
        "notes": meta.get("notes", []),
        "caveat": meta.get("caveat"),
    }


# ── SST / temperature anomalies ──────────────────────────────────────────────
def parse_psl_series(text):
    out = {}
    for ln in text.splitlines():
        t = ln.split()
        if len(t) < 13:
            continue
        try:    year = int(t[0])
        except ValueError: continue
        if not (1800 <= year <= 2100):
            continue
        out[year] = [None if float(v) <= -99 else float(v) for v in t[1:13]]
    return out


def tna_aso(psl):
    out = {}
    for y, v in psl.items():
        aso = [v[m] for m in (7, 8, 9) if v[m] is not None]   # Aug, Sep, Oct
        if len(aso) == 3:
            out[y] = round(sum(aso) / 3.0, 3)
    return out


def parse_gistemp(text):
    out, header = {}, None
    for row in csv.reader(io.StringIO(text)):
        if row and row[0].strip() == "Year":
            header = [c.strip() for c in row]; continue
        if header and row and re.match(r'^\d{4}$', row[0].strip()):
            try:    out[int(row[0])] = float(row[header.index("J-D")].strip())
            except (ValueError, IndexError): pass
    return out


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


def corr(metric_by_year, driver_by_year, exclude=()):
    # `exclude`: preliminary seasons — operational data, possibly incomplete
    yrs = [y for y in sorted(metric_by_year)
           if y >= CORR_START_YEAR and y in driver_by_year and y not in exclude]
    r = pearson([driver_by_year[y] for y in yrs], [metric_by_year[y] for y in yrs])
    return {"r": round(r, 3) if r is not None else None,
            "n": len(yrs), "window": [min(yrs), max(yrs)] if yrs else None}


# ── Main ─────────────────────────────────────────────────────────────────────
def normals(metrics):
    lo, hi = NORMAL_WINDOW
    yrs = [y for y in metrics if lo <= y <= hi]
    if len(yrs) < hi - lo + 1:
        return None
    avg = lambda k: round(statistics.mean(metrics[y][k] for y in yrs), 1)
    return {"window": [lo, hi], "named": avg("named"), "hurricanes": avg("hurricanes"),
            "major": avg("major"), "ace": avg("ace")}


def main(fetch=_get, now=None):
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    hurdat_url = latest_hurdat_url(fetch(HURDAT_INDEX))
    print(f"HURDAT2: {hurdat_url.rsplit('/', 1)[-1]}")
    storms  = list(parse_hurdat2(fetch(hurdat_url)))
    metrics = annual_metrics(storms)
    tna  = tna_aso(parse_psl_series(fetch(TNA_URL)))
    glob = parse_gistemp(fetch(GISTEMP_URL))

    # Seasons after HURDAT2's last year, up to the current one (from May —
    # pre-season storms are rare but real). Usually just the current season.
    last_reanalysed = max(metrics)
    last_wanted = now.year if now.month >= 5 else now.year - 1
    prelim_storms = fetch_operational(fetch, range(last_reanalysed + 1, last_wanted + 1))
    prelim = {}
    for y, ss in prelim_storms.items():
        prelim[y] = annual_metrics(ss)[y]
        prelim[y]["storms"] = [{
            "id": s["id"], "name": s["name"],
            "peak_kt": max([peak_tropical(s["obs"], NAMED_STATUS | {"TD", "SD"})] + [0]),
            "ace": storm_ace(s["obs"]), "ri": underwent_ri(s["obs"]),
        } for s in ss]
    metrics.update(prelim)
    excl = set(prelim)

    ace   = {y: metrics[y]["ace"] for y in metrics}
    major = {y: metrics[y]["major"] for y in metrics}
    ri    = {y: metrics[y]["ri_storms"] for y in metrics}
    reanalysed_ace = {y: v for y, v in ace.items() if y not in excl}

    by_id = {s["id"]: s for s in storms}
    featured = [featured_storm(by_id[sid], metrics[by_id[sid]["year"]], tna, reanalysed_ace)
                for sid in FEATURED if sid in by_id]

    latest = max(metrics)
    result = {
        "generated_at":      now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_file":       hurdat_url.rsplit("/", 1)[-1],
        "reanalysed_through": last_reanalysed,
        "preliminary_years": sorted(excl),
        "corr_window_start": CORR_START_YEAR,
        "normals":           normals(metrics),
        "correlations": {
            "ace_vs_tna_sst":       corr(ace, tna, excl),
            "major_vs_tna_sst":     corr(major, tna, excl),
            "ri_storms_vs_tna_sst": corr(ri, tna, excl),
            "ace_vs_global_temp":   corr(ace, glob, excl),
        },
        "caveat": ("Correlation reflects association, not attribution. Annual "
                   "Atlantic activity is strongly modulated by ENSO and the AMO; "
                   "formal attribution needs counterfactual potential-intensity "
                   "modeling."),
        "latest_season": {
            "year": latest, "preliminary": latest in excl,
            "complete": latest < now.year or (now.month, now.day) > (11, 30),
            "source": "NHC ATCF operational best track" if latest in excl else hurdat_url.rsplit("/", 1)[-1],
            "storms": metrics[latest].get("storms"),
        },
        "featured": featured,
        "series": [{
            "year": y, "named": metrics[y]["named"],
            "hurricanes": metrics[y]["hurricanes"], "major": metrics[y]["major"],
            "ace": metrics[y]["ace"], "ri_storms": metrics[y]["ri_storms"],
            "tna_sst_aso": tna.get(y), "global_temp": glob.get(y),
            **({"preliminary": True} if y in excl else {}),
        } for y in sorted(metrics)],
    }

    if len(result["series"]) < 50:
        print("Too few seasons — aborting."); return
    OUT_PATH.write_text(json.dumps(result, separators=(",", ":")))
    r = result["correlations"]["ace_vs_tna_sst"]["r"]
    print(f"✅ {len(result['series'])} seasons written to {OUT_PATH}")
    print(f"   ACE↔TNA-SST r={r} (n={result['correlations']['ace_vs_tna_sst']['n']})")
    for y in sorted(metrics)[-3:]:
        m = metrics[y]
        print(f"   {y}{' (prelim)' if y in excl else ''}: {m['named']} named, {m['hurricanes']} hurricanes, "
              f"{m['major']} major, ACE {m['ace']}, RI {m['ri_storms']}")
    for f in featured:
        print(f"   {f['name']} {f['year']}: peak {f['peak_kt']} kt (Cat {f['peak_category']}), "
              f"max 24h gain {f['max_24h_gain_kt']} kt, landfalls {len(f['landfalls'])}")


if __name__ == "__main__":
    main()
