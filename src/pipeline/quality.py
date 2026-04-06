"""
Data Quality Validation Module.

Runs after collection and/or aggregation to check:
  1. Data freshness — is our data stale?
  2. Completeness — are all expected items present?
  3. Null ratio — are prices actually populated?
  4. Price anomalies — sudden jumps via IQR method
  5. ECOS value range — CPI within expected bounds

Each check returns a result dict:
  {"check": str, "status": "PASS"|"WARN"|"FAIL", "value": num, "threshold": num, "message": str}

Results are logged to raw.quality_check_log for historical tracking.
"""

import logging
from datetime import datetime, timedelta

from pipeline.config import (
    KOSTAT_FRESHNESS_MAX_DAYS,
    ECOS_FRESHNESS_MAX_DAYS,
    KOSTAT_ITEM_COVERAGE_MIN_PCT,
    KOSTAT_NULL_RATIO_MAX_PCT,
    ECOS_CPI_RANGE_LOW,
    ECOS_CPI_RANGE_HIGH,
    PRICE_ANOMALY_IQR_MULTIPLIER,
)
from pipeline.alerts import send_alert, format_quality_report, WARNING, CRITICAL, INFO

logger = logging.getLogger(__name__)


# ── Individual Checks ─────────────────────────────────────────────────

def check_kostat_freshness(cursor) -> dict:
    """Check if KOSTAT data is stale (older than threshold)."""
    cursor.execute("SELECT MAX(price_date) FROM raw.kostat_products")
    row = cursor.fetchone()
    latest = row[0] if row and row[0] else None

    if latest is None:
        return _result("kostat_freshness", "FAIL", None, KOSTAT_FRESHNESS_MAX_DAYS,
                        "No KOSTAT data found in database")

    age_days = (datetime.now().date() - latest).days
    status = "PASS" if age_days <= KOSTAT_FRESHNESS_MAX_DAYS else "WARN"
    return _result("kostat_freshness", status, age_days, KOSTAT_FRESHNESS_MAX_DAYS,
                    f"Latest KOSTAT data: {latest} ({age_days} days old)")


def check_ecos_freshness(cursor) -> dict:
    """Check if ECOS data is stale (older than threshold)."""
    cursor.execute("SELECT MAX(time_period) FROM raw.ecos_indices")
    row = cursor.fetchone()
    latest = row[0] if row and row[0] else None

    if latest is None:
        return _result("ecos_freshness", "FAIL", None, ECOS_FRESHNESS_MAX_DAYS,
                        "No ECOS data found in database")

    # time_period is YYYYMM — approximate age by comparing to current month
    try:
        latest_date = datetime.strptime(latest + "15", "%Y%m%d").date()  # mid-month
        age_days = (datetime.now().date() - latest_date).days
    except ValueError:
        return _result("ecos_freshness", "FAIL", None, ECOS_FRESHNESS_MAX_DAYS,
                        f"Invalid time_period format: {latest}")

    status = "PASS" if age_days <= ECOS_FRESHNESS_MAX_DAYS else "WARN"
    return _result("ecos_freshness", status, age_days, ECOS_FRESHNESS_MAX_DAYS,
                    f"Latest ECOS period: {latest} (~{age_days} days old)")


def check_kostat_completeness(cursor) -> dict:
    """Check if all expected KOSTAT items were collected in the latest run."""
    cursor.execute("""
        SELECT COUNT(DISTINCT item_code)
        FROM raw.kostat_products
        WHERE price_date = (SELECT MAX(price_date) FROM raw.kostat_products)
    """)
    collected = cursor.fetchone()[0] or 0

    # Expected: 124 active items (from item catalog)
    expected = 124
    coverage_pct = (collected / expected * 100) if expected > 0 else 0
    status = "PASS" if coverage_pct >= KOSTAT_ITEM_COVERAGE_MIN_PCT else "WARN"
    return _result("kostat_completeness", status, round(coverage_pct, 1),
                    KOSTAT_ITEM_COVERAGE_MIN_PCT,
                    f"{collected}/{expected} items collected ({coverage_pct:.1f}%)")


