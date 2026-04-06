"""
Schema Drift Detection.

Runs before collection to verify API response shapes match expectations.
Compares current API responses to a stored baseline in raw.schema_baseline.

Checks:
  - KOSTAT: item catalog (codes, names, count)
  - ECOS: response field presence (all 14 expected fields)

On first run, establishes the baseline. On subsequent runs, compares and flags drift.
Drift is logged but does NOT block collection — it's informational.
"""

import json
import logging
from datetime import datetime

import requests

from pipeline.config import (
    DATA_GO_KR_KEY,
    KOSTAT_BASE_URL,
    ECOS_API_KEY,
    ECOS_BASE_URL,
    ECOS_STAT_CODE_CPI,
    ECOS_RETURN_FORMAT,
    ECOS_LANGUAGE,
    REQUEST_TIMEOUT,
)
from pipeline.alerts import send_alert, WARNING, INFO

logger = logging.getLogger(__name__)

# Expected ECOS StatisticSearch fields (from official 개발명세서)
ECOS_EXPECTED_FIELDS = [
    "STAT_CODE", "STAT_NAME", "ITEM_CODE1", "ITEM_NAME1",
    "ITEM_CODE2", "ITEM_NAME2", "ITEM_CODE3", "ITEM_NAME3",
    "ITEM_CODE4", "ITEM_NAME4", "UNIT_NAME", "WGT",
    "TIME", "DATA_VALUE",
]


# ── KOSTAT Schema Check ──────────────────────────────────────────────

def check_kostat_schema(cursor) -> dict:
    """
    Compare current KOSTAT item catalog against stored baseline.
    Detects: new items, removed items, name changes.
    """
    # Fetch current catalog from API
    url = f"{KOSTAT_BASE_URL}/getPriceItemList"
    params = {"serviceKey": DATA_GO_KR_KEY, "numOfRows": "500", "pageNo": "1"}

    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.text)
        items = {}
        for item in root.iter("item"):
            ic = item.findtext("ic") or ""
            in_ = item.findtext("in") or ""
            ed = item.findtext("ed") or ""
            if ic:
                items[ic] = {"name": in_, "ed": ed}
    except Exception as e:
        logger.error("Failed to fetch KOSTAT item catalog: %s", e)
        return {"source": "KOSTAT", "check": "item_catalog", "drift": False,
                "error": str(e)}

    # Get baseline
    cursor.execute("""
        SELECT baseline_value FROM raw.schema_baseline
        WHERE source = 'KOSTAT' AND check_type = 'item_catalog'
        ORDER BY checked_at DESC LIMIT 1
    """)
    row = cursor.fetchone()

    current_codes = set(items.keys())
    result = {"source": "KOSTAT", "check": "item_catalog", "drift": False,
              "total_items": len(items), "details": None}

    if row is None:
        # First run — establish baseline
        _save_baseline(cursor, "KOSTAT", "item_catalog", items)
        result["details"] = f"Baseline established: {len(items)} items"
        logger.info("KOSTAT schema baseline established: %d items", len(items))
    else:
        baseline = row[0]
        baseline_codes = set(baseline.keys())

        added = current_codes - baseline_codes
        removed = baseline_codes - current_codes
        name_changes = []
        for code in current_codes & baseline_codes:
            if items[code]["name"] != baseline[code].get("name"):
                name_changes.append({
                    "code": code,
                    "old": baseline[code].get("name"),
                    "new": items[code]["name"],
                })

        if added or removed or name_changes:
            result["drift"] = True
            details = []
            if added:
                details.append(f"New items: {sorted(added)}")
            if removed:
                details.append(f"Removed items: {sorted(removed)}")
            if name_changes:
                details.append(f"Name changes: {name_changes}")
            result["details"] = "; ".join(details)

            _save_baseline(cursor, "KOSTAT", "item_catalog", items,
                           drift=True, drift_details=result["details"])

            send_alert(WARNING, "KOSTAT Schema Drift Detected", result["details"],
                       {"Added": len(added), "Removed": len(removed),
                        "Name Changes": len(name_changes)})
        else:
            result["details"] = f"No drift detected ({len(items)} items)"
            _save_baseline(cursor, "KOSTAT", "item_catalog", items)

    cursor.connection.commit()
    return result


