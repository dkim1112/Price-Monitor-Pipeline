# Project Progress Journal

> This journal records the honest process, not the perfect process.
> Failures, dead ends, and pivots are the most valuable entries here.
> Documents some of the hard moments I faced while working on this project.

## Writing Guide

- Each entry should include:
  - What I did today
  - What I got stuck on / What was different from expectations
  - How I used AI (what I delegated vs. what I decided myself)
  - What to do next

---

(Record entries below as the project progresses)

## Data Source Research

### What I did today

- Researched the three main Korean public data sources for price information:
  - **KAMIS** (kamis.or.kr): Daily wholesale/retail prices for ~218 agricultural, livestock, and seafood products. API provides endpoints like `dailySalesList` and `recentlyPriceTrendList`. Requires `cert_key` + `cert_id`.
  - **ECOS** (ecos.bok.or.kr): Bank of Korea economic statistics — CPI, PPI, exchange rates. Monthly cycle. Uses `StatisticSearch` with stat codes. Has good Python wrapper (`PublicDataReader`).
  - **KOSIS** (kosis.kr): Broadest portal (500+ subjects from 120+ agencies) but for price data specifically, overlaps heavily with ECOS.
- Decided on **KAMIS + ECOS** as the two primary sources (documented in ADR-001).
- Wrote test scripts for both APIs (`scripts/test_kamis_api.py`, `scripts/test_ecos_api.py`).

### What was different from expectations

- KAMIS turned out to be the better fit vs data.go.kr's generic agricultural price API — KAMIS is the upstream source with richer endpoints.
- KOSIS has less straightforward documentation than ECOS; glad we deprioritized it.
- Haven't tested actual API responses yet (need to sign up for keys first).

### How I used AI

- Delegated: API documentation research, discovering endpoint parameters and Python libraries, writing boilerplate test scripts.
- Decided myself: Which two sources to use and why (ADR-001), the rationale for excluding KOSIS and scraping, how to frame the project narrative.

### What to do next

- [x] Sign up for KAMIS and ECOS API keys
- [ ] Run both test scripts and document actual response schemas
- [ ] Identify specific product categories / stat codes to track
- [ ] Start drafting the unified schema design

---

## First Pivot — KAMIS to KOSTAT

### What I did today

