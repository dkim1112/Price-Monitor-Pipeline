-- Migration 002: Data Quality & Monitoring tables
-- Run manually: docker exec -i price-monitor-db psql -U pipeline -d price_monitor < db/migration_002_quality.sql

-- Price anomaly log (flagged by IQR-based detection)
CREATE TABLE IF NOT EXISTS mart.price_anomalies (
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

CREATE INDEX IF NOT EXISTS idx_anomalies_item_date
    ON mart.price_anomalies (item_code, price_date);
CREATE INDEX IF NOT EXISTS idx_anomalies_unresolved
    ON mart.price_anomalies (resolved) WHERE NOT resolved;

-- Schema baseline for drift detection
CREATE TABLE IF NOT EXISTS raw.schema_baseline (
    id              SERIAL PRIMARY KEY,
    source          VARCHAR(20) NOT NULL,
    check_type      VARCHAR(50) NOT NULL,
    baseline_value  JSONB NOT NULL,
    checked_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    drift_detected  BOOLEAN NOT NULL DEFAULT FALSE,
    drift_details   TEXT
);

CREATE INDEX IF NOT EXISTS idx_baseline_source
    ON raw.schema_baseline (source, check_type, checked_at DESC);

-- Quality check results log
CREATE TABLE IF NOT EXISTS raw.quality_check_log (
    id              BIGSERIAL PRIMARY KEY,
    check_name      VARCHAR(100) NOT NULL,
    source          VARCHAR(20),
    status          VARCHAR(20) NOT NULL DEFAULT 'PASS',  -- PASS, WARN, FAIL
    metric_value    DECIMAL(12, 4),
    threshold       DECIMAL(12, 4),
    details         JSONB,
    checked_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_quality_check_time
    ON raw.quality_check_log (check_name, checked_at DESC);
