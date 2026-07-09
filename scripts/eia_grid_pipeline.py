"""
eia_grid_pipeline.py — keeps the Data Centers dashboard's U.S.-grid context live.

Fetches U.S. total electricity net generation (all sectors) from the EIA API v2
(total-energy route, MSN ELETPUS, in billion kWh = TWh) and writes
public_data_us_grid_gold.json at repo root. The data-center TWh figures stay
curated (no machine-readable feed exists); this only refreshes the *denominator*
so "data centers as a share of the U.S. grid" stays anchored to current generation.

Requires an EIA API key (free: https://www.eia.gov/opendata/). Set repo secret
EIA_API_KEY. Stdlib only.
"""

import json, os, urllib.parse, urllib.request
from pathlib import Path
from datetime import datetime

OUT_PATH = Path(__file__).parent.parent / "public_data_us_grid_gold.json"
EIA_BASE = "https://api.eia.gov/v2/total-energy/data/"
UA = "ClimatePulse/1.0 (portfolio analytics)"


def fetch_eia(api_key, timeout=60):
    params = [
        ("api_key", api_key),
        ("frequency", "annual"),
        ("data[0]", "value"),
        ("facets[msn][]", "ELETPUS"),          # Electricity Net Generation, Total (All Sectors)
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "desc"),
        ("length", "60"),
    ]
    url = EIA_BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def parse_eia(payload):
    """EIA v2 JSON -> {year: twh} (ELETPUS is already billion kWh = TWh)."""
    rows = (payload.get("response") or {}).get("data") or []
    out = {}
    for row in rows:
        try:
            year = int(str(row["period"])[:4])
            val = float(row["value"])
        except (KeyError, ValueError, TypeError):
            continue
        out[year] = round(val, 1)
    return out


def main():
    api_key = os.environ.get("EIA_API_KEY")
    if not api_key:
        print("EIA_API_KEY not set — skipping (dashboard keeps last-good / curated context).")
        return
    try:
        annual = parse_eia(fetch_eia(api_key))
    except Exception as e:
        print(f"EIA fetch/parse failed ({e}) — skipping, last-good file preserved.")
        return
    if not annual:
        print("EIA returned no rows — skipping.")
        return

    years = sorted(annual)
    latest = years[-1]
    result = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "U.S. EIA API v2 · Total Energy · Electricity Net Generation, Total (All Sectors)",
        "unit": "TWh/yr",
        "latest_year": latest,
        "us_total_twh": annual[latest],
        "series": [{"year": y, "twh": annual[y]} for y in years],
    }
    OUT_PATH.write_text(json.dumps(result, separators=(",", ":")))
    print(f"OK U.S. grid {latest}: {annual[latest]} TWh ({len(years)} yrs) -> {OUT_PATH.name}")


if __name__ == "__main__":
    main()
