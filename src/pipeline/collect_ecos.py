"""
Bank of Korea ECOS API — Data Collector
한국은행 경제통계시스템

Endpoints used:
  1. StatisticSearch  → CPI index values (→ raw.ecos_indices)
  2. StatisticItemList → item catalog with weights (for enrichment)

Error codes (from official 개발명세서):
  100: Invalid auth key → abort
  200: No data → skip (normal)
  400: Timeout → retry with smaller range
  602: Rate limit exceeded → exponential backoff
"""

import time
import logging
from datetime import datetime

import requests
import psycopg2.extras

from pipeline.config import (
    ECOS_API_KEY,
    ECOS_BASE_URL,
    ECOS_STAT_CODE_CPI,
    ECOS_RETURN_FORMAT,
    ECOS_LANGUAGE,
    ECOS_PAGE_SIZE,
    MAX_RETRIES,
    RETRY_BACKOFF_SECONDS,
    REQUEST_TIMEOUT,
)
from pipeline.db import CollectionLog

logger = logging.getLogger(__name__)


# ── URL Builder ───────────────────────────────────────────────────────

def _ecos_url(service: str, start: int, end: int, *params) -> str:
    """
    Build ECOS REST URL.
    Pattern: {base}/{service}/{key}/{format}/{lang}/{start}/{end}/{...params}
    """
    parts = [
        ECOS_BASE_URL, service, ECOS_API_KEY,
        ECOS_RETURN_FORMAT, ECOS_LANGUAGE,
        str(start), str(end),
    ]
    parts.extend(str(p) for p in params)
    return "/".join(parts)


def _request_with_retry(url: str) -> dict:
    """
    HTTP GET with retry, backoff, and ECOS-specific error handling.
    Returns parsed JSON response.
    """
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            data = resp.json()

            # Check for ECOS-specific error codes
            if "RESULT" in data:
                code = data["RESULT"].get("CODE", "")
                msg = data["RESULT"].get("MESSAGE", "")

                if "100" in code:
                    raise RuntimeError(f"ECOS auth error: {msg}")
                if "200" in code:
                    # No data — not an error, return empty
                    return {"rows": [], "total": 0}
                if "602" in code:
                    # Rate limit — must back off
                    wait = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)] * 2
                    logger.warning("ECOS rate limit hit, waiting %ds...", wait)
                    time.sleep(wait)
                    continue
                if "400" in code or "500" in code or "600" in code:
                    logger.warning("ECOS error %s: %s, retrying...", code, msg)
                    time.sleep(RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                    continue

            return data

        except requests.exceptions.RequestException as e:
            logger.warning("Request error on attempt %d: %s", attempt + 1, e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])

    raise RuntimeError(f"Failed after {MAX_RETRIES} attempts: {url}")


# ── StatisticSearch ───────────────────────────────────────────────────

def fetch_cpi_data(
    start_period: str,
    end_period: str,
    stat_code: str = ECOS_STAT_CODE_CPI,
    cycle: str = "M",
) -> list[dict]:
    """
    Fetch CPI index data from ECOS StatisticSearch.
    Handles pagination automatically.

    Args:
        start_period: YYYYMM format (e.g., "202401")
        end_period: YYYYMM format (e.g., "202412")
        stat_code: ECOS stat table code
        cycle: A/S/Q/M/SM/D

    Returns:
        List of row dicts with all 14 fields
    """
    all_rows = []
    page_start = 1

    while True:
        page_end = page_start + ECOS_PAGE_SIZE - 1
        url = _ecos_url(
            "StatisticSearch", page_start, page_end,
            stat_code, cycle, start_period, end_period
        )

        data = _request_with_retry(url)

        if "StatisticSearch" not in data:
            # Could be empty result from error handler
            if data.get("rows") == []:
                break
            logger.warning("Unexpected ECOS response: %s", str(data)[:200])
            break

        rows = data["StatisticSearch"].get("row", [])
        total = int(data["StatisticSearch"].get("list_total_count", 0))
        all_rows.extend(rows)

        logger.debug(
            "StatisticSearch page %d-%d: got %d rows (total: %d)",
            page_start, page_end, len(rows), total
        )

        if page_end >= total:
            break
        page_start = page_end + 1

    logger.info(
        "Fetched %d CPI rows for %s to %s",
        len(all_rows), start_period, end_period
    )
    return all_rows


