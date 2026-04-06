"""
Pipeline configuration — all settings in one place.
Uses environment variables with sensible defaults for local development.
"""

import os

# ── API Keys ──────────────────────────────────────────────────────────
ECOS_API_KEY = os.getenv("ECOS_API_KEY", "")
DATA_GO_KR_KEY = os.getenv("DATA_GO_KR_KEY", "")

# ── API Endpoints ─────────────────────────────────────────────────────
ECOS_BASE_URL = "https://ecos.bok.or.kr/api"
KOSTAT_BASE_URL = "http://apis.data.go.kr/1240000/bpp_openapi"

# ── ECOS Settings ─────────────────────────────────────────────────────
ECOS_STAT_CODE_CPI = "901Y009"     # 소비자물가지수
ECOS_RETURN_FORMAT = "json"
ECOS_LANGUAGE = "kr"
ECOS_PAGE_SIZE = 1000              # max rows per request

# ── KOSTAT Settings ───────────────────────────────────────────────────
KOSTAT_PAGE_SIZE = 1000            # max rows per request (API limit)
KOSTAT_MAX_DATE_RANGE_DAYS = 30    # API constraint
KOSTAT_DATA_LAG_DAYS = 14          # typical lag; adaptive logic handles variance

# ── Database ──────────────────────────────────────────────────────────
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "price_monitor")
DB_USER = os.getenv("DB_USER", "pipeline")
DB_PASSWORD = os.getenv("DB_PASSWORD", "pipeline_dev_2026")

def get_db_url():
    """SQLAlchemy-style connection URL."""
    return f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

def get_db_params():
    """psycopg2-style connection params dict."""
    return {
        "host": DB_HOST,
        "port": DB_PORT,
        "dbname": DB_NAME,
        "user": DB_USER,
        "password": DB_PASSWORD,
    }

# ── Retry Settings ────────────────────────────────────────────────────
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = [5, 15, 45]   # exponential-ish
REQUEST_TIMEOUT = 30                   # seconds per HTTP request

# ── Data Quality Thresholds ──────────────────────────────────────────
KOSTAT_FRESHNESS_MAX_DAYS = 21         # WARN if latest KOSTAT data older than this
ECOS_FRESHNESS_MAX_DAYS = 45           # WARN if latest ECOS period older than this
KOSTAT_ITEM_COVERAGE_MIN_PCT = 90      # WARN if fewer than this % of items collected
KOSTAT_NULL_RATIO_MAX_PCT = 10         # WARN if NULL sale_price exceeds this %
ECOS_CPI_RANGE_LOW = 30.0             # CPI values below this are flagged (sub-indices can be low)
ECOS_CPI_RANGE_HIGH = 250.0           # CPI values above this are flagged (sub-indices can spike)
PRICE_ANOMALY_IQR_MULTIPLIER = 2.0    # Flag if price change > N × IQR

# ── Alerting ─────────────────────────────────────────────────────────
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
