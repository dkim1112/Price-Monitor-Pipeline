# Unified Schema Design

> This document defines the PostgreSQL schema for the price monitoring pipeline.
> Two data sources (KOSTAT + ECOS) with fundamentally different structures must coexist.

## Design Principles

1. **Raw layer preserves source data as-is** — no transformation, no loss
2. **Mart layer provides unified, query-friendly views** — aggregated, clean
3. **Source metadata is always tracked** — when was it collected, from which API call
4. **Schema supports time-series analysis** — partitioned by date for efficient range queries

---

## Schema: `raw` (Source Data Layer)

### `raw.kostat_products`
Stores every individual product listing from the KOSTAT Online Price API.

```sql
CREATE TABLE raw.kostat_products (
    id              BIGSERIAL PRIMARY KEY,
    collected_at    TIMESTAMP NOT NULL DEFAULT NOW(),  -- when we fetched this
    item_code       VARCHAR(10) NOT NULL,              -- ic: category code (e.g., "A01101")
    item_name       VARCHAR(100),                      -- in: category name (e.g., "쌀")
    product_id      VARCHAR(30),                       -- pi: e-commerce product ID
    product_name    TEXT,                               -- pn: full product name
    sale_price      INTEGER,                            -- sp: listed sale price (won)
    discount_price  INTEGER,                            -- dp: discount price (won)
    benefit_price   INTEGER,                            -- bp: additional benefit/coupon (won)
    price_date      DATE NOT NULL,                     -- sd: the date the price was observed
    api_call_id     UUID NOT NULL REFERENCES raw.collection_log(id)
) PARTITION BY RANGE (price_date);

-- Partition by month for manageable chunks
CREATE TABLE raw.kostat_products_2026_01 PARTITION OF raw.kostat_products
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE raw.kostat_products_2026_02 PARTITION OF raw.kostat_products
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
-- (create partitions as needed)

CREATE INDEX idx_kostat_item_date ON raw.kostat_products (item_code, price_date);
CREATE INDEX idx_kostat_price_date ON raw.kostat_products (price_date);
```

### `raw.ecos_indices`
Stores CPI/PPI index values from the Bank of Korea ECOS API.

```sql
CREATE TABLE raw.ecos_indices (
    id              BIGSERIAL PRIMARY KEY,
    collected_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    stat_code       VARCHAR(20) NOT NULL,              -- e.g., "901Y009"
    stat_name       VARCHAR(200),                      -- e.g., "4.2.1. 소비자물가지수"
    item_code1      VARCHAR(20),                       -- hierarchical item codes
    item_name1      VARCHAR(200),                      -- e.g., "총지수", "식료품", "쌀"
    item_code2      VARCHAR(20),
    item_name2      VARCHAR(200),
    item_code3      VARCHAR(20),
    item_name3      VARCHAR(200),
    item_code4      VARCHAR(20),
    item_name4      VARCHAR(200),
    unit_name       VARCHAR(50),                       -- e.g., "2020=100"
    weight          DECIMAL(10, 2),                    -- WGT: CPI basket weight (e.g., 1000 for 총지수)
    time_period     VARCHAR(10) NOT NULL,              -- e.g., "202401" (YYYYMM)
    data_value      DECIMAL(10, 2),                    -- the actual index value
    api_call_id     UUID NOT NULL REFERENCES raw.collection_log(id)
);

CREATE INDEX idx_ecos_stat_time ON raw.ecos_indices (stat_code, item_code1, time_period);
CREATE INDEX idx_ecos_time ON raw.ecos_indices (time_period);
```

### `raw.collection_log`
Tracks every API call for observability and debugging.

```sql
CREATE TABLE raw.collection_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source          VARCHAR(20) NOT NULL,              -- 'KOSTAT' or 'ECOS'
    endpoint        VARCHAR(100) NOT NULL,             -- e.g., 'getPriceInfo', 'StatisticSearch'
    request_params  JSONB,                             -- full request parameters
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    status          VARCHAR(20) NOT NULL,              -- 'SUCCESS', 'FAILED', 'PARTIAL'
    records_fetched INTEGER DEFAULT 0,
    error_message   TEXT,
    http_status     INTEGER
);

CREATE INDEX idx_log_source_time ON raw.collection_log (source, started_at);
```

