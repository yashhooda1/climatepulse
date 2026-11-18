"""Offline tests for scripts/hurricane_pipeline.py — synthetic HURDAT2 and
ATCF b-deck text, no network. Run: python -m unittest discover -s tests -v"""
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import hurricane_pipeline as hp  # noqa: E402


def hurdat_storm(sid, name, fixes):
    """fixes: (YYYYMMDD, HHMM, rec, status, wind, pres)."""
    lines = [f"{sid},{name:>19},{len(fixes):>6},"]
    for ymd, hhmm, rec, st, w, p in fixes:
        lines.append(f"{ymd}, {hhmm}, {rec:>1}, {st}, 25.0N,  90.0W, {w:>3}, {p:>4},")
    return "\n".join(lines)


def btk_line(cy, ymdh, lat, lon, w, p, ty, rad="34", name="ISAIAS"):
    return (f"AL, {cy}, {ymdh},   , BEST,   0, {lat}, {lon}, {w:>3}, {p:>4}, {ty}, {rad:>3}, NEQ,"
            f"   60,    0,    0,   40, 1012,  150,  30,   0,   0,   L,   0,    ,   0,   0, {name:>10}, D,")


# A storm that only reaches hurricane force after going extratropical: it is a
# named storm, not a hurricane — the 2025 over-count this fixes.
EX_ONLY = hurdat_storm("AL012025", "TESTEX", [
    ("20250901", "0000", "", "TS", 45, 1000),
    ("20250901", "0600", "", "TS", 50, 998),
    ("20250901", "1200", "", "EX", 65, 985),
])
# A real hurricane with rapid intensification (+35 kt in 24 h) and a landfall.
RI_HU = hurdat_storm("AL022025", "TESTHU", [
    ("20250910", "0000", "", "TS", 50, 995),
    ("20250910", "0600", "", "TS", 60, 990),
    ("20250910", "1200", "", "HU", 70, 980),
    ("20250910", "1800", "", "HU", 80, 970),
    ("20250911", "0000", "", "HU", 100, 950),
    ("20250911", "0300", "L", "HU", 100, 951),
    ("20250911", "0600", "", "TS", 55, 990),
])


class Counts(unittest.TestCase):
    def setUp(self):
        self.storms = list(hp.parse_hurdat2(EX_ONLY + "\n" + RI_HU))

    def test_extratropical_peak_is_not_a_hurricane(self):
        m = hp.annual_metrics(self.storms)[2025]
        self.assertEqual(m["named"], 2)
        self.assertEqual(m["hurricanes"], 1)
        self.assertEqual(m["major"], 1)

    def test_ace_skips_extratropical_and_off_synoptic(self):
        m = hp.annual_metrics(self.storms)[2025]
        # EX_ONLY: 45²+50² ; RI_HU: 50²+60²+70²+80²+100²+55² (0300 landfall fix excluded)
        expect = (45**2 + 50**2 + 50**2 + 60**2 + 70**2 + 80**2 + 100**2 + 55**2) / 1e4
        self.assertAlmostEqual(m["ace"], round(expect, 2))

    def test_ri_and_landfall(self):
        hu = self.storms[1]
        self.assertTrue(hp.underwent_ri(hu["obs"]))
        self.assertEqual(hp.max_24h_gain(hu["obs"])[0], 50)
        self.assertFalse(hp.underwent_ri(self.storms[0]["obs"]))
        f = hp.featured_storm(hu, {"ace": 1.0}, {2025: 0.5}, {2025: 1.0})
        self.assertEqual(f["name"], "Testhu")
        self.assertEqual(f["peak_category"], 3)
        self.assertEqual(len(f["landfalls"]), 1)
        self.assertEqual(f["landfalls"][0]["kt"], 100)


class Basin(unittest.TestCase):
    def test_isthmus_divide(self):
        self.assertTrue(hp.in_atlantic(11.5, -82.0))    # western Caribbean
        self.assertTrue(hp.in_atlantic(16.0, -88.0))    # Gulf of Honduras
        self.assertTrue(hp.in_atlantic(19.0, -95.0))    # Bay of Campeche
        self.assertTrue(hp.in_atlantic(28.6, -96.0))    # Matagorda, TX
        self.assertTrue(hp.in_atlantic(9.7, -81.0))     # Panama, Caribbean side
        self.assertTrue(hp.in_atlantic(11.7, -85.5))    # Lake Nicaragua (land crossing)
        self.assertTrue(hp.in_atlantic(23.5, -99.5))    # inland NE Mexico after a Gulf landfall
        self.assertTrue(hp.in_atlantic(17.2, -96.4))    # inland Oaxaca
        self.assertFalse(hp.in_atlantic(11.0, -86.5))   # off Nicaragua's Pacific coast
        self.assertFalse(hp.in_atlantic(15.0, -100.0))  # East Pacific
        self.assertFalse(hp.in_atlantic(13.0, -90.0))   # off El Salvador

    def test_pacific_stage_of_a_crossover_does_not_count(self):
        # Bonnie-2022-style: TS in the Caribbean, major hurricane after crossing.
        text = "\n".join([
            "AL042022,             BONNIE,      4,",
            "20220701, 0000,  , TS, 11.5N,  80.0W,  40, 1005,",
            "20220701, 0600,  , TS, 11.2N,  82.0W,  45, 1004,",
            "20220705, 0000,  , HU, 13.5N,  95.0W, 100,  965,",
            "20220705, 0600,  , HU, 14.0N,  97.0W, 100,  964,",
        ])
        m = hp.annual_metrics(list(hp.parse_hurdat2(text)))[2022]
        self.assertEqual((m["named"], m["hurricanes"], m["major"]), (1, 0, 0))
        self.assertAlmostEqual(m["ace"], round((40**2 + 45**2) / 1e4, 2))


