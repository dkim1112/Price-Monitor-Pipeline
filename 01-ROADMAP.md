# Project 01: Roadmap

## Phase 0: Data Source Exploration & Design (Week 1)

> Before writing any code, understand the data and make architecture decisions.
> This is the most important phase — the judgments made here shape the entire project.

### Tasks

- [x] Research candidate data sources and test actual access
  - ~~Public Data Portal price APIs (agricultural, livestock, seafood products, etc.)~~ → KAMIS requires company registration; pivoted to KOSTAT Online Price API
  - [x] Bank of Korea Economic Statistics System (ECOS) API — ✅ working (stat_code 901Y009)
  - [x] Statistics Korea (KOSIS) API — deprioritized (overlaps with ECOS for price data)
  - [x] (Optional) Assess feasibility of online marketplace price scraping — ruled out (legal risk, maintenance burden)
  - [x] KOSTAT Online Price Collection API (data.go.kr) — ✅ working (245 items, product-level data)
- [x] Document characteristics of each source
  - KOSTAT: weekly updates (~2 week lag), XML only, 245 categories, ~500-2000 products per category per query, date range max 30 days, data from 2015-01-01
  - ECOS: monthly cycle, JSON+XML, 1,743 items (hierarchical), base year 2020=100, CPI/PPI indices
  - Full comparison in JOURNAL.md
- [x] Draft a unified schema design → `01-SCHEMA-DESIGN.md`
  - raw layer: `raw.kostat_products` (partitioned by month), `raw.ecos_indices`, `raw.collection_log`
  - mart layer: `mart.daily_price_summary`, `mart.monthly_cpi_index`, `mart.price_vs_cpi` view
  - Item mapping: 124 items, 100% exact code match between KOSTAT and ECOS CPI
- [x] Make architecture decisions & write first DECISIONS.md entries
  - ADR-001: Data source selection (KOSTAT + ECOS, with KAMIS pivot)
  - ADR-002: Storage → PostgreSQL via Docker
  - ADR-003: Volume strategy → store all raw, aggregate later
  - ADR-004: Orchestration → Cron + Python (not Airflow)
- [x] Phase 0 exit criteria: able to actually fetch data from at least 2 sources ✅

### Key Differentiator Checks

- [x] ⭐ Documented the differences between sources (interview material)
- [x] ⭐ Recorded rationale for tech choices in DECISIONS.md (4 ADRs with full rationale)

---

## Phase 1: Core Pipeline Build (Weeks 2–3)

> Get the simplest possible version of "ingest → normalize → store" working.

### Tasks

- [x] Set up PostgreSQL via Docker Compose (docker-compose.yml + db/init.sql)
  - Partitioned `raw.kostat_products` by month, UNIQUE dedup index on `raw.ecos_indices`
  - Auto-init with DDL + item mapping seed data
- [x] Develop data collection modules (one per source)
  - KOSTAT: XML parsing, adaptive date probing (30-day range windows), pagination, batch insert
  - ECOS: JSON parsing, error code handling (especially 602 rate limit), ON CONFLICT dedup
- [x] Build aggregation logic (raw → mart)
  - SQL-pushed aggregation: median/mean/percentiles for daily prices, UPSERT for idempotency
  - `mart.price_vs_cpi` view joining product prices to CPI indices
- [x] Create CLI entrypoint (main.py) with subcommands
  - `collect-kostat`, `collect-ecos`, `aggregate`, `run-all`, `status`
- [x] Write basic unit tests (12 unit + 6 DB integration tests)
- [x] Configure cron scheduling
  - `scripts/cron_collect.sh` wrapper with .env loading, virtualenv activation, log rotation
  - KOSTAT: Mondays 9:00 AM, ECOS: 5th of month 9:00 AM, Aggregation: Mondays 9:30 AM
- [ ] Phase 1 exit criteria: data automatically accumulates on schedule

### Key Differentiator Checks

- [x] ⭐ Recorded judgments made during normalization — KOSTAT XML result codes can be None on success (not always "00"), item codes are 6 chars not 7, data lag requires adaptive probing
- [x] ⭐ Documented which AI-generated code was modified and why — three rounds of KOSTAT collector fixes documented in JOURNAL.md

---

## Phase 2: Data Quality & Monitoring (Week 4)

