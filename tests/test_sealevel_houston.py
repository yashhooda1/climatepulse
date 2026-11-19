"""Offline tests for the Houston Coast block in scripts/sealevel_pipeline.py.
Payloads mirror the shapes NOAA CO-OPS returns. No network.
Run: python -m unittest discover -s tests -v"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import sealevel_pipeline as sp  # noqa: E402


def monthly(rows):
    """rows: (year, month, msl_m)."""
    return {"metadata": {"id": "x"}, "data": [{"year": str(y), "month": str(m), "MSL": str(v)} for y, m, v in rows]}


TRENDS = {"count": 2, "SeaLvlTrends": [
    {"stationId": "1611400", "trend": 0.75, "trendError": 0.07, "trendUnits": "inches/decade"},
    {"stationId": "8771450", "trend": 2.61, "trendError": 0.08, "trendUnits": "inches/decade",
     "startDate": "01/15/1904", "endDate": "12/15/2025"},
]}

PROJ = {"SlrProjections": [
    {"scenario": s, "projectionYear": y, "projectionRsl": v, "projectionCiLow": v - 5, "projectionCiHigh": v + 5, "units": "cm"}
    for s, base in [("Intermediate", 1.0), ("Low", 0.5)]
    for y, v in [(2005, 0.0), (2050, 40.0 * base), (2100, 120.0 * base)]
]}


class Parsing(unittest.TestCase):
    def test_annual_needs_ten_months(self):
        rows = [(2020, m, 0.1) for m in range(1, 13)] + [(2021, m, 0.2) for m in range(1, 10)]
        a = sp.annual_msl(monthly(rows))
        self.assertEqual(list(a), [2020])
        self.assertEqual(a[2020], 100.0)

    def test_year_in_progress_needs_twelve_months(self):
        rows = [(2025, m, 0.1) for m in range(1, 13)] + [(2026, m, 0.3) for m in range(1, 11)]
        self.assertEqual(list(sp.annual_msl(monthly(rows), current_year=2026)), [2025])
        self.assertEqual(list(sp.annual_msl(monthly(rows))), [2025, 2026])

    def test_annual_skips_blank_values(self):
        rows = [(2020, m, 0.1) for m in range(1, 12)] + [(2020, 12, "")]
        self.assertEqual(sp.annual_msl(monthly(rows))[2020], 100.0)

    def test_trend_converts_inches_per_decade(self):
        t = sp.station_trend(TRENDS, "8771450")
        self.assertAlmostEqual(t["mm_yr"], 6.63, places=2)
        self.assertAlmostEqual(t["ft_century"], 2.17, places=2)  # NOAA's table lists 2.17 for Pier 21
        self.assertIsNone(sp.station_trend(TRENDS, "0000000"))

    def test_projections_ordered_in_metres(self):
        p = sp.station_projections(PROJ)
        self.assertEqual(list(p), ["Low", "Intermediate"])
        self.assertEqual(p["Intermediate"][-1], {"year": 2100, "m": 1.2, "lo": 1.15, "hi": 1.25})


class Build(unittest.TestCase):
    def test_build_houston_end_to_end(self):
        rows = [(y, m, (y - 1904) * 0.0066) for y in range(1904, 2026) for m in range(1, 13)]

        def fetch(url):
            if "sealvltrends" in url:
                return json.dumps(TRENDS)
            if "slr_projections" in url:
                return json.dumps(PROJ)
            if "station=8771450" in url:
                return json.dumps(monthly(rows))
            raise OSError("gauge down")

        h = sp.build_houston(fetch, 2026)
        self.assertEqual([g["id"] for g in h["gauges"]], ["8771450"])  # other gauges failed soft
        g = h["gauges"][0]
        self.assertEqual(g["annual"][0]["year"], 1904)
        self.assertEqual(g["rise_since_ike_mm"], round(6.63 * (2025 - 2008)))
        self.assertIn("Intermediate", g["projections"])
        self.assertEqual([p["key"] for p in h["places"]], ["galveston", "buffalo_bayou", "houston"])
        for place in h["places"]:
            for n in place["notes"]:
                self.assertTrue(n["url"].startswith("https://"), n)

    def test_everything_down_returns_none(self):
        def fetch(url):
            raise OSError("down")
        self.assertIsNone(sp.build_houston(fetch, 2026))


if __name__ == "__main__":
    unittest.main()