class BDecks(unittest.TestCase):
    INDEX = ('<a href="bal092026.dat">x</a> <a href="bal902026.dat">invest</a> '
             '<a href="bal012026.dat">y</a> <a href="bal012025.dat">old</a>')

    def test_index_skips_invests_and_other_years(self):
        self.assertEqual(hp.btk_files(self.INDEX, 2026), ["bal012026.dat", "bal092026.dat"])

    def test_parse_dedupes_wind_radii_lines(self):
        text = "\n".join([
            btk_line("09", "2026100600", "140N", "440W", 30, 1008, "TD", name="NINE"),
            btk_line("09", "2026100606", "142N", "445W", 40, 1004, "TS"),
            btk_line("09", "2026100606", "142N", "445W", 40, 1004, "TS", rad="50"),
            btk_line("09", "2026100612", "145N", "450W", 75, 985, "HU"),
            btk_line("09", "2026100612", "145N", "450W", 75, 985, "HU", rad="64"),
        ])
        s = hp.parse_btk(text)
        self.assertEqual(s["id"], "AL092026")
        self.assertEqual(s["name"], "Isaias")
        self.assertEqual(len(s["obs"]), 3)
        self.assertEqual(s["obs"][0][6], 14.0)
        self.assertEqual(s["obs"][0][7], -44.0)
        m = hp.annual_metrics([s])[2026]
        self.assertEqual((m["named"], m["hurricanes"]), (1, 1))
        self.assertAlmostEqual(m["ace"], round((40**2 + 75**2) / 1e4, 2))


class EndToEnd(unittest.TestCase):
    def test_preliminary_season_is_added_but_not_correlated(self):
        seasons = [hurdat_storm(f"AL01{y}", "S", [(f"{y}0901", "0000", "", "HU", 60 + y % 40, 990),
                                                   (f"{y}0901", "0600", "", "HU", 70 + y % 40, 980)])
                   for y in range(1950, 2026)]
        tna = "\n".join(f"{y} " + " ".join(["0.1"] * 7 + [f"{(y % 7) / 10:.2f}"] * 3 + ["0.1"] * 2)
                        for y in range(1948, 2027))
        gis = "Year,J-D\n" + "\n".join(f"{y},{(y - 1950) / 100:.2f}" for y in range(1950, 2026))
        btk = "\n".join([btk_line("09", "2026100606", "142N", "445W", 50, 1000, "TS"),
                         btk_line("09", "2026100612", "145N", "450W", 105, 959, "HU")])
        pages = {
            hp.HURDAT_INDEX: '<a href="hurdat2-1851-2025-040126.txt">',
            hp.HURDAT_INDEX + "hurdat2-1851-2025-040126.txt": "\n".join(seasons),
            hp.TNA_URL: tna, hp.GISTEMP_URL: gis,
            hp.BTK_INDEX: '<a href="bal092026.dat">', hp.BTK_INDEX + "bal092026.dat": btk,
        }
        with tempfile.TemporaryDirectory() as d:
            hp.OUT_PATH = Path(d) / "gold.json"
            hp.main(fetch=pages.__getitem__, now=datetime(2026, 11, 17))
            g = json.loads(hp.OUT_PATH.read_text())
        self.assertEqual(g["reanalysed_through"], 2025)
        self.assertEqual(g["preliminary_years"], [2026])
        self.assertEqual(g["series"][-1]["year"], 2026)
        self.assertTrue(g["series"][-1]["preliminary"])
        self.assertEqual(g["series"][-1]["major"], 1)
        self.assertEqual(g["correlations"]["ace_vs_tna_sst"]["window"], [1950, 2025])
        self.assertFalse(g["latest_season"]["complete"])
        self.assertEqual(g["latest_season"]["storms"][0]["name"], "Isaias")
        self.assertEqual(g["normals"]["window"], [1991, 2020])

    def test_btk_outage_keeps_reanalysed_history(self):
        def fetch(url):
            if url.startswith(hp.BTK_INDEX):
                raise OSError("down")
            return {hp.HURDAT_INDEX: '<a href="hurdat2-1851-2025-040126.txt">'}.get(url, "")
        self.assertEqual(hp.fetch_operational(fetch, [2026]), {})


if __name__ == "__main__":
    unittest.main()
