"""
dc_staleness_check.py — decides whether the curated Data Centers figures are due
for a manual refresh. Never edits data. Emits a decision + issue body; the workflow
opens/de-dupes a GitHub issue from that.

Logic:
  1. Read the dashboard's data vintage (generated_at in api/datacenters.js).
  2. If older than STALE_MONTHS (default 6) -> due.
  3. Best-effort: fetch IEA / LBNL pages and note any year newer than the recorded
     baseline as a "possible new report" hint (soft signal, never gates on its own).
Stdlib only.
"""

import os, re, json, urllib.request
from pathlib import Path
from datetime import datetime, timezone

STALE_MONTHS = int(os.environ.get("STALE_MONTHS", "6"))
# Read the deployed data vintage from the live endpoint — no site-repo file access needed.
LIVE_URL = os.environ.get("DC_LIVE_URL", "https://www.yashhooda.ai/api/datacenters")
UA = "ClimatePulse/1.0 (portfolio staleness check)"

SOURCES = [
    ("IEA — Energy & AI", "https://www.iea.org/reports/energy-and-ai", 2025),
    ("LBNL — U.S. Data Center Energy Usage Report", "https://www.energy.gov/eere/buildings/articles/2024-united-states-data-center-energy-usage-report", 2024),
]


def _fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def data_vintage():
    """generated_at from the live /api/datacenters payload."""
    try:
        payload = json.loads(_fetch(LIVE_URL))
        raw = payload.get("generated_at")
        return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
    except Exception as e:
        print(f"(couldn't read live vintage from {LIVE_URL}: {e})")
        return None


def months_between(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month)


def report_hint(url, threshold_year, timeout=30):
    try:
        text = _fetch(url, timeout)
    except Exception as e:
        return f"(couldn't check page: {e})", False
    years = [int(y) for y in re.findall(r"\b(20[2-4]\d)\b", text)]
    newest = max(years) if years else None
    if newest and newest > threshold_year:
        return f"page mentions {newest} (data last refreshed for {threshold_year}) — possible newer edition", True
    return f"nothing newer than {threshold_year} detected", False


def main():
    now = datetime.now(timezone.utc)
    vintage = data_vintage()
    if vintage is None:
        # Couldn't determine vintage (endpoint down / not deployed yet) — skip, don't spam.
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write("due=false\n")
        print("vintage unknown — skipping this run (no reminder).")
        return
    age = months_between(vintage, now)
    due_by_time = age >= STALE_MONTHS
    vintage_year = vintage.year

    lines = []
    hint_flag = False
    for name, url, base in SOURCES:
        threshold = max(base, vintage_year)          # only flag editions beyond our last refresh
        text, flagged = report_hint(url, threshold)
        hint_flag = hint_flag or flagged
        lines.append(f"- **{name}** — {text}\n  {url}")

    due = due_by_time or hint_flag
    vintage_str = vintage.date().isoformat() if vintage else "unknown"

    body = (
        f"The curated Data Centers dashboard figures were last refreshed **{vintage_str}** "
        f"(~{age} months ago; threshold {STALE_MONTHS}).\n\n"
        f"**Why this issue:** {'data is past the refresh window' if due_by_time else 'a source may have a newer edition'}.\n\n"
        f"### Source check\n" + "\n".join(lines) + "\n\n"
        f"### Refresh checklist (edit the SEED in `api/datacenters.js`)\n"
        f"- [ ] Global demand series + 2030/2035 projection (IEA Energy & AI)\n"
        f"- [ ] Global / U.S. share percentages\n"
        f"- [ ] Top U.S. market capacities in MW (CBRE)\n"
        f"- [ ] Hyperscaler water withdrawals (company sustainability reports)\n"
        f"- [ ] Per-query energy ranges & PUE\n"
        f"- [ ] `us_dc_twh_2023` / `us_dc_2028_range_pct` (LBNL)\n"
        f"- [ ] Bump `generated_at` after updating\n\n"
        f"_Automated reminder — no data was changed. Close when reviewed._"
    )
    Path("dc_refresh_issue.md").write_text(body)

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"due={'true' if due else 'false'}\n")
    print(f"vintage={vintage_str} age={age}mo due_by_time={due_by_time} hint={hint_flag} -> DUE={due}")


if __name__ == "__main__":
    main()
