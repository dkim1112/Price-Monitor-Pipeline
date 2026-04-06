"""
KOSTAT Online Price Collection API — Data Collector
통계청_온라인 수집 가격 정보

Endpoints:
  1. getPriceItemList → active item catalog
  2. getPriceInfo     → product-level prices per item + date range

Constraints:
  - XML only (no JSON)
  - startDate >= 20150101, endDate <= D-2
  - Max 30-day range per request
  - Weekly updates with ~2 week lag
  - Max 1000 rows per page
  - Rate limit: 30 TPS
"""

import time
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import requests
import psycopg2.extras

from pipeline.config import (
    DATA_GO_KR_KEY,
    KOSTAT_BASE_URL,
    KOSTAT_PAGE_SIZE,
    KOSTAT_MAX_DATE_RANGE_DAYS,
    KOSTAT_DATA_LAG_DAYS,
    MAX_RETRIES,
    RETRY_BACKOFF_SECONDS,
    REQUEST_TIMEOUT,
)
from pipeline.db import CollectionLog

logger = logging.getLogger(__name__)


# ── XML Parsing ───────────────────────────────────────────────────────

def _parse_xml(text: str) -> dict:
    """Parse KOSTAT XML response into structured dict."""
    root = ET.fromstring(text)
    result_code = root.findtext(".//resultCode") or ""
    result_msg = root.findtext(".//resultMsg") or ""
    total_count = root.findtext(".//totalCount") or "0"

    items = []
    for item in root.iter("item"):
        d = {}
        for child in item:
            d[child.tag] = child.text
        items.append(d)

    return {
        "code": result_code,
        "msg": result_msg,
        "total": int(total_count),
        "items": items,
    }