# ── Database Insert ───────────────────────────────────────────────────

def insert_ecos_rows(cursor, rows: list[dict], api_call_id: str) -> int:
    """
    Insert ECOS rows into raw.ecos_indices.
    Uses ON CONFLICT to skip duplicates (same stat+item+period+call).
    Returns count of rows inserted.
    """
    if not rows:
        return 0

    insert_rows = []
    for row in rows:
        wgt = row.get("WGT")
        wgt_val = None
        if wgt and wgt != "null":
            try:
                wgt_val = float(wgt)
            except ValueError:
                wgt_val = None

        data_val = row.get("DATA_VALUE")
        data_val_num = None
        if data_val and data_val != "-":
            try:
                data_val_num = float(data_val)
            except ValueError:
                data_val_num = None

        insert_rows.append((
            row.get("STAT_CODE"),
            row.get("STAT_NAME"),
            row.get("ITEM_CODE1"),
            row.get("ITEM_NAME1"),
            row.get("ITEM_CODE2"),
            row.get("ITEM_NAME2"),
            row.get("ITEM_CODE3"),
            row.get("ITEM_NAME3"),
            row.get("ITEM_CODE4"),
            row.get("ITEM_NAME4"),
            row.get("UNIT_NAME"),
            wgt_val,
            row.get("TIME"),
            data_val_num,
            api_call_id,
        ))

    psycopg2.extras.execute_batch(
        cursor,
        """
        INSERT INTO raw.ecos_indices
            (stat_code, stat_name, item_code1, item_name1,
             item_code2, item_name2, item_code3, item_name3,
             item_code4, item_name4, unit_name, weight,
             time_period, data_value, api_call_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (stat_code, item_code1, time_period, api_call_id) DO NOTHING
        """,
        insert_rows,
        page_size=500,
    )

    return len(insert_rows)


# ── Main Collection Orchestrator ──────────────────────────────────────

def run_collection(
    cursor,
    start_period: str = None,
    end_period: str = None,
    stat_code: str = ECOS_STAT_CODE_CPI,
):
    """
    Main ECOS collection entry point.

    Args:
        cursor: DB cursor
        start_period: YYYYMM. If None, defaults to current month.
        end_period: YYYYMM. If None, defaults to current month.
        stat_code: Which stat table to fetch.

    Returns:
        dict with collection stats
    """
    now = datetime.now()
    if start_period is None:
        start_period = now.strftime("%Y%m")
    if end_period is None:
        end_period = now.strftime("%Y%m")

    logger.info(
        "ECOS collection: stat=%s, period=%s to %s",
        stat_code, start_period, end_period
    )

    # Create collection log
    log = CollectionLog(
        source="ECOS",
        endpoint="StatisticSearch",
        params={
            "stat_code": stat_code,
            "start_period": start_period,
            "end_period": end_period,
        },
    )
    log.start(cursor)
    cursor.connection.commit()

    try:
        rows = fetch_cpi_data(start_period, end_period, stat_code)
        inserted = insert_ecos_rows(cursor, rows, str(log.id))
        log.succeed(cursor, records_fetched=inserted)
        cursor.connection.commit()

        stats = {
            "stat_code": stat_code,
            "start_period": start_period,
            "end_period": end_period,
            "rows_fetched": len(rows),
            "rows_inserted": inserted,
        }
        logger.info("ECOS collection complete: %s", stats)
        return stats

    except Exception as e:
        log.fail(cursor, error=str(e))
        cursor.connection.commit()
        raise
