"""
Aggregation logic: raw → mart layer.

Transforms:
  raw.kostat_products → mart.daily_price_summary
  raw.ecos_indices    → mart.monthly_cpi_index

Uses SQL-based aggregation (pushes compute to PostgreSQL) rather than
pulling data into Python. This is more efficient and idempotent.
"""

import logging

logger = logging.getLogger(__name__)


def refresh_daily_price_summary(cursor, price_date: str = None):
    """
    Compute daily price aggregates from raw KOSTAT data.
    Uses UPSERT (INSERT ... ON CONFLICT UPDATE) for idempotency.

    Args:
        cursor: DB cursor
        price_date: Optional YYYY-MM-DD string to refresh only one date.
                    If None, refreshes all dates that have raw data but no summary.
    """
    if price_date:
        date_filter = "AND k.price_date = %s"
        params = (price_date,)
    else:
        date_filter = """
            AND NOT EXISTS (
                SELECT 1 FROM mart.daily_price_summary s
                WHERE s.item_code = k.item_code AND s.price_date = k.price_date
            )
        """
        params = ()

    sql = f"""
        INSERT INTO mart.daily_price_summary
            (item_code, item_name, price_date, product_count,
             median_price, mean_price, min_price, max_price,
             p25_price, p75_price, median_discount)
        SELECT
            k.item_code,
            MAX(k.item_name) AS item_name,
            k.price_date,
            COUNT(*) AS product_count,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY k.sale_price)::INTEGER AS median_price,
            AVG(k.sale_price)::DECIMAL(12,2) AS mean_price,
            MIN(k.sale_price) AS min_price,
            MAX(k.sale_price) AS max_price,
            PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY k.sale_price)::INTEGER AS p25_price,
            PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY k.sale_price)::INTEGER AS p75_price,
            PERCENTILE_CONT(0.5) WITHIN GROUP (
                ORDER BY CASE
                    WHEN k.sale_price > 0 AND k.discount_price IS NOT NULL
                    THEN 1.0 - (k.discount_price::DECIMAL / k.sale_price)
                    ELSE NULL
                END
            )::DECIMAL(5,2) AS median_discount
        FROM raw.kostat_products k
        WHERE k.sale_price > 0
            {date_filter}
        GROUP BY k.item_code, k.price_date
        ON CONFLICT (item_code, price_date) DO UPDATE SET
            item_name = EXCLUDED.item_name,
            product_count = EXCLUDED.product_count,
            median_price = EXCLUDED.median_price,
            mean_price = EXCLUDED.mean_price,
            min_price = EXCLUDED.min_price,
            max_price = EXCLUDED.max_price,
            p25_price = EXCLUDED.p25_price,
            p75_price = EXCLUDED.p75_price,
            median_discount = EXCLUDED.median_discount,
            computed_at = NOW()
    """

    cursor.execute(sql, params)
    affected = cursor.rowcount
    logger.info("daily_price_summary: %d rows upserted", affected)
    return affected


def refresh_monthly_cpi_index(cursor, year_month: str = None):
    """
    Compute monthly CPI index from raw ECOS data.
    Uses UPSERT for idempotency.

    Args:
        cursor: DB cursor
        year_month: Optional YYYYMM string to refresh only one month.
                    If None, refreshes all months not yet in mart.
    """
    if year_month:
        date_filter = "AND e.time_period = %s"
        params = (year_month,)
    else:
        date_filter = """
            AND NOT EXISTS (
                SELECT 1 FROM mart.monthly_cpi_index c
                WHERE c.item_code = e.item_code1 AND c.year_month = e.time_period
            )
        """
        params = ()

    sql = f"""
        INSERT INTO mart.monthly_cpi_index
            (year_month, item_code, item_name, index_value, weight, parent_code)
        SELECT DISTINCT ON (e.time_period, e.item_code1)
            e.time_period AS year_month,
            e.item_code1 AS item_code,
            e.item_name1 AS item_name,
            e.data_value AS index_value,
            e.weight,
            NULL AS parent_code  -- could be derived from StatisticItemList hierarchy
        FROM raw.ecos_indices e
        WHERE e.data_value IS NOT NULL
            AND e.item_code1 IS NOT NULL
            {date_filter}
        ORDER BY e.time_period, e.item_code1, e.collected_at DESC
        ON CONFLICT (year_month, item_code) DO UPDATE SET
            item_name = EXCLUDED.item_name,
            index_value = EXCLUDED.index_value,
            weight = EXCLUDED.weight,
            computed_at = NOW()
    """

    cursor.execute(sql, params)
    affected = cursor.rowcount
    logger.info("monthly_cpi_index: %d rows upserted", affected)
    return affected


def run_aggregation(cursor, price_date: str = None, year_month: str = None):
    """
    Run all aggregation steps.

    Args:
        cursor: DB cursor
        price_date: Optional date filter for KOSTAT aggregation (YYYY-MM-DD)
        year_month: Optional month filter for ECOS aggregation (YYYYMM)

    Returns:
        dict with counts
    """
    logger.info("Running aggregation...")

    daily_count = refresh_daily_price_summary(cursor, price_date)
    cpi_count = refresh_monthly_cpi_index(cursor, year_month)

    cursor.connection.commit()

    stats = {
        "daily_price_summary_rows": daily_count,
        "monthly_cpi_index_rows": cpi_count,
    }
    logger.info("Aggregation complete: %s", stats)
    return stats