def _request_with_retry(url: str, params: dict) -> requests.Response:
    """HTTP GET with retry + backoff for transient failures."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp
            if resp.status_code >= 500:
                logger.warning(
                    "Server error %d on attempt %d, retrying...",
                    resp.status_code, attempt + 1
                )
            else:
                return resp  # client errors don't benefit from retry
        except requests.exceptions.RequestException as e:
            logger.warning("Request error on attempt %d: %s", attempt + 1, e)

        if attempt < MAX_RETRIES - 1:
            wait = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
            time.sleep(wait)

    raise RuntimeError(f"Failed after {MAX_RETRIES} attempts: {url}")


# ── Item Catalog ──────────────────────────────────────────────────────

def fetch_active_items() -> list[dict]:
    """
    Fetch the full item catalog and filter to active items only.
    Active items have ed containing '이후' (post-2024-12-19 schema).
    Returns list of dicts with keys: ic, in, ed
    """
    url = f"{KOSTAT_BASE_URL}/getPriceItemList"
    params = {
        "serviceKey": DATA_GO_KR_KEY,
        "numOfRows": "500",
        "pageNo": "1",
    }

    resp = _request_with_retry(url, params)
    result = _parse_xml(resp.text)

    if result["code"] and result["code"] not in ("00", "") and not result["items"]:
        raise RuntimeError(f"getPriceItemList error: [{result['code']}] {result['msg']}")

    all_items = result["items"]
    active = [i for i in all_items if "이후" in (i.get("ed") or "")]

    logger.info(
        "Item catalog: %d total, %d active",
        len(all_items), len(active)
    )
    return active


# ── Price Collection ──────────────────────────────────────────────────

def _find_latest_data_date() -> str:
    """
    Adaptively find the most recent date with available data.
    Starts at D-14 and works backward until data is found (max D-60).
    Returns YYYYMMDD string.
    """
    url = f"{KOSTAT_BASE_URL}/getPriceInfo"
    test_item = "A01101"  # 쌀 — always has data (6-char code from getPriceItemList)

    # Strategy: search 30-day windows moving backward.
    # KOSTAT data is sparse (weekly updates, ~2 week lag), so single-date
    # probes often miss. Using ranges is more reliable.
    for start_offset in [3, 30, 60, 90]:
        end_offset = max(start_offset - 30, 2)  # don't go past D-2
        start = (datetime.now() - timedelta(days=start_offset)).strftime("%Y%m%d")
        end = (datetime.now() - timedelta(days=end_offset)).strftime("%Y%m%d")
        params = {
            "serviceKey": DATA_GO_KR_KEY,
            "itemCode": test_item,
            "startDate": start,
            "endDate": end,
            "numOfRows": "1",
            "pageNo": "1",
        }
        try:
            resp = _request_with_retry(url, params)
            result = _parse_xml(resp.text)
            if result["items"]:
                # Note: successful responses may have code "00" OR no code at all
                actual_date = result["items"][0].get("sd", "")
                if actual_date:
                    # sd is YYYY-MM-DD, convert to YYYYMMDD
                    found = actual_date.replace("-", "")
                    logger.info(
                        "Latest data found: %s (searched %s to %s)",
                        found, start, end
                    )
                    return found
                # Fallback: use end of the range
                logger.info("Data found in range %s-%s", start, end)
                return end
        except Exception as e:
            logger.debug("Probe %s-%s failed: %s", start, end, e)
            continue

    raise RuntimeError("No KOSTAT data found in last 90 days")


def collect_item_prices(
    cursor,
    item_code: str,
    item_name: str,
    start_date: str,
    end_date: str,
    api_call_id: str,
) -> int:
    """
    Fetch all price records for one item in a date range.
    Handles pagination. Inserts directly into raw.kostat_products.
    Returns total records inserted.
    """
    url = f"{KOSTAT_BASE_URL}/getPriceInfo"
    total_inserted = 0
    page = 1

    while True:
        params = {
            "serviceKey": DATA_GO_KR_KEY,
            "itemCode": item_code,
            "startDate": start_date,
            "endDate": end_date,
            "numOfRows": str(KOSTAT_PAGE_SIZE),
            "pageNo": str(page),
        }

        resp = _request_with_retry(url, params)
        result = _parse_xml(resp.text)

        # Successful responses may have code "00" or no code at all.
        # Error code "21" = no data (normal for some items on some dates).
        if result["code"] == "21" and not result["items"]:
            logger.debug("No data for %s on %s-%s", item_code, start_date, end_date)
            return 0
        if result["code"] and result["code"] not in ("00", "") and not result["items"]:
            raise RuntimeError(
                f"getPriceInfo error for {item_code}: [{result['code']}] {result['msg']}"
            )

        if not result["items"]:
            break

        # Batch insert
        rows = []
        for item in result["items"]:
            sd = item.get("sd", "")
            # Parse date: "YYYY-MM-DD" → DATE
            try:
                price_date = datetime.strptime(sd, "%Y-%m-%d").date() if sd else None
            except ValueError:
                price_date = None

            if price_date is None:
                continue

            rows.append((
                item_code,
                item_name,
                item.get("pi"),
                item.get("pn"),
                _safe_int(item.get("sp")),
                _safe_int(item.get("dp")),
                _safe_int(item.get("bp")),
                price_date,
                api_call_id,
            ))

        if rows:
            psycopg2.extras.execute_batch(
                cursor,
                """
                INSERT INTO raw.kostat_products
                    (item_code, item_name, product_id, product_name,
                     sale_price, discount_price, benefit_price,
                     price_date, api_call_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
                page_size=500,
            )
            total_inserted += len(rows)

        # Check if more pages
        if len(result["items"]) < KOSTAT_PAGE_SIZE:
            break
        page += 1

    return total_inserted


def _safe_int(val: str) -> int | None:
    """Convert string to int, handling commas and empty values."""
    if not val:
        return None
    try:
        return int(val.replace(",", ""))
    except ValueError:
        return None


# ── Main Collection Orchestrator ──────────────────────────────────────