def check_kostat_null_ratio(cursor) -> dict:
    """Check percentage of NULL sale_price in latest collection."""
    cursor.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE sale_price IS NULL) AS nulls
        FROM raw.kostat_products
        WHERE price_date = (SELECT MAX(price_date) FROM raw.kostat_products)
    """)
    row = cursor.fetchone()
    total, nulls = row[0] or 0, row[1] or 0

    if total == 0:
        return _result("kostat_null_ratio", "FAIL", None, KOSTAT_NULL_RATIO_MAX_PCT,
                        "No KOSTAT data for latest date")

    null_pct = nulls / total * 100
    status = "PASS" if null_pct <= KOSTAT_NULL_RATIO_MAX_PCT else "WARN"
    return _result("kostat_null_ratio", status, round(null_pct, 2),
                    KOSTAT_NULL_RATIO_MAX_PCT,
                    f"{nulls}/{total} rows with NULL sale_price ({null_pct:.2f}%)")


def check_ecos_value_range(cursor) -> dict:
    """Check if ECOS CPI values are within expected range."""
    cursor.execute("""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE data_value < %s OR data_value > %s) AS outliers,
               MIN(data_value), MAX(data_value)
        FROM raw.ecos_indices
        WHERE data_value IS NOT NULL
          AND time_period = (SELECT MAX(time_period) FROM raw.ecos_indices)
    """, (ECOS_CPI_RANGE_LOW, ECOS_CPI_RANGE_HIGH))
    row = cursor.fetchone()
    total, outliers, min_val, max_val = row

    if not total:
        return _result("ecos_value_range", "FAIL", None, None,
                        "No ECOS data for latest period")

    outlier_pct = outliers / total * 100 if total else 0
    status = "PASS" if outliers == 0 else "WARN"
    return _result("ecos_value_range", status, outliers, 0,
                    f"{outliers}/{total} values outside [{ECOS_CPI_RANGE_LOW}, {ECOS_CPI_RANGE_HIGH}] "
                    f"(range: {min_val}–{max_val})")


def detect_price_anomalies(cursor) -> dict:
    """
    IQR-based anomaly detection: compare latest median prices to historical medians.
    Flags items where the change exceeds IQR_MULTIPLIER × IQR.
    """
    # Get the two most recent price dates
    cursor.execute("""
        SELECT DISTINCT price_date
        FROM mart.daily_price_summary
        ORDER BY price_date DESC
        LIMIT 2
    """)
    dates = [r[0] for r in cursor.fetchall()]

    if len(dates) < 2:
        return _result("price_anomalies", "PASS", 0, None,
                        "Not enough historical data for anomaly detection (need ≥2 dates)")

    current_date, previous_date = dates[0], dates[1]

    # Compare median prices between the two dates
    cursor.execute("""
        SELECT
            c.item_code,
            c.item_name,
            c.price_date AS current_date,
            c.median_price AS current_median,
            p.median_price AS previous_median,
            c.p75_price - c.p25_price AS iqr
        FROM mart.daily_price_summary c
        JOIN mart.daily_price_summary p
            ON c.item_code = p.item_code AND p.price_date = %s
        WHERE c.price_date = %s
          AND p.median_price > 0
          AND c.median_price > 0
    """, (previous_date, current_date))

    anomalies = []
    for row in cursor.fetchall():
        item_code, item_name, cur_date, cur_median, prev_median, iqr = row
        if iqr is None or iqr <= 0:
            continue

        change = abs(cur_median - prev_median)
        threshold = PRICE_ANOMALY_IQR_MULTIPLIER * iqr

        if change > threshold:
            pct_change = ((cur_median - prev_median) / prev_median * 100) if prev_median else 0
            anomalies.append({
                "item_code": item_code,
                "item_name": item_name,
                "price_date": cur_date,
                "previous_median": prev_median,
                "current_median": cur_median,
                "pct_change": round(pct_change, 2),
                "iqr_range": iqr,
            })

    # Insert anomalies into mart.price_anomalies
    if anomalies:
        from psycopg2.extras import execute_batch
        execute_batch(
            cursor,
            """
            INSERT INTO mart.price_anomalies
                (item_code, item_name, price_date, previous_median,
                 current_median, pct_change, iqr_range)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            [(a["item_code"], a["item_name"], a["price_date"],
              a["previous_median"], a["current_median"],
              a["pct_change"], a["iqr_range"]) for a in anomalies],
        )
        cursor.connection.commit()

    status = "PASS" if len(anomalies) == 0 else "WARN"
    return _result("price_anomalies", status, len(anomalies), 0,
                    f"{len(anomalies)} items flagged "
                    f"(comparing {current_date} vs {previous_date})")


# ── Run All Checks ────────────────────────────────────────────────────

def run_all_checks(cursor) -> list[dict]:
    """
    Run all quality checks and return results.
    Also logs results to raw.quality_check_log and fires alerts.
    """
    logger.info("Running quality validation checks...")

    checks = [
        check_kostat_freshness,
        check_ecos_freshness,
        check_kostat_completeness,
        check_kostat_null_ratio,
        check_ecos_value_range,
        detect_price_anomalies,
    ]

    results = []
    for check_fn in checks:
        try:
            result = check_fn(cursor)
            results.append(result)
            _log_result(cursor, result)
            logger.info(
                "[%s] %s: %s",
                result["status"], result["check"], result["message"]
            )
        except Exception as e:
            logger.error("Check %s failed with error: %s", check_fn.__name__, e)
            results.append(_result(check_fn.__name__, "FAIL", None, None, str(e)))

    cursor.connection.commit()

    # Determine overall severity and send alert
    has_fails = any(r["status"] == "FAIL" for r in results)
    has_warns = any(r["status"] == "WARN" for r in results)

    if has_fails:
        send_alert(CRITICAL, "Quality Check Failures",
                   format_quality_report(results))
    elif has_warns:
        send_alert(WARNING, "Quality Check Warnings",
                   format_quality_report(results))
    else:
        send_alert(INFO, "Quality Checks Passed",
                   format_quality_report(results))

    return results


# ── Helpers ───────────────────────────────────────────────────────────

def _result(check: str, status: str, value, threshold, message: str) -> dict:
    return {
        "check": check,
        "status": status,
        "value": value,
        "threshold": threshold,
        "message": message,
    }


def _log_result(cursor, result: dict):
    """Persist check result to raw.quality_check_log."""
    import json
    cursor.execute("""
        INSERT INTO raw.quality_check_log
            (check_name, source, status, metric_value, threshold, details)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (
        result["check"],
        result["check"].split("_")[0] if "_" in result["check"] else None,
        result["status"],
        float(result["value"]) if result["value"] is not None else None,
        float(result["threshold"]) if result["threshold"] is not None else None,
        json.dumps({"message": result["message"]}),
    ))
