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
| `climate_pipeline.py` | NOAA GHCN-Daily (9 stations) | `public_data_climate_gold.json` | Daily |
| `hurricane_pipeline.py` | HURDAT2 · NOAA PSL SST · NASA GISTEMP | `public_data_hurricanes_gold.json` | Daily |
| `sealevel_pipeline.py` | NOAA/STAR satellite altimetry | `public_data_sealevel_gold.json` | Daily |
| `eia_grid_pipeline.py` | EIA API v2 (`ELETPUS`) | `public_data_us_grid_gold.json` | Daily |
| `agent_context_pipeline.py` | Strava · GitHub | `agent_context_gold.json` | Weekly |
| `agent_embed.py` | ↑ + OpenAI embeddings | Upstash Vector upsert | Weekly |
| `dc_staleness_check.py` | IEA · LBNL pages | GitHub issue (reminder only) | Quarterly |

### 🌡 Climate — the core pipeline

**Bronze → Silver → Gold** across **9 NOAA stations**, 1970–present:

| Code | Station | Code | Station | Code | Station |
|---|---|---|---|---|---|
| IAH | Houston, TX | DEN | Denver, CO | LAX | Los Angeles, CA |
| EWR | Newark, NJ | LHR | London, UK | HEL | Helsinki, Finland |
| DAL | Dallas, TX | DEL | Delhi, India | ORD | Chicago, IL |

- **Bronze** — raw NOAA daily station records, fetched year by year (`units=standard`, so NOAA returns °F for international stations too)
- **Silver** — cleaned + feature-engineered: `tmean`, `winter_year`, 80°F-day flags, plus a sanity guard dropping physically impossible values
- **Gold** — annual/seasonal aggregations + linear-regression trend metrics per station

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

From the current gold data (57 years, 1970–2026):

| Metric | Value |
|---|---|
| Houston annual warming | **+0.79 °F / decade** |
| Newark annual warming | **+0.39 °F / decade** |
| Houston winter nighttime warming | **+1.02 °F / decade** |
| Houston extra 80°F days (Feb–Mar) | **+2.07 days / decade** |

Two patterns hold across stations: **winter nighttime lows are warming faster than annual averages**, and Houston shows a pronounced shift in late-winter extreme-heat frequency. Per-decade figures for the newly added LA, Helsinki, and Chicago populate on the next full run.

---

## ⚙️ Automation (GitHub Actions)

| Workflow | Schedule | Does |
|---|---|---|
| `climate-refresh.yml` | Daily, 04:00 UTC | Climate + hurricane + sea level + EIA → push gold JSON to site repo |
| `agent-context.yml` | Mondays, 05:00 UTC | Strava + GitHub → push context JSON + embed into Upstash Vector |
| `datacenter-staleness.yml` | Quarterly | Opens a refresh-reminder issue if the curated figures are aging |

All pipeline steps are `continue-on-error` — one source having an outage never blocks the others — and every push is guarded by `git diff --staged --quiet`, so no empty commits.

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
```

`verify_stations.py` / `find_helsinki.py` are utilities for validating GHCN station IDs **before** adding them — the climate pipeline aborts the whole run if a station returns no data, so a bad ID would stop every city from refreshing.

---

## 📐 Design principles

1. **Only data crosses repos.** Scripts and workflows stay here; the deployed site receives JSON and nothing else.
2. **Automate the data, or automate the reminder.** Where a trustworthy machine-readable feed exists, build a pipeline. Where it doesn't (data-center figures), keep the numbers curated and schedule a nudge — a fake pipeline that silently injects wrong numbers is worse than an honest static dataset.
3. **Honest framing.** Correlation is labeled as correlation, projections as projections, contested figures as ranges.
4. **Fail soft.** A dead upstream degrades one dashboard, never the whole refresh.

## 📚 Data sources

NOAA GHCN-Daily · NOAA HURDAT2 · NOAA PSL · NOAA/STAR Laboratory for Satellite Altimetry · NASA GISTEMP · U.S. EIA · Strava API · GitHub API

*Altimetry data are provided by the NOAA Laboratory for Satellite Altimetry.*