- **KAMIS blocker discovered**: KAMIS API keys require company registration (official business with a website). Individual developers can't get access. This killed our original Plan A.
- **ECOS key obtained** (key works, but couldn't test from sandbox environment — need to run locally).
- **Pivoted to KOSTAT Online Price API** (통계청\_온라인 수집 가격 정보, data.go.kr ID: 15080757):
  - Auto-approved for individuals, 10,000 requests/day
  - Two clean endpoints: `getPriceItemList` (product catalog) + `getPriceInfo` (price data by item + month)
  - These are online-collected prices - i.e., the government scrapes e-commerce sites. Interesting angle.
- Documented the pivot in ADR-001.
- Wrote test script: `scripts/test_kostat_price_api.py`

### What was different from expectations

- **Big surprise**: KAMIS looked perfect on paper but has an access barrier nobody mentions in tutorials. Most blog posts about KAMIS assume you're working at a company. This is exactly the kind of real-world constraint that's worth documenting.
- The pivot actually improves the project narrative — "online collected prices" vs "official indices" is a more interesting contrast than "wholesale market prices" vs "indices."

### How I used AI

- Delegated: Searching for KAMIS alternatives on data.go.kr, finding the KOSTAT API parameters, writing the test script boilerplate.
- Decided myself: Which alternative to pick (KOSTAT over Consumer Agency or MAFRA), how to frame the pivot as a positive in the project story.

### What to do next

- [x] Sign up at data.go.kr and apply for KOSTAT Online Price API
- [x] Run `test_ecos_api.py` locally with the ECOS key
- [x] Run `test_kostat_price_api.py` locally with the data.go.kr key
- [ ] Document actual response schemas from both APIs
- [ ] Start drafting unified schema design

---

## Both APIs Confirmed Working

### What I did today

- **ECOS API confirmed working**: stat_code `901Y009` returns CPI data (소비자물가지수). 1,743 items in the hierarchy, from 총지수 down to individual products (쌀, 라면, 두부...). Monthly cycle, base year 2020=100. Schema: 14 fields including STAT_CODE, ITEM_CODE1-4, ITEM_NAME1-4, TIME, DATA_VALUE, UNIT_NAME, WGT.
- **KOSTAT API debugging journey**:
  - First attempt: 401 Unauthorized (key encoding issue — data.go.kr decoding key needed)
  - Second attempt: got item list but wrong field names (API uses abbreviated XML tags: `rn`, `ic`, `in`, `ed`)
  - Third attempt: getPriceInfo returned error 22 (wrong parameters — I used `yyyymm` but the API needs `startDate`/`endDate` in YYYYMMDD format)
  - Found the official API guide v2.2 which had the exact spec
  - Fourth attempt: error 21 "no data" for recent dates
  - Diagnostic script revealed: data has ~1-2 week lag (D-14 works, D-7 doesn't), and `ed` field means schema version date (item codes changed on 2024-12-19)
- **Final confirmed KOSTAT schema**: 245 active product categories. Each returns hundreds to thousands of individual e-commerce product listings. Per record: `pi` (product ID), `pn` (product name), `sp` (sale price), `dp` (discount price), `bp` (benefit price), `sd` (price date). Example: 라면 returns 2,116 product listings per query date.

### What was different from expectations

- **KOSTAT is way richer than expected**: This isn't aggregated category prices — it's individual product listings scraped from e-commerce sites. 라면 alone has 2,116+ products. This creates a data volume challenge we need to plan for.
- **API documentation was misleading**: The API guide sample used item code `A011010` (7 chars) but actual codes are `A01101` (6 chars). The guide also didn't mention the `ed` field at all. Had to discover the schema empirically.
- **Data lag is significant**: Weekly updates with ~2 week lag means this isn't "real-time" despite the project name. Need to adjust expectations and possibly the project framing.
- **No JSON support**: KOSTAT is XML-only. ECOS supports both. Had to be extra careful about pipeline design.

### How I used AI

- Delegated: Writing test scripts, parsing the API guide document, generating debugging scripts to test parameter variations systematically.
- Decided myself: Interpreting the `ed` field meaning, identifying the data lag issue, recognizing the volume implications for pipeline design.

### What to do next

- [x] Design unified schema (big challenge: KOSTAT has product-level data, ECOS has index data)
- [x] Decide how to handle KOSTAT's massive per-item volume (aggregate? sample? store all?)
- [x] Make architecture decisions: storage, orchestration, data quality approach
- [x] Record ADR-002 for schema design decisions

---

## Phase 0 Complete

### What I did today

- **Item mapping**: Built automated script to map KOSTAT ↔ ECOS item codes. Result: **124 active KOSTAT items, 100% exact code match** with ECOS CPI at HIGH confidence. Both sources use the same CPI classification system — no manual curation needed. Mapping saved to `item_mapping.csv` and `item_mapping_insert.sql`.
- **Unified schema designed** (`01-SCHEMA-DESIGN.md`):
  - Raw layer: `raw.kostat_products` (partitioned monthly, ~500K rows/week), `raw.ecos_indices`, `raw.collection_log`
  - Mart layer: `mart.daily_price_summary` (aggregated with median/mean/percentiles), `mart.monthly_cpi_index`, `mart.price_vs_cpi` view
  - Estimated storage: ~200 MB/month, < 3 GB/year
- **Architecture decisions completed** (4 ADRs):
  - ADR-001: KOSTAT + ECOS (pivoted from KAMIS due to access restriction)
  - ADR-002: PostgreSQL via Docker (production-grade portfolio story)
  - ADR-003: Store all raw data, aggregate later (raw → mart pattern)
  - ADR-004: Cron + Python over Airflow (right tool for the workload)

### What was different from expectations

- **Item mapping was trivial**: Expected manual curation work, but KOSTAT and ECOS share the exact same CPI classification codes. 100% match. This is actually worth mentioning in interviews — "I designed for complexity but the data turned out to be cleaner than expected. I documented this as a pleasant surprise rather than pretending it was hard."

### How I used AI

- Delegated: Generating the mapping script, writing SQL DDL for the schema, drafting ADR boilerplate, researching API documentation.
- Decided myself: The two-layer schema design (raw vs mart), choosing PostgreSQL over DuckDB with clear rationale, choosing Cron over Airflow, the data volume strategy (store all vs aggregate on ingest), how to frame each decision for interview storytelling.

### Phase 0 Exit Criteria — All Met ✅

1. ✅ Can fetch data from KOSTAT API (245 product categories, product-level prices)
2. ✅ Can fetch data from ECOS API (CPI indices, 1,743 hierarchical items)
3. ✅ Unified schema designed with clear raw → mart flow
4. ✅ 4 architecture decisions documented with full rationale
5. ✅ Item mapping complete (124 items, 100% coverage)

### What to do next (Phase 1)

- [ ] Set up PostgreSQL in Docker (docker-compose.yml)
- [ ] Create database schema (run DDL from 01-SCHEMA-DESIGN.md)
- [ ] Build KOSTAT collection module (XML parsing → raw.kostat_products)
- [ ] Build ECOS collection module (JSON parsing → raw.ecos_indices)
- [ ] Build aggregation logic (raw → mart.daily_price_summary)
- [ ] Set up cron scheduling
- [ ] Write basic unit tests

---

## Pre-Phase 1 API Coverage Verification

### What I did today

- **Cross-referenced official ECOS 개발명세서** (7 XLS documents from ecos.bok.or.kr) against our schema and test scripts. These cover all 6 ECOS endpoints: StatisticSearch, StatisticItemList, StatisticTableList, KeyStatisticList, StatisticWord, StatisticMeta.
- **Ran `verify_api_coverage.py`** — comprehensive live verification script. 18/19 checks passed.
- **Discovered 64 price-related ECOS tables** via StatisticTableList browse. Found PPI stat codes (`404Y014`–`404Y017`) for future phases.
- **Fixed 3 schema issues**:
  1. `weight` column: VARCHAR(20) → DECIMAL(10,2) — verified WGT returns numeric values like `1000`
  2. `api_call_id`: added NOT NULL + FK constraint — enforces data lineage
  3. Added ECOS error code reference table (especially code 602: rate limit exceeded)
- Documented in ADR-005.

### What was different from expectations

- **StatisticTableList actually works with stat_code** — our Phase 0 keyword search (`소비자물가`) failed, but searching by stat_code or browsing all 823 tables works fine. The keyword search just doesn't support Korean terms well. This means discovery is possible, just not via keyword.
- **WGT field is numeric** — live API returned `1000` for 총지수. Storing it as VARCHAR would have broken any aggregation or weight-based analysis in the mart layer.
- **KOSTAT data lag is still ~2 weeks** — D-14 and D-21 both returned no data on Feb 19, meaning the latest available data was from early February at best. Our collection module needs adaptive date logic, not a fixed offset.

### How I used AI

- Delegated: Reading and extracting all 7 ECOS 개발명세서 XLS files, writing the verification script, cross-referencing field lists between spec and schema.
- Decided myself: Which schema changes matter (weight type, FK constraint), that the KOSTAT failure is operational not structural, that PPI tables are "document for later" not "add now."

### Phase 0.5 Exit Criteria — All Met ✅

1. ✅ All ECOS StatisticSearch response fields mapped to `raw.ecos_indices` (14/14)
2. ✅ All KOSTAT getPriceInfo response fields mapped to `raw.kostat_products` (6/6)
3. ✅ Schema corrected (weight type, FK constraints)
4. ✅ ECOS error codes documented for collection module
5. ✅ Price-related stat codes cataloged for future expansion

---

## Phase 1 — Core Pipeline Build

### What I did today

- **Built the entire pipeline in one pass**: Docker Compose, DDL, both collectors, aggregation, CLI, and tests.
- **Infrastructure**: `docker-compose.yml` with PostgreSQL 16, auto-init from `db/init.sql` (full DDL with monthly partitions for 2025-2026, UNIQUE dedup index on ECOS, FK constraints on api_call_id).
- **KOSTAT collector** (`collect_kostat.py`): XML parsing, adaptive date probing with 30-day range windows, pagination, batch insert via `psycopg2.extras.execute_batch`. Periodic commits every 10 items.
- **ECOS collector** (`collect_ecos.py`): JSON parsing, ECOS-specific error handling, `ON CONFLICT DO NOTHING` for dedup, weight and data_value parsing with None handling.
- **Aggregation** (`aggregate.py`): SQL-pushed to PostgreSQL — `PERCENTILE_CONT` for median/p25/p75, UPSERT with `ON CONFLICT DO UPDATE` for idempotency.
- **CLI** (`main.py`): 5 subcommands — `collect-kostat`, `collect-ecos`, `aggregate`, `run-all`, `status`.
- **Tests** (`test_pipeline.py`): 12 unit tests (XML parsing, URL building, safe_int, WGT/DATA_VALUE conversion) + 6 DB integration tests. All 12 unit tests pass.

### What was different from expectations

- **Three rounds of KOSTAT debugging required**:
  1. **Single-date probes missed data**: KOSTAT data is sparse (weekly, ~2 week lag). Single-date probes at D-14, D-21 all returned nothing. Fix: switched to 30-day range window probes.
  2. **Item code length mismatch**: The API guide sample used `A011010` (7 chars) but `getPriceItemList` returns `A01101` (6 chars). Diagnosed with `debug_date_probe.py` — the 6-char code returned 28,286 records while the 7-char code returned zero. Fix: changed test_item to `A01101`.
  3. **XML result code is None on success**: Parser required `result["code"] == "00"` but successful KOSTAT responses have no `resultCode` element at all (only error responses include it). Fix: changed to check for presence of items rather than requiring code "00".
- **The `-v` flag didn't work with subcommands**: argparse's `-v` on the parent parser isn't inherited by subcommands. Fix: created a shared parent parser with `parents=[common]`.
- **Docker wasn't installed**: Had to guide through Docker Desktop installation first.

### How I used AI

- Delegated: Generating boilerplate for all modules (config, db connection, CLI argument parsing), writing the test suite, creating the Docker Compose and init.sql DDL, writing the debug_date_probe.py diagnostic script.
- Decided myself: The "build everything together" approach, the adaptive date probing strategy (30-day windows instead of single dates), diagnosing the item code format mismatch from debug output, recognizing the result code None-vs-"00" pattern, the periodic commit strategy (every 10 items).

### What to do next

- [x] Verify KOSTAT collection works end-to-end — 624,025 records, 124/124 items, 0 errors
- [x] Run ECOS collection — 8,134 rows for Jan 2025–Feb 2026
- [x] Run aggregation — 124 daily summaries, 15,106 monthly CPI rows
- [x] Set up cron scheduling — wrapper script + crontab entries
- [x] Phase 1 complete → move to Phase 2

---

## Phase 2 — Data Quality & Monitoring

### What I did today

- **Built three new modules** for the quality/monitoring layer:
  - `pipeline/quality.py`: 6 validation checks — data freshness, item completeness, null ratio, price anomaly detection (IQR method), ECOS CPI range, and historical logging to `raw.quality_check_log`
  - `pipeline/alerts.py`: Slack webhook alerting with severity levels (INFO/WARNING/CRITICAL), graceful degradation to log-only when no webhook configured
  - `pipeline/schema_check.py`: Schema drift detection — compares live KOSTAT item catalog and ECOS field set against stored baselines in `raw.schema_baseline`
- **DB migration** (`migration_002_quality.sql`): Added `mart.price_anomalies`, `raw.schema_baseline`, `raw.quality_check_log`
- **Updated run-all** to 5-step flow: schema check → collect KOSTAT → collect ECOS → validate → aggregate
- **New CLI commands**: `python main.py validate` and `python main.py schema-check`

### Verification Results

**Schema drift detection** (`python main.py schema-check -v`):

```
[OK] KOSTAT/item_catalog: Baseline established: 245 items
[OK] ECOS/field_presence: Baseline established: 14 fields
```

Both baselines established on first run. No drift detected — KOSTAT returned all 245 items in the catalog, ECOS returned all 14 expected StatisticSearch fields.

**Data quality validation** (`python main.py validate -v`):

```
Check                     Status        Value  Threshold
------------------------------------------------------------
kostat_freshness          WARN             30         21
ecos_freshness            PASS             23         45
kostat_completeness       PASS          100.0         90
kostat_null_ratio         PASS            0.0         10
ecos_value_range          WARN            143          0
price_anomalies           PASS              0          -

Summary: 0 failures, 2 warnings, 4 passed
```

Two(expected) warnings caught:

1. **KOSTAT freshness (30 days)**: We only collected one date (Feb 8), so it's 30 days old. The threshold (21 days) correctly flags this as stale.
2. **ECOS value range (143 outliers)**: Initially set range to [80, 130] based on "CPI ~ 100" assumption. But real CPI sub-indices range from 47.24 (communications) to 211.52 (housing). All 143 flagged values were false positives → widened range to [30, 250].

### What was different from expectations

- **ECOS CPI range was initially too tight**: Set [80, 130] based on textbook "CPI ≈ 100". But real sub-indices range from 47 (communications) to 211 (housing). First validate run flagged 143/581 values as outliers — all false positives. Widened to [30, 250]. This is a good lesson: thresholds must be calibrated against actual data, not theory.
- **API keys not loaded for schema check**: The schema check module makes direct HTTP calls (not through the collectors), so it failed when env vars weren't exported in the shell session. Fixed by sourcing `.env` before running.
- **Anomaly detection needs ≥2 collection dates**: With only one KOSTAT date collected (Feb 8), there's no baseline to compare against. This is expected — the check will activate once the pipeline has been running for a while.

### How I used AI

- Delegated: Generating the quality module structure, writing IQR anomaly detection SQL, creating the Slack webhook integration, formatting the CLI output table, writing the test cases with mock cursors.
- Decided myself: Every threshold value and its rationale (documented in ADR-007), widening the CPI range after seeing false positives, the decision to log but not block on schema drift, choosing IQR over z-score for anomaly detection (IQR is more robust to non-normal price distributions).

### What to do next

- [ ] Run unit tests to verify Phase 2 code
- [ ] Set up a Slack webhook for live alerting (optional)
- [ ] Intentionally trigger failures to confirm alerts work (Phase 2 exit criterion)
- [x] Start Phase 3: visualization/dashboard

---

## Phase 3 — Visualization & Analysis

### What I did today

- **Built Streamlit dashboard** (`src/dashboard.py`) — single-file app with 4 pages:
  1. **Price Trends**: Item selector dropdown (124 categories), time series with IQR band (p25–p75 shading), median + mean lines, summary metrics (product count, median price, price range, discount rate), expandable raw data table
  2. **Price vs CPI**: Dual-axis chart — median product price (left axis, KRW) vs CPI index (right axis, 2020=100). Auto-calculates % change comparison: "Product prices changed +X% while CPI changed +Y%"
  3. **Data Quality Health**: Color-coded status cards for latest check results (PASS/WARN/FAIL), historical metric trends via line chart, active anomalies table from `mart.price_anomalies`
  4. **Pipeline Ops**: Data freshness indicators (days since last collection), collection timeline scatter plot (color = success/failure, size = records), records-per-run bar chart, full collection log table
- **Tech stack**: Streamlit + Plotly (interactive charts) + Pandas (data handling), reusing `pipeline.config.get_db_params()` for DB connection
- **CLI integration**: `python main.py dashboard` launches the Streamlit server
- **Dependencies**: Added streamlit, pandas, plotly to `requirements.txt`

### What was different from expectations

- **Single file works well for Streamlit**: Initially considered splitting pages into separate files, but Streamlit's `st.sidebar.radio()` page routing pattern keeps everything clean in one file. Each page is a function, easy to navigate.
- **DB connection caching**: Used `@st.cache_resource` for the psycopg2 connection (shared across reruns) with automatic reconnection on stale connections.

### How I used AI

- Delegated: Generating the Plotly chart configurations (dual-axis, IQR band fill, scatter with size encoding), Streamlit layout patterns (columns, expanders, metrics), SQL queries for each dashboard page.
- Decided myself: The 4-page structure and what each page should show, making the "Price vs CPI" page the centerpiece (the "so what?" view), choosing Plotly over matplotlib/altair for interactivity, the insight callout design (% change comparison).

---

## Phase 4 — Polish & Packaging

### What I did today

- **Cost analysis** (`docs/cost-analysis.md`): Estimated ~$32–50/month on AWS (t3.small + db.t3.micro RDS). Compared AWS vs GCP. Documented cost optimization options (Lambda, Aurora Serverless, Free Tier). Included scaling triggers.
- **Full-stack Docker Compose**: Added optional dashboard service (behind a `--profile dashboard` flag) and a Dockerfile. Also mounted `migration_002_quality.sql` into the init sequence so new databases get quality tables automatically.

### How I used AI

- Delegated: Generating the cost analysis estimates, Dockerfile boilerplate, structuring the README sections.

---

## Retrospective

### What went well

- **The KAMIS → KOSTAT pivot** turned out to be an improvement. KOSTAT's product-level data (individual e-commerce listings) is more interesting than KAMIS's aggregated prices would have been. The pivot is itself good interview material.
- **Data quality as Phase 2, not Phase 4**: Building quality checks early (freshness, completeness, anomaly detection) caught real issues — the CPI range miscalibration (143 false positives) would have been embarrassing to discover during a demo.
- **The "raw → mart" pattern** paid off. When aggregation needed tweaking, we just re-ran the SQL. No data was lost.
- **8 ADRs with real rationale**: Every decision has a "why this, not that" — not just "I used PostgreSQL."

### What I'd do differently

- **Collect more historical data upfront**: We only collected one KOSTAT date initially. Anomaly detection needs ≥2 dates to work, and the dashboard is more interesting with more data points.
- **Test the dashboard against real data earlier**: Built it at the end but should have prototyped a single chart earlier to validate the mart table schema was actually dashboard-friendly.
- **Airflow was the right call to skip**, but I'd add a simple `run-all` scheduler that also handles retries and backoff, rather than relying on cron alone.

### What I learned

- Government APIs have undocumented behaviors. Always verify against real data, not documentation.
- Quality thresholds must be calibrated against actual distributions, not textbook assumptions.
- The "boring" decisions (Cron over Airflow, PostgreSQL over DuckDB) are the most defensible in interviews when backed by clear rationale.

### Final stats

- **Data**: 624K KOSTAT product records, 8K ECOS CPI rows, 124 daily summaries, 15K monthly CPI entries
- **Quality**: 6 automated checks, schema drift detection for 2 sources
- **Documentation**: 8 ADRs, 7 journal entries, cost analysis, schema design doc
- **Infrastructure**: PostgreSQL (Docker), cron scheduling, Streamlit dashboard (4 pages)