**ECOS error codes** (from official 개발명세서, must handle in collection module):

| Code | Type | Description | Action |
|------|------|-------------|--------|
| 100 | Info | Invalid authentication key | Alert + abort |
| 200 | Info | No data available | Log + skip (normal for future dates) |
| 300-301 | Error | Pagination issues | Fix request params |
| 400 | Error | Search timeout (>60s) | Retry with smaller range |
| 500-601 | Error | Server/DB/SQL error | Retry with backoff |
| **602** | **Error** | **API call limit exceeded** | **Exponential backoff + alert** |

---

## Schema: `mart` (Aggregated / Analysis Layer)

### `mart.daily_price_summary`
Daily aggregates of KOSTAT product-level data per item category.

```sql
CREATE TABLE mart.daily_price_summary (
    id              BIGSERIAL PRIMARY KEY,
    item_code       VARCHAR(10) NOT NULL,
    item_name       VARCHAR(100),
    price_date      DATE NOT NULL,
    product_count   INTEGER,                           -- how many products were listed
    median_price    INTEGER,                           -- median of sale_price
    mean_price      DECIMAL(12, 2),                    -- mean of sale_price
    min_price       INTEGER,
    max_price       INTEGER,
    p25_price       INTEGER,                           -- 25th percentile
    p75_price       INTEGER,                           -- 75th percentile
    median_discount DECIMAL(5, 2),                     -- median discount rate (1 - dp/sp)
    computed_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (item_code, price_date)
);

CREATE INDEX idx_daily_summary_item_date ON mart.daily_price_summary (item_code, price_date);
```

### `mart.monthly_cpi_index`
Cleaned ECOS CPI data, flattened for easy querying.

```sql
CREATE TABLE mart.monthly_cpi_index (
    id              BIGSERIAL PRIMARY KEY,
    year_month      VARCHAR(6) NOT NULL,               -- "202401"
    item_code       VARCHAR(20) NOT NULL,              -- ECOS item_code1
    item_name       VARCHAR(200) NOT NULL,
    index_value     DECIMAL(10, 2) NOT NULL,           -- CPI value (base: 2020=100)
    weight          DECIMAL(8, 2),                     -- item weight in CPI basket
    parent_code     VARCHAR(20),                       -- for hierarchy navigation
    computed_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (year_month, item_code)
);

CREATE INDEX idx_cpi_item_month ON mart.monthly_cpi_index (item_code, year_month);
```

### `mart.price_vs_cpi`
The "so what?" view — maps KOSTAT product categories to CPI categories for comparison.

```sql
CREATE VIEW mart.price_vs_cpi AS
SELECT
    k.item_code AS kostat_item_code,
    k.item_name AS kostat_item_name,
    k.price_date,
    k.median_price,
    k.product_count,
    c.item_code AS cpi_item_code,
    c.item_name AS cpi_item_name,
    c.index_value AS cpi_index,
    c.year_month,
    m.kostat_code,
    m.ecos_code
FROM mart.daily_price_summary k
JOIN mart.item_mapping m ON k.item_code = m.kostat_code
JOIN mart.monthly_cpi_index c ON m.ecos_code = c.item_code
    AND TO_CHAR(k.price_date, 'YYYYMM') = c.year_month;
```

### `mart.item_mapping`
Manual mapping between KOSTAT item codes and ECOS CPI item codes.

```sql
CREATE TABLE mart.item_mapping (
    id              SERIAL PRIMARY KEY,
    kostat_code     VARCHAR(10) NOT NULL,              -- e.g., "A01101"
    kostat_name     VARCHAR(100),                      -- e.g., "쌀"
    ecos_code       VARCHAR(20) NOT NULL,              -- e.g., "A01101" (CPI item code)
    ecos_name       VARCHAR(200),                      -- e.g., "쌀"
    mapping_notes   TEXT,                              -- any caveats about the mapping
    confidence      VARCHAR(10) DEFAULT 'HIGH',        -- HIGH, MEDIUM, LOW
    UNIQUE (kostat_code, ecos_code)
);
```

### `mart.price_anomalies` (Phase 2)
Flagged price outliers from IQR-based anomaly detection.

