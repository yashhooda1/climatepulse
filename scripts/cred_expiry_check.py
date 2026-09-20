#!/usr/bin/env python3
"""Credential expiry + liveness check for the ClimatePulse / METAR pipelines.

Lives in climatepulse (NOT the deployed site). Two jobs:

  1. Reads YASHHOODA_PAT's expiry from the GitHub-Authentication-Token-Expiration
     response header and reports how many days are left.
  2. Probes every other pipeline credential for liveness — a binary "does this
     still authenticate", since none of them expose an expiry date.

Stdlib only, to match co2_pipeline.py — no pip install step needed.

Emits `due` / `dead` step outputs and writes an issue body when action is needed.

Environment
-----------
WARN_DAYS     days of runway below which the PAT counts as due   (default 30)
SITE_REPO     repo the PAT must retain push access to            (default yashhooda1/yashhooda)
ISSUE_BODY    path to write the issue body markdown              (default cred_refresh_issue.md)
TIMEOUT       per-request timeout in seconds                     (default 20)

Credentials, all optional — an unset one is reported as "not configured":
YASHHOODA_PAT, NOAA_TOKEN, EIA_API_KEY, OPENAI_API_KEY,
UPSTASH_VECTOR_REST_URL, UPSTASH_VECTOR_REST_TOKEN
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

WARN_DAYS = int(os.environ.get("WARN_DAYS", "30"))
SITE_REPO = os.environ.get("SITE_REPO", "yashhooda1/yashhooda")
ISSUE_BODY = os.environ.get("ISSUE_BODY", "cred_refresh_issue.md")
TIMEOUT = int(os.environ.get("TIMEOUT", "20"))

NOW = dt.datetime.now(dt.timezone.utc)

# GitHub sends this as e.g. "2026-12-31 15:59:59 UTC" or "2026-12-31 15:59:59 +0000".
# Absent entirely for a classic PAT set to never expire.
EXPIRY_HEADER = "github-authentication-token-expiration"

OK, WARN, DEAD, SKIP = "ok", "warn", "dead", "skip"
# SOFT: could not determine. Almost always a transient upstream blip, so it is
# reported but never opens an issue — a monthly job should not page you because
# api.github.com had a bad thirty seconds.
SOFT = "soft"

ICON = {OK: "✅", WARN: "⚠️", DEAD: "❌", SKIP: "➖", SOFT: "❔"}


class Result:
    """One credential's verdict."""

    def __init__(self, name: str, status: str, detail: str, days_left=None):
        self.name = name
        self.status = status
        self.detail = detail
        self.days_left = days_left

    def line(self) -> str:
        return f"{ICON[self.status]} **{self.name}** — {self.detail}"


def request(url: str, headers: dict, method: str = "GET"):
    """Return (status, headers, body_bytes). Auth failures are results, not exceptions."""
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
    except Exception as e:  # DNS, TLS, timeout — upstream problem, not a dead credential
        return None, {}, str(e).encode()


