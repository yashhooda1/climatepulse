"""
ClimatePulse — Hurricane analytics pipeline (Atlantic basin)
Correlates annual Atlantic hurricane activity with (1) tropical North Atlantic
sea-surface temperature — the water storms actually intensify over — and
(2) global temperature. Writes public_data_hurricanes_gold.json at repo root.

Independent of climate_pipeline.py. Stdlib only (no NOAA_TOKEN, no pandas).
Sources (NOAA/NASA, public domain):
  HURDAT2 Atlantic best-track   https://www.nhc.noaa.gov/data/hurdat/  (latest auto-discovered)
  TNA SST anomaly (5-25N,55-15W) https://psl.noaa.gov/data/correlation/tna.data
  GISTEMP global L-OTI anomaly   https://data.giss.nasa.gov/gistemp/tabledata_v4/GLB.Ts+dSST.csv
"""

import csv, io, json, math, re, statistics, urllib.request
from pathlib import Path
from datetime import datetime

OUT_PATH     = Path(__file__).parent.parent / "public_data_hurricanes_gold.json"
HURDAT_INDEX = "https://www.nhc.noaa.gov/data/hurdat/"
TNA_URL      = "https://psl.noaa.gov/data/correlation/tna.data"
GISTEMP_URL  = "https://data.giss.nasa.gov/gistemp/tabledata_v4/GLB.Ts+dSST.csv"

CORR_START_YEAR = 1950          # well-observed window (recon since 1940s, sats since 1966)
TS_WIND, HU_WIND, MAJOR_WIND, RI_DELTA = 34, 64, 96, 30
SYNOPTIC   = {"0000", "0600", "1200", "1800"}
ACE_STATUS = {"TS", "HU", "SS"}
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
                try:    wind = int(d[6])
                except ValueError: wind = -99
                obs.append((dt, d[3], wind, d[1]))
            yield {"id": sid, "year": year, "obs": obs}
            i += n + 1
        else:
            i += 1


def underwent_ri(obs):
    syn = sorted([(dt, w) for (dt, st, w, hh) in obs if hh in SYNOPTIC and w > 0])
    for a in range(len(syn)):
        for b in range(a + 1, len(syn)):
            hrs = (syn[b][0] - syn[a][0]).total_seconds() / 3600.0
            if hrs > 24.5:
                break
            if abs(hrs - 24.0) <= 0.5 and (syn[b][1] - syn[a][1]) >= RI_DELTA:
                return True
    return False


def annual_metrics(storms):
    years = {}
    for s in storms:
        rec = years.setdefault(s["year"],
              {"named": 0, "hurricanes": 0, "major": 0, "ace": 0.0, "ri_storms": 0})
        winds = [w for (_, _, w, _) in s["obs"] if w > 0]
        peak = max(winds) if winds else 0
        rec["named"]      += peak >= TS_WIND
        rec["hurricanes"] += peak >= HU_WIND
        rec["major"]      += peak >= MAJOR_WIND
        for (dt, st, w, hh) in s["obs"]:
            if hh in SYNOPTIC and st in ACE_STATUS and w >= TS_WIND:
                rec["ace"] += w * w
        rec["ri_storms"]  += underwent_ri(s["obs"])
    for y in years:
        years[y]["ace"] = round(years[y]["ace"] / 10000.0, 2)
    return years


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


def corr(metric_by_year, driver_by_year):
    yrs = [y for y in sorted(metric_by_year)
           if y >= CORR_START_YEAR and y in driver_by_year]
    r = pearson([driver_by_year[y] for y in yrs], [metric_by_year[y] for y in yrs])
    return {"r": round(r, 3) if r is not None else None,
            "n": len(yrs), "window": [min(yrs), max(yrs)] if yrs else None}


# ── Main ─────────────────────────────────────────────────────────────────────
def main(fetch=_get):
    hurdat_url = latest_hurdat_url(fetch(HURDAT_INDEX))
    print(f"HURDAT2: {hurdat_url.rsplit('/', 1)[-1]}")
    metrics = annual_metrics(list(parse_hurdat2(fetch(hurdat_url))))
    tna  = tna_aso(parse_psl_series(fetch(TNA_URL)))
    glob = parse_gistemp(fetch(GISTEMP_URL))

    ace   = {y: metrics[y]["ace"] for y in metrics}
    major = {y: metrics[y]["major"] for y in metrics}
    ri    = {y: metrics[y]["ri_storms"] for y in metrics}

    result = {
        "generated_at":      datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_file":       hurdat_url.rsplit("/", 1)[-1],
        "corr_window_start": CORR_START_YEAR,
        "correlations": {
            "ace_vs_tna_sst":       corr(ace, tna),
            "major_vs_tna_sst":     corr(major, tna),
            "ri_storms_vs_tna_sst": corr(ri, tna),
            "ace_vs_global_temp":   corr(ace, glob),
        },
        "caveat": ("Correlation reflects association, not attribution. Annual "
                   "Atlantic activity is strongly modulated by ENSO and the AMO; "
                   "formal attribution needs counterfactual potential-intensity "
                   "modeling."),
        "series": [{
            "year": y, "named": metrics[y]["named"],
            "hurricanes": metrics[y]["hurricanes"], "major": metrics[y]["major"],
            "ace": metrics[y]["ace"], "ri_storms": metrics[y]["ri_storms"],
            "tna_sst_aso": tna.get(y), "global_temp": glob.get(y),
        } for y in sorted(metrics)],
    }

    if len(result["series"]) < 50:
        print("Too few seasons — aborting."); return
    OUT_PATH.write_text(json.dumps(result, separators=(",", ":")))
    r = result["correlations"]["ace_vs_tna_sst"]["r"]
    print(f"✅ {len(result['series'])} seasons written to {OUT_PATH}")
    print(f"   ACE↔TNA-SST r={r} (n={result['correlations']['ace_vs_tna_sst']['n']})")


if __name__ == "__main__":
    main()