```sql
CREATE TABLE mart.price_anomalies (
    id              BIGSERIAL PRIMARY KEY,
    item_code       VARCHAR(10) NOT NULL,
    item_name       VARCHAR(100),
    price_date      DATE NOT NULL,
    previous_median INTEGER,
    current_median  INTEGER,
    pct_change      DECIMAL(8, 2),
    iqr_range       INTEGER,
    flagged_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved        BOOLEAN NOT NULL DEFAULT FALSE
);
```

---

## Schema: Quality & Monitoring (Phase 2)

### `raw.schema_baseline`
Stored baselines for schema drift detection.

```sql
CREATE TABLE raw.schema_baseline (
    id              SERIAL PRIMARY KEY,
    source          VARCHAR(20) NOT NULL,       -- 'KOSTAT' or 'ECOS'
    check_type      VARCHAR(50) NOT NULL,       -- 'item_catalog' or 'field_presence'
    baseline_value  JSONB NOT NULL,             -- snapshot of current schema
    checked_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    drift_detected  BOOLEAN NOT NULL DEFAULT FALSE,
    drift_details   TEXT
);
```

### `raw.quality_check_log`
Historical log of all quality check results for threshold tuning.

```sql
CREATE TABLE raw.quality_check_log (
    id              BIGSERIAL PRIMARY KEY,
    check_name      VARCHAR(100) NOT NULL,
    source          VARCHAR(20),
    status          VARCHAR(20) NOT NULL DEFAULT 'PASS',  -- PASS, WARN, FAIL
    metric_value    DECIMAL(12, 4),
    threshold       DECIMAL(12, 4),
    details         JSONB,
    checked_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

## Data Flow

```
KOSTAT API  ──→  raw.kostat_products  ──→  mart.daily_price_summary  ──┐
                                                                        ├──→  mart.price_vs_cpi
ECOS API    ──→  raw.ecos_indices     ──→  mart.monthly_cpi_index   ──┘
                                                                        │
Both APIs   ──→  raw.collection_log                                     │
                                                                        │
                                           mart.item_mapping  ──────────┘

Quality layer (Phase 2):
Both APIs   ──→  raw.schema_baseline  (drift detection)
Pipeline    ──→  raw.quality_check_log (check history)
Aggregation ──→  mart.price_anomalies (flagged outliers)
```

---

## Key Design Decisions

1. **Partitioning**: `raw.kostat_products` is partitioned by `price_date` (monthly). This keeps queries fast for date-range analysis and makes cleanup easy (drop old partitions).

2. **Aggregation**: `mart.daily_price_summary` uses **median** as the primary price metric (more robust than mean for e-commerce data with outliers/bulk pricing).

3. **Item mapping**: The `mart.item_mapping` table is manually curated. KOSTAT and ECOS use similar but not identical category codes. This mapping is a deliberate design choice — it's transparent, auditable, and the "confidence" column acknowledges uncertainty.

4. **Collection log**: Every API call is logged with full parameters and status. This enables data lineage tracking and makes debugging pipeline failures straightforward.

5. **No denormalization in raw layer**: Raw data preserves the source structure exactly. All transformations happen in the mart layer, making the pipeline idempotent and reprocessable.

6. **Data lineage enforcement**: `api_call_id` is `NOT NULL` with a foreign key to `raw.collection_log(id)`. Every row in the raw layer is traceable to a specific API call — when it was made, what parameters were used, whether it succeeded. This is critical for debugging data quality issues.

---

## Estimated Storage

| Table | Est. rows/week | Est. size/month |
|-------|---------------|-----------------|
| raw.kostat_products | ~500K | ~200 MB |
| raw.ecos_indices | ~1,743 (monthly) | < 1 MB |
| mart.daily_price_summary | ~124 per collection date | < 1 MB |
| mart.monthly_cpi_index | ~1,743 (monthly) | < 1 MB |
| mart.price_anomalies | varies (flagged outliers only) | < 1 MB |
| raw.collection_log | ~300 | < 1 MB |
| raw.schema_baseline | ~2 per run (KOSTAT + ECOS) | < 1 MB |
| raw.quality_check_log | ~6 per run (one per check) | < 1 MB |

Total estimated: **~200 MB/month** (dominated by raw KOSTAT data).
At this rate, a year of data fits comfortably in < 3 GB.