def parse_expiry(raw: str):
    """Parse GitHub's expiry header. Returns an aware datetime, or None."""
    raw = raw.strip()
    # Normalise a trailing "UTC" into a numeric offset so one parse path handles both.
    raw = re.sub(r"\bUTC\b$", "+0000", raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = dt.datetime.strptime(raw, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed
        except ValueError:
            continue
    return None


def check_github_pat() -> list:
    """Expiry + push-access check for the PAT. Returns a list of Results."""
    token = os.environ.get("YASHHOODA_PAT", "").strip()
    if not token:
        return [Result("YASHHOODA_PAT", SKIP, "not configured in this repo's secrets")]

    hdrs = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "climatepulse-cred-check",
    }

    status, headers, body = request("https://api.github.com/user", hdrs)

    if status is None:
        return [Result("YASHHOODA_PAT", SOFT, f"could not reach api.github.com — {body.decode()[:120]}")]
    if status == 401:
        return [Result("YASHHOODA_PAT", DEAD, "**revoked or expired** — every gold-data push to the site is failing")]
    if status != 200:
        return [Result("YASHHOODA_PAT", SOFT, f"unexpected HTTP {status} from /user")]

    login = "?"
    try:
        login = json.loads(body).get("login", "?")
    except Exception:
        pass

    results = []
    raw = headers.get(EXPIRY_HEADER)

    if not raw:
        # No header => classic PAT with no expiration set.
        results.append(
            Result("YASHHOODA_PAT", OK, f"authenticates as `{login}`; no expiry set (classic PAT, never expires)")
        )
    else:
        expiry = parse_expiry(raw)
        if expiry is None:
            results.append(Result("YASHHOODA_PAT", WARN, f"authenticates as `{login}`, but expiry header was unparseable: `{raw}`"))
        else:
            days = (expiry - NOW).total_seconds() / 86400.0
            stamp = expiry.strftime("%Y-%m-%d")
            # Known upstream quirk: the header sometimes echoes the current time
            # rather than the real expiry. A token that just authenticated cannot
            # genuinely be expiring inside the next few minutes.
            if abs(days) < 0.02:
                results.append(
                    Result("YASHHOODA_PAT", WARN,
                           f"authenticates as `{login}`, but the expiry header returned ~now (`{raw}`) — "
                           "unreliable, check the expiry date in Settings by hand")
                )
            elif days < 0:
                results.append(Result("YASHHOODA_PAT", DEAD, f"expired {stamp} — site data pushes are failing", days))
            elif days <= WARN_DAYS:
                results.append(
                    Result("YASHHOODA_PAT", WARN,
                           f"expires **{stamp}** — {int(days)} days left (threshold {WARN_DAYS})", days)
                )
            else:
                results.append(Result("YASHHOODA_PAT", OK, f"authenticates as `{login}`; expires {stamp} ({int(days)} days left)", days))

    # Authenticating is not enough — the PAT must still be able to push to the site repo.
    status, _, body = request(f"https://api.github.com/repos/{SITE_REPO}", hdrs)
    if status == 200:
        try:
            can_push = json.loads(body).get("permissions", {}).get("push", False)
        except Exception:
            can_push = False
        if can_push:
            results.append(Result(f"PAT → {SITE_REPO}", OK, "push access confirmed"))
        else:
            results.append(Result(f"PAT → {SITE_REPO}", DEAD, "token is valid but **has lost push access** — gold-data pushes will fail"))
    elif status in (403, 404):
        results.append(Result(f"PAT → {SITE_REPO}", DEAD, f"repo not visible to this token (HTTP {status}) — scope was narrowed or revoked"))
    elif status is None:
        results.append(Result(f"PAT → {SITE_REPO}", SOFT, "could not reach api.github.com for the push-access check"))
    else:
        results.append(Result(f"PAT → {SITE_REPO}", SOFT, f"unexpected HTTP {status} on the push-access check"))

    return results


# Liveness probes. Each entry: (label, env var(s), url builder, header builder, dead-status set)
def probe(label, url, headers, dead_statuses=(401, 403)) -> Result:
    status, _, body = request(url, headers)
    if status is None:
        return Result(label, SOFT, f"endpoint unreachable — {body.decode(errors='replace')[:120]}")
    if status in dead_statuses:
        return Result(label, DEAD, f"**rejected (HTTP {status})** — key is invalid, revoked, or out of quota")
    if 200 <= status < 300:
        return Result(label, OK, "authenticates")
    return Result(label, SOFT, f"unexpected HTTP {status} — probably upstream, worth a look")


def check_others() -> list:
    results = []

    noaa = os.environ.get("NOAA_TOKEN", "").strip()
    if noaa:
        results.append(probe(
            "NOAA_TOKEN",
            "https://www.ncei.noaa.gov/cdo-web/api/v2/datasets?limit=1",
            {"token": noaa, "User-Agent": "climatepulse-cred-check"},
            dead_statuses=(400, 401, 403),  # CDO returns 400 on a bad token
        ))
    else:
        results.append(Result("NOAA_TOKEN", SKIP, "not configured"))

    eia = os.environ.get("EIA_API_KEY", "").strip()
    if eia:
        results.append(probe(
            "EIA_API_KEY",
            f"https://api.eia.gov/v2/?api_key={urllib.parse.quote(eia)}",
            {"User-Agent": "climatepulse-cred-check"},
            dead_statuses=(401, 403),
        ))
    else:
        results.append(Result("EIA_API_KEY", SKIP, "not configured"))

    openai = os.environ.get("OPENAI_API_KEY", "").strip()
    if openai:
        results.append(probe(
            "OPENAI_API_KEY",
            "https://api.openai.com/v1/models",
            {"Authorization": f"Bearer {openai}", "User-Agent": "climatepulse-cred-check"},
        ))
    else:
        results.append(Result("OPENAI_API_KEY", SKIP, "not configured"))

    up_url = os.environ.get("UPSTASH_VECTOR_REST_URL", "").strip().rstrip("/")
    up_tok = os.environ.get("UPSTASH_VECTOR_REST_TOKEN", "").strip()
    if up_url and up_tok:
        results.append(probe(
            "UPSTASH_VECTOR",
            f"{up_url}/info",
            {"Authorization": f"Bearer {up_tok}", "User-Agent": "climatepulse-cred-check"},
        ))
    else:
        results.append(Result("UPSTASH_VECTOR", SKIP, "not configured"))

    return results


def write_issue_body(results: list, path: str) -> None:
    bad = [r for r in results if r.status in (WARN, DEAD)]
    lines = [
        "The monthly credential check found something that needs attention.",
        "",
        "## Needs action",
        "",
    ]
    lines += [f"- {r.line()}" for r in bad]
    lines += [
        "",
        "## Full results",
        "",
    ]
    lines += [f"- {r.line()}" for r in results]
    lines += [
        "",
        "## Why this matters",
        "",
        "`YASHHOODA_PAT` is the only thing pushing gold data to the live site. When it lapses,",
        "`climate-refresh`, `co2-refresh` and `agent-context` all keep reporting green for every",
        "step before the push, while the dashboards silently freeze on stale data.",
        "",
        "## To renew",
        "",
        "1. GitHub → Settings → Developer settings → Personal access tokens.",
        f"2. Regenerate, keeping push access to `{SITE_REPO}`.",
        "3. Update the `YASHHOODA_PAT` secret in **climatepulse** (and anywhere else it is set).",
        "4. Re-run *Credential expiry check* to confirm, then close this issue.",
        "",
        f"<sub>Opened automatically by `scripts/cred_expiry_check.py` on {NOW.strftime('%Y-%m-%d')}.</sub>",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def emit(name: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")


def main() -> int:
    results = check_github_pat() + check_others()

    print("--- credential check ---")
    for r in results:
        print(f"{ICON[r.status]} {r.name}: {r.detail}")

    dead = [r for r in results if r.status == DEAD]
    warn = [r for r in results if r.status == WARN]
    soft = [r for r in results if r.status == SOFT]
    # SOFT results are deliberately excluded: an unreachable endpoint is an
    # upstream blip, and opening an issue for it trains you to ignore the issues.
    due = bool(dead or warn)

    emit("due", "true" if due else "false")
    emit("dead", "true" if dead else "false")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### Credential check\n\n")
            for r in results:
                fh.write(f"- {r.line()}\n")

    if due:
        write_issue_body(results, ISSUE_BODY)
        print(f"\nwrote {ISSUE_BODY} ({len(dead)} dead, {len(warn)} warning)")
    else:
        if soft:
            print(f"\nnothing actionable — {len(soft)} undetermined (upstream unreachable), no issue opened")
        else:
            print("\nall credentials healthy — no issue needed")

    return 0  # the workflow decides whether to fail; this script only reports


if __name__ == "__main__":
    sys.exit(main())
