# Architecture Decision Records (ADR)

> Every technical choice must have a "why."
> This document is a core asset for answering "Why did you choose this technology?" in interviews.

## Template

```
### ADR-XXX: [Decision Title]
- **Status**: Proposed | Accepted | Deprecated | Superseded
- **Context**: What situation required this decision?
- **Alternatives Considered**:
  - Option A: [Pros] / [Cons]
  - Option B: [Pros] / [Cons]
- **Decision**: What was chosen?
- **Rationale**: Why was this chosen? (include trade-offs)
- **Consequences**: What impact did this decision have? (add later)
```

---

(Record entries below as the project progresses)

### ADR-001: Primary Data Source Selection
- **Status**: Accepted
- **Context**: The pipeline needs to integrate price data from multiple Korean government sources to compare product-level prices against macro indices. Initial candidate was KAMIS (Korea Agricultural Marketing Information Service) + ECOS, but KAMIS API keys require company registration — individual developers cannot get access. Needed an alternative source for product-level price data that preserves the "micro vs macro" contrast with ECOS.
- **Alternatives Considered**:
  - Option A — 통계청 온라인 수집 가격 정보 (KOSTAT Online Price Collection, data.go.kr ID: 15080757):
    - Pros: Auto-approved for individuals, 10,000 requests/day, product-level online prices collected by Statistics Korea, clean two-endpoint design (item list + price query)
    - Cons: Less well-documented than KAMIS, may have smaller product coverage
  - Option B — 한국소비자원 생필품 가격 정보 (Korea Consumer Agency Daily Necessities):
    - Pros: Very consumer-facing (daily necessities), relatable items
    - Cons: Narrower scope (daily necessities only), less clear documentation
  - Option C — MAFRA portal (data.mafra.go.kr):
    - Pros: Auto key on signup, agricultural focus, includes Garak Market codes
    - Cons: Separate portal/account, narrower to agriculture only
  - Option D — KAMIS + online marketplace scraping:
    - Pros: "Real consumer prices" angle is compelling
    - Cons: Scraping is legally gray, fragile to site changes, hard to maintain — bad fit for a portfolio project
- **Decision**: KOSTAT Online Price Collection API + Bank of Korea ECOS. Two-source architecture: KOSTAT (product-level e-commerce prices) + ECOS (CPI/PPI index values).
- **Rationale**: KOSTAT preserves the original design intent — actual product prices vs macro indices. It has the best developer experience (auto-approval, highest rate limit) and being from Statistics Korea itself adds credibility. The "online collected" angle is interesting: these are prices scraped from e-commerce by the government, creating a natural talking point about data collection methodology. The two-source approach gives the richest contrast in data characteristics (weekly vs monthly, actual prices vs index numbers, product-level vs macro-level) while keeping scope manageable. KOSIS was deprioritized because its price data overlaps significantly with ECOS.
- **Consequences**: The pivot from KAMIS to KOSTAT is itself good interview material — demonstrates adaptability when initial plans hit real-world constraints. KOSTAT API has quirks (XML-only, ~2 week data lag, abbreviated field names) that required iterative debugging to resolve.

### ADR-002: Storage Engine Selection
- **Status**: Accepted
- **Context**: The pipeline needs to store two layers of data: (1) raw product-level KOSTAT data (~500K+ rows/week across 245 categories) and raw ECOS index data, and (2) aggregated daily/weekly summaries. The project runs on a single developer machine but should demonstrate production-level design patterns. Need a storage engine that supports proper schema design, indexing, and is recognizable to interviewers.
- **Alternatives Considered**:
  - Option A — PostgreSQL (via Docker):
    - Pros: Industry standard for DE pipelines. Supports concurrent reads/writes, proper constraints, indexes, partitioning. Docker-composable for reproducibility. Streamlit/Grafana connect natively (Phase 3). Interviewers will recognize and respect it.
    - Cons: Requires managing a database server (Docker). Overhead for a solo project. Arguably overkill for the actual data volume.
  - Option B — DuckDB:
    - Pros: Zero server, columnar storage, blazing fast analytics. Very modern (growing in data stack). Native Python and Parquet support. Perfect fit for analytical workloads.
    - Cons: Not designed for concurrent writes. Less familiar to traditional DE interviewers. Can't demonstrate "connect to a database" patterns.
  - Option C — SQLite:
    - Pros: Zero config, ships with Python, simplest option.
    - Cons: Limited analytics, poor concurrency, may look like a toy in a portfolio.
