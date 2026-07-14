# ClimatePulse 🌎

The data-engineering backend for [yashhooda.ai](https://www.yashhooda.ai) — six scheduled pipelines that fetch public climate, energy, and activity data, distill it into "gold" JSON, and push it to the site repo for the live dashboards and the AI agent.

**This repo runs the pipelines. It is not deployed.** Only JSON data files ever cross into the site repo (`yashhooda1/yashhooda`) — no scripts, no workflows — so nothing here can affect the site's Vercel build.

```
   NOAA · NASA · EIA · Strava · GitHub          ← public sources
                  ↓
   climatepulse/scripts/*.py                    ← Bronze → Silver → Gold
                  ↓
   *_gold.json                                  ← committed to yashhooda repo
                  ↓
   yashhooda/api/*.js  →  live dashboards + AI agent
```

---

## 🔧 Pipelines

| Pipeline | Source | Output | Cadence |
|---|---|---|---|
| `climate_pipeline.py` | NOAA GHCN-Daily (13 stations) | `public_data_climate_gold.json` | Daily |
| `hurricane_pipeline.py` | HURDAT2 · NOAA PSL SST · NASA GISTEMP | `public_data_hurricanes_gold.json` | Daily |
| `sealevel_pipeline.py` | NOAA/STAR satellite altimetry | `public_data_sealevel_gold.json` | Daily |
| `eia_grid_pipeline.py` | EIA API v2 (`ELETPUS`) | `public_data_us_grid_gold.json` | Daily |
| `agent_context_pipeline.py` | Strava · GitHub | `agent_context_gold.json` | Weekly |
| `agent_embed.py` | ↑ + OpenAI embeddings | Upstash Vector upsert | Weekly |
| `dc_staleness_check.py` | IEA · LBNL pages | GitHub issue (reminder only) | Quarterly |

### 🌡 Climate — the core pipeline

**Bronze → Silver → Gold** across **13 NOAA stations**, 1970–present:

| Code | Station | GHCN ID | Climate |
|---|---|---|---|
| IAH | Houston, TX | `USW00012960` | Humid subtropical |
| EWR | Newark, NJ | `USW00014734` | Humid continental |
| DAL | Dallas, TX | `USW00013960` | Humid subtropical |
| DEN | Denver, CO | `USW00003017` | Semi-arid, 5,280 ft |
| ORD | Chicago, IL | `USW00094846` | Humid continental |
| LAX | Los Angeles, CA | `USW00023174` | Coastal Mediterranean |
| LHR | London, UK | `UKM00003772` | Maritime temperate |
| AMS | Amsterdam, NL | `NLE00152485` | Maritime (Schiphol, below sea level) |
| BRU | Brussels, BE | `BE000006447` | Maritime (Uccle — record since 1833) |
| CDG | Paris, FR | `FRM00007149` | Oceanic / continental (Orly) |
| FCO | Rome, IT | `IT000016239` | Mediterranean (Ciampino) |
| HEL | Helsinki, FI | `FI000000304` | Subarctic, 60°N (Kaisaniemi) |
| DEL | Delhi, India | `IN022021900` | Semi-arid monsoon |

- **Bronze** — raw NOAA daily records, fetched year by year (`units=standard`, so NOAA returns °F for international stations too)
- **Silver** — cleaned + feature-engineered: `tmean`, `winter_year`, 80°F-day flags, plus a sanity guard dropping physically impossible values
- **Gold** — annual/seasonal aggregations + linear-regression trend metrics per station

#### ⚠️ Adding a station: verify the ID first

**GHCN station IDs cannot be guessed.** The network-code letter varies by country — Finland uses `FI0…`, the Netherlands `NLE…`, France `FRM…`, Belgium `BE000…`, Italy `IT000…`. Many international stations also report only precipitation or mean temperature, with no `TMAX`/`TMIN`, which this pipeline requires. And a bad ID makes the run **abort for every city**, not just the new one.

So run the finder before touching `STATIONS`:

```bash
export NOAA_TOKEN=your_token
python find_stations.py        # discovers + TMAX-verifies candidates, prints paste-ready IDs
```

It queries NOAA's station catalog by country, filters to stations spanning 1970→now, probes each for `TMAX` at the start/middle/end of the record, and falls through to the next candidate if one has gaps.

#### 📉 Data-quality guards

- **Partial years are excluded.** A year needs data in all 12 months to produce an annual mean. Without this guard the in-progress current year is averaged over only the elapsed months — biasing the most recent point low by 1.5–3.5°F and creating a fake "cooling" hook at the end of the trend line that also drags the regression slope down.
- **Rome (Ciampino) has a stale feed.** Its GHCN record currently ends 2025-08-24, so Rome's latest complete year lags the others. The partial-year guard means it shows a missing point rather than a distorted one.
- **Sparse stations may show gaps.** Orly's data coverage is ~0.79, so some of its historical years fail the 12-month test. Charts use `spanGaps: true`, so gaps render cleanly — a missing point is more honest than a six-month "annual" average.

### 🌀 Hurricanes
HURDAT2 storm records joined to tropical-Atlantic SST and global temperature; computes ACE, major-hurricane counts, rapid-intensification events, and correlations since 1950. Framed as **association, not attribution**.

### 🌊 Sea level
NOAA/STAR satellite altimetry (GMSL since 1993), re-anchoring the 2100 projection fan to the latest observed rate. NOAA-2022 scenario targets (0.3–2.0 m) stay curated — they only change when NOAA issues a new technical report.

### ⚡ U.S. grid + data centers
The EIA feed keeps the *denominator* live (U.S. total net generation) so the Data Centers dashboard's "share of the grid" figure stays current. The data-center figures themselves are **curated by necessity** — no machine-readable feed exists (IEA/LBNL publish PDFs, CBRE is subscription). Rather than fake a pipeline that could inject wrong numbers, `dc_staleness_check.py` automates the *reminder*: it opens a GitHub issue when the figures pass a 6-month window or a source publishes a newer edition.

### 🤖 Agent context
Distills the last 7 days of Strava runs and GitHub commits into a compact summary, pushed to the site and embedded into the Upstash Vector index the site's AI chat already retrieves from — so the agent always knows what's currently being built and run, with no changes to `chat.js`.

---

## 📊 Key insights (climate)

From the current gold data (57 years, 1970–present):

| Metric | Value |
|---|---|
| Houston annual warming | **+0.79 °F / decade** |
| Newark annual warming | **+0.39 °F / decade** |
| Houston winter nighttime warming | **+1.02 °F / decade** |
| Houston extra 80°F days (Feb–Mar) | **+2.07 days / decade** |

Two patterns hold across stations: **winter nighttime lows warm faster than annual averages** — a greenhouse-gas fingerprint distinct from urban heat island effects — and low-latitude cities show the sharpest growth in extreme-heat days.

With 13 stations the dataset now spans a genuine climatic range: **Helsinki at 60°N** (high-latitude amplification, where warming should be fastest), through the **maritime European cluster** (Amsterdam, Brussels, Paris, London — sea-moderated, narrow seasonal swings), to **Mediterranean Rome**, **continental Chicago**, **coastal Los Angeles**, and **monsoon Delhi**. That spread is the point: the same regression run against very different climates.

> Dashboard "Signal" lines are **computed from this gold data at render time**, not hardcoded — so no figure in the UI can drift from what the pipeline actually produced. Per-decade numbers for newly added stations populate on their first full run.

---

## ⚙️ Automation (GitHub Actions)

| Workflow | Schedule | Does |
|---|---|---|
| `climate-refresh.yml` | Daily, 04:00 UTC | Climate + hurricane + sea level + EIA → push gold JSON to site repo |
| `agent-context.yml` | Mondays, 05:00 UTC | Strava + GitHub → push context JSON + embed into Upstash Vector |
| `datacenter-staleness.yml` | Quarterly | Opens a refresh-reminder issue if the curated figures are aging |

All pipeline steps are `continue-on-error` — one source having an outage never blocks the others — and every push is guarded by `git diff --staged --quiet`, so no empty commits.

### ⚡ Incremental fetching (why the quota matters)

NOAA's CDO API allows **1,000 requests/day**. A naive full refetch costs `stations × years` per run:

| Stations | Full refetch | Runtime |
|---|---|---|
| 6 | 342 calls | ~9 min |
| 9 | 513 calls | ~13 min |
| **13** | **741 calls** | **~19 min** |

At 13 stations on a daily cron that fits — but with almost no headroom. One manual re-run in the same day would blow the cap, and since the pipeline aborts when a station returns nothing, *every* city would stop refreshing.

The fix: **historical NOAA records are immutable.** Only the current year changes. So the pipeline caches daily rows in the gold file, reuses everything before the current year, and refetches only the current year — **741 calls → 13**, and runtime drops from ~19 minutes to under a minute. Set `FORCE_FULL=1` to force a complete rebuild (needed once after adding a station).

The current year is always refetched *in full* rather than "just the new days," because NOAA revises recent data for 45–60 days after month close.

**Cadence note:** NOAA GHCN updates roughly daily, so it's the real beneficiary of the daily cron. Sea level (~10-day cadence), EIA (monthly), and hurricanes (seasonal) no-op most days. The bigger win from running daily is **faster failure detection** — if a source changes format, we hear about it within a day instead of a week.

---

## 🔑 Secrets

Set as repository secrets (Settings → Secrets and variables → Actions):

| Secret | Used by |
|---|---|
| `NOAA_TOKEN` | Climate pipeline ([free token](https://www.ncdc.noaa.gov/cdo-web/token)) |
| `EIA_API_KEY` | U.S. grid feed ([free key](https://www.eia.gov/opendata/)) |
| `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` / `STRAVA_REFRESH_TOKEN` | Agent context — refresh token **must** carry the `activity:read_all` scope |
| `OPENAI_API_KEY` | Embeddings (`text-embedding-3-small`, 1536-dim) |
| `UPSTASH_VECTOR_REST_URL` / `UPSTASH_VECTOR_REST_TOKEN` | RAG upsert — must point at the **same index** the site's `chat.js` queries |
| `YASHHOODA_PAT` | Pushing gold JSON to the site repo |

---

## 🛠 Tech stack

Python · pandas · scikit-learn (linear regression) · stdlib-only pipelines where possible · GitHub Actions · Bronze–Silver–Gold architecture

## 🚀 Run locally

```bash
export NOAA_TOKEN=your_token
python scripts/climate_pipeline.py      # or any other pipeline

FORCE_FULL=1 python scripts/climate_pipeline.py   # force a full historical rebuild
```

`find_stations.py` discovers and TMAX-verifies GHCN station IDs **before** you add them — see *Adding a station* above. Never skip it: the climate pipeline aborts the whole run if a single station returns no data, so one bad ID stops every city from refreshing.

---

## 📐 Design principles

1. **Only data crosses repos.** Scripts and workflows stay here; the deployed site receives JSON and nothing else.
2. **Verify, don't guess.** Station IDs are checked against NOAA before they enter the config — a guessed ID doesn't fail quietly, it aborts every city.
3. **Automate the data, or automate the reminder.** Where a trustworthy machine-readable feed exists, build a pipeline. Where it doesn't (data-center figures), keep the numbers curated and schedule a nudge — a fake pipeline that silently injects wrong numbers is worse than an honest static dataset.
4. **A missing point beats a wrong one.** Partial years are dropped rather than averaged into a misleading value.
5. **One source of truth.** Numbers shown in the UI are derived from the gold data at render time, never hand-typed.
6. **Honest framing.** Correlation is labeled as correlation, projections as projections, contested figures as ranges.
7. **Fail soft.** A dead upstream degrades one dashboard, never the whole refresh.

## 📚 Data sources

NOAA GHCN-Daily · NOAA HURDAT2 · NOAA PSL · NOAA/STAR Laboratory for Satellite Altimetry · NASA GISTEMP · U.S. EIA · Strava API · GitHub API

*Altimetry data are provided by the NOAA Laboratory for Satellite Altimetry.*
