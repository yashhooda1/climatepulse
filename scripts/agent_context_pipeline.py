"""
agent_context_pipeline.py — ClimatePulse "Live Context" pipeline.

Refreshes the yashhooda.ai AI agent's knowledge from Yash's real activity so the
Running / Projects / General agents answer with CURRENT facts instead of stale
hardcoded ones. Pulls the last 7 days from Strava (running) and GitHub (coding),
distills them to compact summaries + structured fields, and writes
agent_context_gold.json at repo root. Stdlib only.

Consumed two ways:
  1. Structured injection — chat.js appends running.summary / coding.summary to the
     routed agent's system prompt (a few hundred tokens, always fresh).
  2. Dashboard — the "This Week" section renders the structured fields.
  (A separate embed step feeds the RAG layer into Upstash Vector.)

Secrets (climatepulse repo): STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET,
STRAVA_REFRESH_TOKEN, and optionally GITHUB_TOKEN (higher rate limit).
"""

import json, os, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta

OUT_PATH = Path(__file__).parent.parent / "agent_context_gold.json"
GH_USER  = "yashhooda1"
BOULDERTHON = datetime(2026, 9, 27, tzinfo=timezone.utc)
HOUSTON_MARATHON = datetime(2027, 1, 17, tzinfo=timezone.utc)
METERS_PER_MILE = 1609.34
UA = "ClimatePulse/1.0 (agent-context)"


def _get(url, headers=None, timeout=45):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _post_form(url, form, timeout=45):
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _pace(moving_time_s, meters):
    miles = meters / METERS_PER_MILE
    if miles <= 0 or moving_time_s <= 0:
        return None
    spm = moving_time_s / miles
    return f"{int(spm // 60)}:{int(spm % 60):02d}"


# ── STRAVA ────────────────────────────────────────────────────────────────────
def strava_access_token(fetch_post=_post_form):
    cid, secret, refresh = (os.environ.get("STRAVA_CLIENT_ID"),
                            os.environ.get("STRAVA_CLIENT_SECRET"),
                            os.environ.get("STRAVA_REFRESH_TOKEN"))
    if not (cid and secret and refresh):
        missing = [n for n, v in (("STRAVA_CLIENT_ID", cid), ("STRAVA_CLIENT_SECRET", secret),
                                  ("STRAVA_REFRESH_TOKEN", refresh)) if not v]
        print(f"Strava: not connected — missing secret(s): {', '.join(missing)}")
        return None
    tok = fetch_post("https://www.strava.com/oauth/token", {
        "client_id": cid, "client_secret": secret,
        "grant_type": "refresh_token", "refresh_token": refresh,
    })
    at = tok.get("access_token")
    if not at:
        print(f"Strava: token refresh returned no access_token (check app + 'activity:read_all' scope). "
              f"Response keys: {list(tok.keys())}")
    return at


def build_running(activities, now):
    """activities: list of Strava activity dicts (already fetched). -> running block."""
    runs = [a for a in activities if (a.get("type") or a.get("sport_type")) == "Run"]
    total_m = sum(a.get("distance", 0) for a in runs)
    week_miles = round(total_m / METERS_PER_MILE, 1)
    recent = []
    for a in sorted(runs, key=lambda x: x.get("start_date", ""), reverse=True)[:5]:
        miles = round(a.get("distance", 0) / METERS_PER_MILE, 1)
        recent.append({
            "date": (a.get("start_date", "") or "")[:10],
            "name": a.get("name", "Run"),
            "miles": miles,
            "pace": _pace(a.get("moving_time", 0), a.get("distance", 0)),
        })
    long_run = max((r["miles"] for r in recent), default=0)
    long_pace = next((r["pace"] for r in recent if r["miles"] == long_run), None)
    days_to = max(0, (BOULDERTHON - now).days)
    summary = (f"{week_miles} mi over the last 7 days across {len(runs)} run(s)"
               + (f"; longest {long_run} mi" + (f" @ {long_pace}/mi" if long_pace else "") if long_run else "")
               + f". {days_to} days to the Boulderthon marathon (Sep 27, 2026).")
    return {"week_miles": week_miles, "week_runs": len(runs), "recent": recent,
            "longest_run_miles": long_run, "days_to_boulderthon": days_to,
            "days_to_houston": max(0, (HOUSTON_MARATHON - now).days), "summary": summary}


# ── GITHUB ────────────────────────────────────────────────────────────────────
def build_coding(now, fetch=_get, headers=None):
    """Reliable coding activity: list repos by push time, then count Yash-authored
    commits per repo from the commits API. The public events feed is cached and
    abbreviates commit data, so we query repo state directly instead."""
    cutoff_iso = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    active = []
    repos = fetch(f"https://api.github.com/users/{GH_USER}/repos?sort=pushed&per_page=30&type=owner",
                  headers=headers)
    if isinstance(repos, list):
        for r in repos:
            if (r.get("pushed_at") or "") < cutoff_iso:
                break  # repos are sorted by pushed-desc — everything after is older
            name = r.get("name", "")
            commits = fetch(
                f"https://api.github.com/repos/{GH_USER}/{name}/commits"
                f"?since={cutoff_iso}&author={GH_USER}&per_page=100", headers=headers)
            n = len(commits) if isinstance(commits, list) else 0
            if n > 0:
                active.append({"name": name, "commits": n, "language": r.get("language")})
    active.sort(key=lambda x: x["commits"], reverse=True)
    active = active[:5]
    total = sum(a["commits"] for a in active)
    focus = ", ".join(a["name"] for a in active[:3]) or "no commits this week"
    if total:
        langs = sorted({a["language"] for a in active if a.get("language")})
        lang_str = f" ({', '.join(langs)})" if langs else ""
        summary = f"{total} commit(s) this week across {len(active)} repo(s){lang_str}; most active: {focus}."
    else:
        summary = "No GitHub commits authored in the last 7 days."
    return {"week_commits": total, "active_repos": active, "current_focus": focus, "summary": summary}


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main(strava_fetch=_get, strava_token=None, gh_fetch=_get):
    now = datetime.now(timezone.utc)
    after = int((now - timedelta(days=7)).timestamp())

    # Running
    running = {"week_miles": 0, "week_runs": 0, "recent": [],
               "summary": "Strava not connected — running data unavailable this week."}
    try:
        token = strava_token if strava_token is not None else strava_access_token()
        if token:
            acts = strava_fetch(
                f"https://www.strava.com/api/v3/athlete/activities?after={after}&per_page=50",
                headers={"Authorization": f"Bearer {token}", "User-Agent": UA})
            print(f"Strava: fetched {len(acts) if isinstance(acts, list) else '??'} activity(ies) in the last 7 days")
            running = build_running(acts, now)
    except Exception as e:
        print(f"Strava step failed ({e}) — keeping placeholder.")

    # Coding
    coding = {"week_commits": 0, "active_repos": [], "summary": "Coding data unavailable this week."}
    try:
        headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        coding = build_coding(now, fetch=gh_fetch, headers=headers)
        print(f"GitHub: {coding['week_commits']} commit(s) across {len(coding['active_repos'])} active repo(s)")
    except Exception as e:
        print(f"GitHub step failed ({e}) — keeping placeholder.")

    result = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Strava API (activities) · GitHub public events",
        "running": running,
        "coding": coding,
    }
    OUT_PATH.write_text(json.dumps(result, separators=(",", ":")))
    print(f"OK -> {OUT_PATH.name} | run: {running['summary']} | code: {coding['summary']}")
    return result


if __name__ == "__main__":
    main()