# ── ECOS Schema Check ────────────────────────────────────────────────

def check_ecos_schema(cursor) -> dict:
    """
    Verify ECOS StatisticSearch returns all 14 expected fields.
    Fetches 1 row and checks field presence.
    """
    url = "/".join([
        ECOS_BASE_URL, "StatisticSearch", ECOS_API_KEY,
        ECOS_RETURN_FORMAT, ECOS_LANGUAGE,
        "1", "1",  # just 1 row
        ECOS_STAT_CODE_CPI, "M", "202401", "202401",
    ])

    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch ECOS sample: %s", e)
        return {"source": "ECOS", "check": "field_presence", "drift": False,
                "error": str(e)}

    result = {"source": "ECOS", "check": "field_presence", "drift": False,
              "details": None}

    if "StatisticSearch" not in data:
        # Might be an error response
        result["drift"] = True
        result["details"] = f"No StatisticSearch key in response: {str(data)[:200]}"
        send_alert(WARNING, "ECOS Schema Drift", result["details"])
        return result

    rows = data["StatisticSearch"].get("row", [])
    if not rows:
        result["details"] = "No rows returned for 202401 — possible data issue"
        return result

    actual_fields = set(rows[0].keys())
    expected = set(ECOS_EXPECTED_FIELDS)
    missing = expected - actual_fields
    extra = actual_fields - expected

    # Get baseline
    cursor.execute("""
        SELECT baseline_value FROM raw.schema_baseline
        WHERE source = 'ECOS' AND check_type = 'field_presence'
        ORDER BY checked_at DESC LIMIT 1
    """)
    baseline_row = cursor.fetchone()

    current_value = {"fields": sorted(actual_fields)}

    if missing or extra:
        result["drift"] = True
        details = []
        if missing:
            details.append(f"Missing fields: {sorted(missing)}")
        if extra:
            details.append(f"New fields: {sorted(extra)}")
        result["details"] = "; ".join(details)

        _save_baseline(cursor, "ECOS", "field_presence", current_value,
                       drift=True, drift_details=result["details"])

        send_alert(WARNING, "ECOS Schema Drift Detected", result["details"],
                   {"Missing": len(missing), "Extra": len(extra)})
    else:
        if baseline_row is None:
            _save_baseline(cursor, "ECOS", "field_presence", current_value)
            result["details"] = f"Baseline established: {len(actual_fields)} fields"
        else:
            _save_baseline(cursor, "ECOS", "field_presence", current_value)
            result["details"] = f"All {len(expected)} expected fields present"

    cursor.connection.commit()
    return result


# ── Run All Schema Checks ────────────────────────────────────────────

def run_schema_checks(cursor) -> list[dict]:
    """Run all schema drift checks and return results."""
    logger.info("Running schema drift detection...")

    results = []

    for check_fn in [check_kostat_schema, check_ecos_schema]:
        try:
            result = check_fn(cursor)
            results.append(result)
            drift_str = "DRIFT" if result.get("drift") else "OK"
            logger.info("[%s] %s/%s: %s",
                        drift_str, result["source"], result["check"],
                        result.get("details", ""))
        except Exception as e:
            logger.error("Schema check %s failed: %s", check_fn.__name__, e)
            results.append({"source": check_fn.__name__, "drift": False,
                            "error": str(e)})

    any_drift = any(r.get("drift") for r in results)
    if not any_drift:
        send_alert(INFO, "Schema Check Passed", "No drift detected in any source")

    return results


# ── Helpers ───────────────────────────────────────────────────────────

def _save_baseline(cursor, source: str, check_type: str, value: dict,
                   drift: bool = False, drift_details: str = None):
    """Save or update schema baseline."""
    cursor.execute("""
        INSERT INTO raw.schema_baseline
            (source, check_type, baseline_value, drift_detected, drift_details)
        VALUES (%s, %s, %s, %s, %s)
    """, (source, check_type, json.dumps(value), drift, drift_details))
