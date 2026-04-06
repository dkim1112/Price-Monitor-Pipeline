"""
Alerting system — Slack webhook + file logging.

Graceful degradation: if SLACK_WEBHOOK_URL is not set,
alerts are only logged (no external calls).
"""

import json
import logging
from datetime import datetime

import requests

from pipeline.config import SLACK_WEBHOOK_URL, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

# Alert severity levels
INFO = "INFO"
WARNING = "WARNING"
CRITICAL = "CRITICAL"

# Slack color mapping
_COLORS = {
    INFO: "#36a64f",       # green
    WARNING: "#ff9900",    # orange
    CRITICAL: "#ff0000",   # red
}

# Emoji mapping
_EMOJI = {
    INFO: ":white_check_mark:",
    WARNING: ":warning:",
    CRITICAL: ":rotating_light:",
}


def send_alert(level: str, title: str, message: str, fields: dict = None):
    """
    Send an alert via Slack webhook and log it.

    Args:
        level: INFO, WARNING, or CRITICAL
        title: Short summary (e.g., "KOSTAT Collection Failed")
        message: Detailed description
        fields: Optional dict of key-value pairs for structured data
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Always log locally
    log_fn = {INFO: logger.info, WARNING: logger.warning, CRITICAL: logger.error}
    log_fn.get(level, logger.info)(
        "[ALERT %s] %s — %s", level, title, message
    )

    # Send to Slack if configured
    if SLACK_WEBHOOK_URL:
        _send_slack(level, title, message, fields, timestamp)
    else:
        logger.debug("Slack webhook not configured, alert logged only")


def _send_slack(level: str, title: str, message: str, fields: dict, timestamp: str):
    """Format and send a Slack webhook message."""
    # Build attachment fields
    slack_fields = [
        {"title": "Severity", "value": f"{_EMOJI.get(level, '')} {level}", "short": True},
        {"title": "Time", "value": timestamp, "short": True},
    ]
    if fields:
        for k, v in fields.items():
            slack_fields.append({"title": k, "value": str(v), "short": True})

    payload = {
        "attachments": [
            {
                "color": _COLORS.get(level, "#cccccc"),
                "title": f"Price Monitor: {title}",
                "text": message,
                "fields": slack_fields,
                "footer": "Price Monitor Pipeline",
                "ts": int(datetime.now().timestamp()),
            }
        ]
    }

    try:
        resp = requests.post(
            SLACK_WEBHOOK_URL,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.error("Slack webhook returned %d: %s", resp.status_code, resp.text)
    except requests.exceptions.RequestException as e:
        logger.error("Failed to send Slack alert: %s", e)


def format_collection_summary(source: str, stats: dict) -> str:
    """Format a collection stats dict into a readable message."""
    lines = [f"*{source} Collection Summary*"]
    for k, v in stats.items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def format_quality_report(results: list[dict]) -> str:
    """
    Format quality check results into a readable message.

    Args:
        results: List of dicts with keys: check, status, value, threshold, message
    """
    if not results:
        return "All quality checks passed."

    lines = ["*Quality Check Results*"]
    for r in results:
        icon = {"PASS": "+", "WARN": "!", "FAIL": "X"}.get(r["status"], "?")
        lines.append(f"  [{icon}] {r['check']}: {r.get('message', '')}")
        if r.get("value") is not None and r.get("threshold") is not None:
            lines.append(f"      value={r['value']}, threshold={r['threshold']}")

    fail_count = sum(1 for r in results if r["status"] == "FAIL")
    warn_count = sum(1 for r in results if r["status"] == "WARN")
    lines.append(f"\nTotal: {fail_count} failures, {warn_count} warnings")
    return "\n".join(lines)