> Handle not just when the pipeline "works fine" but when "things go wrong."
> This phase is what separates this portfolio from most intern projects.

### Tasks

- [x] Define and implement data quality validation rules (`pipeline/quality.py`)
  - [x] Data freshness: KOSTAT > 21 days, ECOS > 45 days → WARN
  - [x] Completeness: < 90% of 124 expected items → WARN
  - [x] Null ratio: > 10% NULL sale_price → WARN
  - [x] Price anomalies: IQR-based detection (change > 2× IQR) → WARN + log to mart.price_anomalies
  - [x] ECOS value range: CPI values outside [30, 250] → WARN
- [x] Implement schema drift detection (`pipeline/schema_check.py`)
  - [x] KOSTAT: item catalog comparison (new/removed/renamed items)
  - [x] ECOS: field presence verification (all 14 expected fields)
  - [x] Baselines stored in raw.schema_baseline with drift history
- [x] Implement failure scenario handling
  - [x] Retry + backoff already in Phase 1 collectors (3 attempts, [5,15,45]s)
  - [x] ECOS 602 rate limit gets doubled backoff
  - [x] All check results logged to raw.quality_check_log for historical tracking
- [x] Alerting system (`pipeline/alerts.py`)
  - [x] Slack webhook with severity levels (INFO/WARNING/CRITICAL)
  - [x] Graceful degradation: logs only when no webhook configured
  - [x] Formatted messages with structured fields
- [x] DB migration: mart.price_anomalies, raw.schema_baseline, raw.quality_check_log
- [x] CLI: `python main.py validate` and `python main.py schema-check`
- [x] Updated run-all flow: schema check → collect → validate → aggregate → alert
- [ ] Phase 2 exit criteria: intentionally trigger failures and confirm alerts fire

### Key Differentiator Checks

- [x] ⭐ Tested quality checks on real data — 2 warnings caught (KOSTAT freshness, ECOS range)
- [x] ⭐ Recorded "why this threshold value" for every quality rule in ADR-007

---

## Phase 3: Visualization & Analysis Layer (Week 5)

> Make the pipeline output human-readable.

### Tasks

- [x] Build Streamlit dashboard (`src/dashboard.py`) with 4 pages
  - [x] Price Trends: daily time series with IQR band (p25–p75), median/mean lines, summary metrics
  - [x] Price vs CPI: dual-axis chart comparing product prices to official CPI indices, with % change insight callout
  - [x] Data Quality Health: status cards (PASS/WARN/FAIL), historical metrics over time, active anomalies table
  - [x] Pipeline Ops: data freshness indicators, collection timeline (success/failure scatter), records-per-run bar chart
- [x] CLI integration: `python main.py dashboard` launches Streamlit
- [x] Dependencies: streamlit, pandas, plotly added to requirements.txt

### Key Differentiator Checks

- [x] ⭐ Dashboard communicates "So what?" — Price vs CPI page shows "Product prices rose X% while CPI rose Y%"

---

## Phase 4: Polish & Packaging (Week 6)

> Complete the project as a portfolio piece. This matters as much as the code.

### Tasks

- [x] Write README.md (Problem → Approach → Decisions → Learnings → Limitations)
- [x] Docker Compose for full environment reproducibility (+ Dockerfile for dashboard)
- [x] Cost analysis document (`docs/cost-analysis.md` — AWS ~$35-50/month, GCP comparison, scaling triggers)
- [ ] Write blog posts (1–2) — deferred for later
  - Candidate: "What to consider when integrating data sources with different update cycles"
  - Candidate: "How I used AI in building a data pipeline — and where it fell short"
- [x] Finalize JOURNAL.md (7 entries + retrospective with "what went well / what I'd change / what I learned")
- [x] Full retrospective (final stats: ~1,800 lines, 8 ADRs, 624K records, 4-page dashboard)

### Key Differentiator Checks

- [x] ⭐ README alone conveys the depth of the project (Problem → Approach → Architecture → Learnings → Limitations)
- [x] ⭐ DECISIONS.md contains at least 5 architecture decisions with rationale (8 ADRs)
- [x] ⭐ JOURNAL.md honestly records failures and pivots (KAMIS pivot, KOSTAT debugging, CPI false positives)
- [x] ⭐ Cost analysis is included (AWS + GCP estimates with optimization options)

---