def run_collection(
    cursor,
    target_date: str = None,
    start_date: str = None,
    end_date: str = None,
    item_codes: list[str] = None,
):
    """
    Main KOSTAT collection entry point.

    Args:
        cursor: DB cursor (caller manages transaction)
        target_date: YYYYMMDD string. Collects a single date. If None, auto-detects latest.
        start_date: YYYYMMDD string. If provided with end_date, collects a date range.
        end_date: YYYYMMDD string. Used with start_date for range collection.
        item_codes: List of item codes to collect. If None, collects all active items.

    Returns:
        dict with collection stats
    """
    # Determine date range
    if start_date and end_date:
        # Range mode: split into 30-day chunks (API limit)
        logger.info("KOSTAT range collection: %s to %s", start_date, end_date)
        date_ranges = _split_date_range(start_date, end_date)
    else:
        # Single date mode
        if target_date is None:
            target_date = _find_latest_data_date()
        logger.info("KOSTAT collection target date: %s", target_date)
        date_ranges = [(target_date, target_date)]

    # Get item list
    active_items = fetch_active_items()
    if item_codes:
        active_items = [i for i in active_items if i["ic"] in item_codes]
        logger.info("Filtered to %d items", len(active_items))

    # Create collection log
    log = CollectionLog(
        source="KOSTAT",
        endpoint="getPriceInfo",
        params={
            "date_ranges": [(s, e) for s, e in date_ranges],
            "item_count": len(active_items),
        },
    )
    log.start(cursor)
    cursor.connection.commit()

    total_records = 0
    items_with_data = 0
    items_without_data = 0
    errors = []

    for chunk_idx, (chunk_start, chunk_end) in enumerate(date_ranges):
        if len(date_ranges) > 1:
            logger.info(
                "Date chunk %d/%d: %s to %s",
                chunk_idx + 1, len(date_ranges), chunk_start, chunk_end
            )

        for i, item in enumerate(active_items):
            ic = item["ic"]
            name = item.get("in", "")

            try:
                count = collect_item_prices(
                    cursor, ic, name,
                    start_date=chunk_start,
                    end_date=chunk_end,
                    api_call_id=str(log.id),
                )
                total_records += count
                if count > 0:
                    items_with_data += 1
                else:
                    items_without_data += 1

                # Commit periodically (every 10 items)
                if (i + 1) % 10 == 0:
                    cursor.connection.commit()
                    logger.info(
                        "Progress: chunk %d/%d, %d/%d items, %d records so far",
                        chunk_idx + 1, len(date_ranges),
                        i + 1, len(active_items), total_records
                    )

            except Exception as e:
                logger.error("Error collecting %s (%s): %s", ic, name, e)
                errors.append(f"{ic}: {e}")
                cursor.connection.rollback()
                log.start(cursor)
                cursor.connection.commit()

    # Final log update
    if errors:
        log.fail(cursor, error=f"{len(errors)} items failed: {'; '.join(errors[:5])}")
    else:
        log.succeed(cursor, records_fetched=total_records)
    cursor.connection.commit()

    stats = {
        "date_ranges": [(s, e) for s, e in date_ranges],
        "items_total": len(active_items),
        "items_with_data": items_with_data,
        "items_without_data": items_without_data,
        "total_records": total_records,
        "errors": len(errors),
    }
    logger.info("KOSTAT collection complete: %s", stats)
    return stats


def _split_date_range(start: str, end: str) -> list[tuple[str, str]]:
    """
    Split a date range into 30-day chunks (KOSTAT API limit).
    Input/output format: YYYYMMDD strings.
    """
    chunks = []
    dt_start = datetime.strptime(start, "%Y%m%d")
    dt_end = datetime.strptime(end, "%Y%m%d")

    current = dt_start
    while current <= dt_end:
        chunk_end = min(current + timedelta(days=29), dt_end)
        chunks.append((current.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")))
        current = chunk_end + timedelta(days=1)

    return chunks