- **Decision**: PostgreSQL via Docker.
- **Rationale**: The primary goal is a portfolio piece that demonstrates production-level thinking. PostgreSQL lets us showcase proper schema design (normalized tables, constraints, indexes), which is directly transferable to real DE work. The Docker overhead is actually a feature — it shows we can set up reproducible infrastructure. The data volume (~500K rows/week) is modest for PostgreSQL but enough to make indexing and partitioning decisions meaningful. DuckDB was a close second and would be the right choice for a pure analytics project, but this pipeline emphasizes data engineering patterns over analytical performance.
- **Consequences**: Need Docker Compose setup. Schema design becomes a key deliverable. Partitioning strategy needed for KOSTAT raw data (likely by collection date).

### ADR-003: Data Volume Strategy — Store All, Aggregate Later
- **Status**: Accepted
- **Context**: KOSTAT returns thousands of individual e-commerce product listings per item category per query date (e.g., 라면 → 2,116 products). We need to decide whether to store all raw data, aggregate on ingest, or sample.
- **Alternatives Considered**:
  - Option A — Store all raw, aggregate in a separate layer:
    - Pros: No data loss. Enables retroactive analysis. Demonstrates "raw vs curated" data lake pattern. Aggregation logic is separately testable and documentable.
    - Cons: Higher storage (~500K+ rows/week). More complex schema. Need partition/cleanup strategy.
  - Option B — Aggregate on ingest:
    - Pros: Simple. Small storage footprint. Less pipeline complexity.
    - Cons: Loses raw data forever. Can't recompute if aggregation logic changes. Less interesting as a portfolio piece.
  - Option C — Sample representative products:
    - Pros: Lower volume. Trackable products over time.
    - Cons: Introduces selection bias. Hard to justify which products are "representative." Less defensible methodology.
- **Decision**: Store all raw data, compute aggregates in a separate materialized view / summary table.
- **Rationale**: The "raw → staged → aggregated" pattern is a core data engineering concept and demonstrates understanding of data lake/warehouse architecture. The storage cost is trivial on a local PostgreSQL instance. Keeping raw data means we can recompute aggregates if we later decide median is better than mean, or want to add percentile analysis. This approach also creates more opportunities to demonstrate data quality checks (outlier detection, price anomaly flagging) on the raw layer before aggregation.
- **Consequences**: Need a two-layer schema: `raw` (source data as-is) and `mart` (aggregated summaries). Need a cleanup/retention policy for raw data. Aggregation logic (median? mean? percentiles?) becomes its own design decision.

### ADR-004: Orchestration — Cron + Python over Airflow
- **Status**: Accepted
- **Context**: The pipeline needs a scheduler to run collection jobs on a recurring basis (weekly for KOSTAT, monthly for ECOS). Need to choose between a full-featured orchestrator (Airflow, Prefect, Dagster) and a simpler approach (cron + Python scripts). This is a solo project running on a single machine.
- **Alternatives Considered**:
  - Option A — Apache Airflow:
    - Pros: Industry gold standard. DAGs, retry logic, monitoring UI, backfill support. Very impressive on a resume.
    - Cons: Heavy infrastructure (webserver, scheduler, metadata DB, executor). Docker Compose for Airflow alone is complex. Overkill for 2 data sources on a weekly/monthly cycle. Risk of spending more time on Airflow config than on actual pipeline logic.
  - Option B — Prefect / Dagster:
    - Pros: Modern, Pythonic, lighter than Airflow. Good developer experience. Cloud-hosted free tiers available.
    - Cons: Still adds a dependency and learning curve. Cloud-hosted means external dependency for a local portfolio project.
  - Option C — Cron + Python with structured logging:
    - Pros: Zero infrastructure overhead. All time spent on pipeline logic, not orchestrator config. The `raw.collection_log` table already provides observability. Can add retry logic, error handling, and alerting directly in Python. Easy to understand and explain in interviews.
    - Cons: No visual DAG. No built-in backfill. Less impressive at first glance.
