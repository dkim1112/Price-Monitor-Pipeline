-- Price Monitor Pipeline — PostgreSQL Schema
-- Based on 01-SCHEMA-DESIGN.md
-- Run via: docker-compose up (auto-executed on first start)

-- ══════════════════════════════════════════════════════════════════════
-- SCHEMAS
-- ══════════════════════════════════════════════════════════════════════

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS mart;

-- ══════════════════════════════════════════════════════════════════════
-- RAW LAYER
-- ══════════════════════════════════════════════════════════════════════

-- Collection log must be created FIRST (FK target)
CREATE TABLE raw.collection_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source          VARCHAR(20) NOT NULL,              -- 'KOSTAT' or 'ECOS'
    endpoint        VARCHAR(100) NOT NULL,             -- e.g., 'getPriceInfo', 'StatisticSearch'
    request_params  JSONB,                             -- full request parameters
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    status          VARCHAR(20) NOT NULL DEFAULT 'RUNNING',  -- 'SUCCESS', 'FAILED', 'PARTIAL', 'RUNNING'
    records_fetched INTEGER DEFAULT 0,
    error_message   TEXT,
    http_status     INTEGER
);

CREATE INDEX idx_log_source_time ON raw.collection_log (source, started_at);

-- KOSTAT product-level prices (partitioned by month)
CREATE TABLE raw.kostat_products (
    id              BIGSERIAL,
    collected_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    item_code       VARCHAR(10) NOT NULL,
    item_name       VARCHAR(100),
    product_id      VARCHAR(30),
    product_name    TEXT,
    sale_price      INTEGER,
    discount_price  INTEGER,
    benefit_price   INTEGER,
    price_date      DATE NOT NULL,
    api_call_id     UUID NOT NULL REFERENCES raw.collection_log(id),
    PRIMARY KEY (id, price_date)
) PARTITION BY RANGE (price_date);

-- Create partitions for 2025 and 2026
DO $$
DECLARE
    y INT;
    m INT;
    start_date DATE;
    end_date DATE;
    partition_name TEXT;
BEGIN
    FOR y IN 2025..2026 LOOP
        FOR m IN 1..12 LOOP
            start_date := make_date(y, m, 1);
            end_date := start_date + INTERVAL '1 month';
            partition_name := format('raw.kostat_products_%s_%s',
                                     y, lpad(m::text, 2, '0'));
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF raw.kostat_products
                 FOR VALUES FROM (%L) TO (%L)',
                partition_name, start_date, end_date
            );
        END LOOP;
    END LOOP;
END $$;

CREATE INDEX idx_kostat_item_date ON raw.kostat_products (item_code, price_date);
CREATE INDEX idx_kostat_price_date ON raw.kostat_products (price_date);

-- ECOS CPI/PPI index values
CREATE TABLE raw.ecos_indices (
    id              BIGSERIAL PRIMARY KEY,
    collected_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    stat_code       VARCHAR(20) NOT NULL,
    stat_name       VARCHAR(200),
    item_code1      VARCHAR(20),
    item_name1      VARCHAR(200),
    item_code2      VARCHAR(20),
    item_name2      VARCHAR(200),
    item_code3      VARCHAR(20),
    item_name3      VARCHAR(200),
    item_code4      VARCHAR(20),
    item_name4      VARCHAR(200),
    unit_name       VARCHAR(50),
    weight          DECIMAL(10, 2),
    time_period     VARCHAR(10) NOT NULL,
    data_value      DECIMAL(10, 2),
    api_call_id     UUID NOT NULL REFERENCES raw.collection_log(id)
);

CREATE INDEX idx_ecos_stat_time ON raw.ecos_indices (stat_code, item_code1, time_period);
CREATE INDEX idx_ecos_time ON raw.ecos_indices (time_period);

-- Prevent duplicate ECOS rows for same stat+item+period per collection run
CREATE UNIQUE INDEX idx_ecos_dedup
    ON raw.ecos_indices (stat_code, item_code1, time_period, api_call_id);

-- ══════════════════════════════════════════════════════════════════════
-- MART LAYER
-- ══════════════════════════════════════════════════════════════════════

CREATE TABLE mart.daily_price_summary (
    id              BIGSERIAL PRIMARY KEY,
    item_code       VARCHAR(10) NOT NULL,
    item_name       VARCHAR(100),
    price_date      DATE NOT NULL,
    product_count   INTEGER,
    median_price    INTEGER,
    mean_price      DECIMAL(12, 2),
    min_price       INTEGER,
    max_price       INTEGER,
    p25_price       INTEGER,
    p75_price       INTEGER,
    median_discount DECIMAL(5, 2),
    computed_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (item_code, price_date)
);

CREATE INDEX idx_daily_summary_item_date ON mart.daily_price_summary (item_code, price_date);

CREATE TABLE mart.monthly_cpi_index (
    id              BIGSERIAL PRIMARY KEY,
    year_month      VARCHAR(6) NOT NULL,
    item_code       VARCHAR(20) NOT NULL,
    item_name       VARCHAR(200) NOT NULL,
    index_value     DECIMAL(10, 2) NOT NULL,
    weight          DECIMAL(8, 2),
    parent_code     VARCHAR(20),
    computed_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (year_month, item_code)
);

CREATE INDEX idx_cpi_item_month ON mart.monthly_cpi_index (item_code, year_month);

CREATE TABLE mart.item_mapping (
    id              SERIAL PRIMARY KEY,
    kostat_code     VARCHAR(10) NOT NULL,
    kostat_name     VARCHAR(100),
    ecos_code       VARCHAR(20) NOT NULL,
    ecos_name       VARCHAR(200),
    mapping_notes   TEXT,
    confidence      VARCHAR(10) DEFAULT 'HIGH',
    UNIQUE (kostat_code, ecos_code)
);

-- The price_vs_cpi view joins everything together
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

-- ══════════════════════════════════════════════════════════════════════
-- DONE
-- ══════════════════════════════════════════════════════════════════════