- **Decision**: Cron + Python scripts with structured logging (Option C). Wrap collection logic in a `main.py` entrypoint with proper error handling, retries, and logging to `raw.collection_log`.
- **Rationale**: The actual complexity of this pipeline is in data handling (XML parsing, schema normalization, quality checks, aggregation), not in orchestration. With only 2 sources on weekly/monthly cycles, a full orchestrator adds infrastructure burden without proportional value. The `raw.collection_log` table provides the observability that Airflow's UI would give us, and cron is universally understood. This decision also demonstrates judgment: knowing when NOT to use a heavy tool is as valuable as knowing how to use one. If an interviewer asks "why not Airflow?", the answer is clear: "The orchestration complexity didn't justify it — I'd use Airflow if I had 10+ sources with complex dependencies."
- **Consequences**: Need a clean Python entrypoint (`main.py`) with argument parsing for manual runs and backfills. Cron schedule: KOSTAT collection on Mondays (weekly data), ECOS collection on the 5th of each month (monthly data). Need alerting via Slack webhook or email for failures (Phase 2).

### ADR-005: Pre-Phase 1 API Coverage Verification
- **Status**: Accepted
- **Context**: Before building the pipeline (Phase 1), needed to verify both APIs return all the fields our schema expects. Cross-referenced the official ECOS 개발명세서 (7 documents covering all 6 endpoints) and KOSTAT API guide v2.2 against our `01-SCHEMA-DESIGN.md`.
- **Verification Results**:
  - **ECOS**: 6 endpoints total. We use **StatisticSearch** (primary data) and **StatisticItemList** (catalog/weights) for the pipeline, plus **StatisticTableList** for discovery. All 14 StatisticSearch response fields confirmed present and mapped to `raw.ecos_indices`. 64 price-related tables discovered, including PPI codes (`404Y014`–`404Y017`) for future expansion.
  - **KOSTAT**: 2 endpoints, both fully covered. `getPriceItemList` → 245 active items (402 total). `getPriceInfo` → 6 price fields all mapped to `raw.kostat_products`.
  - **18/19 checks passed**. One expected failure: KOSTAT `getPriceInfo` returned no data for D-14/D-21 (known ~2 week lag, varies by date).
- **Schema Fixes Applied**:
  1. `raw.ecos_indices.weight`: `VARCHAR(20)` → `DECIMAL(10, 2)` — WGT confirmed numeric (e.g., `1000` for 총지수)
  2. `api_call_id`: added `NOT NULL` + FK constraint to `raw.collection_log(id)` — enforces data lineage
  3. Added ECOS error code reference table to schema doc (especially code 602: rate limit)
  4. Documented that `StatisticTableList` keyword search is unreliable — use stat_code browsing instead
- **Decision**: Schema is verified and corrected. Both APIs provide everything we need. Ready for Phase 1.
- **Rationale**: Verifying API coverage before writing pipeline code prevents rework. The schema fixes (weight type, FK constraints) would have caused bugs or data quality issues if caught later. The ECOS error code documentation ensures the collection module handles all failure modes.
- **Consequences**: No blockers for Phase 1. The PPI stat codes (`404Y014`–`404Y017`) are documented for Phase 3/4 expansion but not in the initial pipeline scope.

### ADR-006: KOSTAT API Defensive Parsing
- **Status**: Accepted
- **Context**: During Phase 1 integration testing, the KOSTAT collector failed in three distinct ways that weren't documented in the official API guide. Each required a code fix and revealed an API behavior that contradicts the documentation.
- **Issues Discovered**:
  1. **Item codes are 6 characters, not 7**: The API guide v2.2 sample uses `A011010` (7 chars) but `getPriceItemList` returns `A01101` (6 chars). The 7-char code silently returns zero results.
  2. **Successful responses have no resultCode**: The guide implies all responses contain `resultCode: "00"` on success, but in practice the `resultCode` element is entirely absent from successful responses — only error responses include it.
  3. **Data lag is variable and sparse**: Weekly updates with ~2-4 week lag means single-date probes often miss. Even a specific date known to have data might not be the latest.
- **Decision**: Adopt defensive parsing — check for data presence (items array) rather than status codes, use 30-day range window probes for date discovery, and source item codes dynamically from `getPriceItemList` rather than hardcoding.
- **Rationale**: Government APIs often have undocumented behaviors. Checking for actual data rather than status codes is more resilient to API changes. The range-based probing accommodates the variable data lag without needing to know the exact update schedule.
- **Consequences**: The collector is more robust but also more tolerant — genuine errors that happen to include partial data might be missed. Added specific error code checks (code "21" = no data) as a middle ground.

### ADR-007: Data Quality Thresholds and Monitoring Strategy
- **Status**: Accepted
- **Context**: Phase 2 requires defining specific thresholds for data quality checks. Each threshold needs a "why this number?" rationale — not arbitrary values.
- **Thresholds and Rationale**:
  1. **KOSTAT freshness: 21 days** — KOSTAT publishes weekly with ~2 week lag. 21 days = missed 1 weekly cycle + buffer. If data is older than this, something is wrong with collection, not just normal lag.
  2. **ECOS freshness: 45 days** — ECOS CPI is monthly, published mid-month for the previous month. 45 days = missed 1 full monthly cycle + buffer. Normal lag is ~15-25 days.
  3. **Item coverage: 90%** — 124 active items. A few items having no data for a given date is normal (sparse updates). Below 90% (~112 items) suggests a systematic API issue rather than normal sparsity.
  4. **Null sale_price ratio: 10%** — Product listings should always have a sale price. A few NULLs are tolerable (data quality at source). Above 10% suggests the API response format changed or parsing broke.
  5. **ECOS CPI range: [30, 250]** — Base year 2020=100. Sub-indices can legitimately range widely (e.g., housing up to ~211, communications down to ~47 in our data). Initial range [80, 130] was too tight — caught 143/581 false positives. Widened to [30, 250] to catch only genuine anomalies (negative values, extreme outliers).
  6. **Price anomaly IQR multiplier: 2.0** — Standard IQR-based outlier detection uses 1.5× (mild) or 3× (extreme). We chose 2× as a middle ground: catches meaningful price shifts without flooding alerts. This can be tuned based on observed false positive rates.
- **Decision**: Use the thresholds above, stored in `config.py` for easy adjustment. All checks log to `raw.quality_check_log` for threshold tuning over time.
- **Rationale**: Every threshold is derived from actual data characteristics observed during Phase 0 and Phase 1, not textbook defaults. The quality check history table enables data-driven threshold refinement.
- **Consequences**: The initial CPI range needed immediate adjustment after first run (80-130 → 30-250). This is expected and healthy — the system caught the miscalibration quickly. Future threshold tuning can use `raw.quality_check_log` to analyze false positive rates.

### ADR-008: Streamlit for Dashboard over Grafana
- **Status**: Accepted
- **Context**: Phase 3 requires a dashboard to visualize pipeline output. Need to choose between Streamlit (Python-native data app framework) and Grafana (monitoring/dashboarding platform with native PostgreSQL support).
- **Alternatives Considered**:
  - Option A — Streamlit:
    - Pros: Pure Python (consistent with pipeline codebase), fast prototyping, custom analysis logic in the same language as the pipeline, Plotly integration for interactive charts, single-file deployment, no separate infrastructure.
    - Cons: Not a "real" dashboarding platform. No built-in alerting or user management. Requires running a Python process.
  - Option B — Grafana:
    - Pros: Industry standard for monitoring dashboards. Native PostgreSQL data source. Built-in alerting, panels, and sharing. Impressive on a resume.
    - Cons: Separate Docker container. SQL-only queries (no Python analysis). Configuration is JSON/YAML-based, not code. Harder to version control dashboard definitions. Less flexibility for custom analytical views.
- **Decision**: Streamlit with Plotly charts.
- **Rationale**: The dashboard's primary purpose is analytical insight ("how do product prices compare to CPI?"), not operational monitoring. Streamlit excels at this — we can write custom analysis (% change calculations, IQR band visualization) directly in Python, reuse the existing `pipeline.config` module for DB connection, and keep the entire project in one language. Grafana would be the right choice if the primary use case were 24/7 monitoring with alerting, but we already have Slack alerts for that (Phase 2). The dashboard is for exploration and storytelling.
- **Consequences**: Dashboard runs as a separate process (`streamlit run dashboard.py`). No built-in auth or sharing — acceptable for a portfolio project. Can be launched via `python main.py dashboard` for convenience.
