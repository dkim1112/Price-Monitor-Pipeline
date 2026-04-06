#!/usr/bin/env python3
"""
Price Monitor Pipeline — Main Entry Point
==========================================

Usage:
  python main.py collect-kostat              # collect latest KOSTAT data
  python main.py collect-kostat --date 20260201  # collect specific date
  python main.py collect-kostat --start 20250101 --end 20260228  # collect date range
  python main.py collect-ecos                # collect current month CPI
  python main.py collect-ecos --start 202401 --end 202412  # collect date range
  python main.py aggregate                   # run all aggregation
  python main.py validate                    # run data quality checks
  python main.py schema-check               # run schema drift detection
  python main.py run-all                     # full pipeline: schema check → collect → validate → aggregate → alert
  python main.py status                      # show recent collection logs

Environment variables:
  ECOS_API_KEY     — Bank of Korea API key
  DATA_GO_KR_KEY   — data.go.kr decoding key
  SLACK_WEBHOOK_URL — Slack webhook for alerts (optional)
  DB_HOST          — PostgreSQL host (default: localhost)
  DB_PORT          — PostgreSQL port (default: 5432)
"""

import sys
import os
import argparse
import logging
from datetime import datetime

from pipeline.db import get_cursor
from pipeline.collect_kostat import run_collection as run_kostat
from pipeline.collect_ecos import run_collection as run_ecos
from pipeline.aggregate import run_aggregation
from pipeline.quality import run_all_checks
from pipeline.schema_check import run_schema_checks
from pipeline.alerts import send_alert, format_collection_summary, INFO, CRITICAL
from pipeline.config import ECOS_API_KEY, DATA_GO_KR_KEY


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_collect_kostat(args):
    """Collect KOSTAT product prices."""
    if not DATA_GO_KR_KEY:
        print("ERROR: DATA_GO_KR_KEY environment variable not set")
        sys.exit(1)

    with get_cursor(commit=False) as cur:
        stats = run_kostat(
            cur,
            target_date=args.date,
            start_date=args.start,
            end_date=args.end,
            item_codes=args.items.split(",") if args.items else None,
        )
    print(f"\nKOSTAT collection done: {stats}")


def cmd_collect_ecos(args):
    """Collect ECOS CPI data."""
    if not ECOS_API_KEY:
        print("ERROR: ECOS_API_KEY environment variable not set")
        sys.exit(1)

    with get_cursor(commit=False) as cur:
        stats = run_ecos(
            cur,
            start_period=args.start,
            end_period=args.end,
            stat_code=args.stat_code,
        )
    print(f"\nECOS collection done: {stats}")


def cmd_aggregate(args):
    """Run aggregation (raw → mart)."""
    with get_cursor(commit=False) as cur:
        stats = run_aggregation(
            cur,
            price_date=args.date,
            year_month=args.month,
        )
    print(f"\nAggregation done: {stats}")


def cmd_validate(args):
    """Run data quality checks."""
    with get_cursor(commit=False) as cur:
        results = run_all_checks(cur)

    print(f"\n{'Check':<25} {'Status':<8} {'Value':>10} {'Threshold':>10}")
    print("-" * 60)
    for r in results:
        val = str(r["value"]) if r["value"] is not None else "-"
        thr = str(r["threshold"]) if r["threshold"] is not None else "-"
        print(f"{r['check']:<25} {r['status']:<8} {val:>10} {thr:>10}")
        if args.verbose:
            print(f"  {r['message']}")

    fails = sum(1 for r in results if r["status"] == "FAIL")
    warns = sum(1 for r in results if r["status"] == "WARN")
    print(f"\nSummary: {fails} failures, {warns} warnings, "
          f"{len(results) - fails - warns} passed")


def cmd_schema_check(args):
    """Run schema drift detection."""
    with get_cursor(commit=False) as cur:
        results = run_schema_checks(cur)

    for r in results:
        drift_str = "DRIFT DETECTED" if r.get("drift") else "OK"
        print(f"[{drift_str}] {r.get('source', '?')}/{r.get('check', '?')}: "
              f"{r.get('details', r.get('error', ''))}")


def cmd_run_all(args):
    """Full pipeline run: schema check → collect → validate → aggregate → alert."""
    print(f"{'='*60}")
    print(f"Full pipeline run — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    # Step 1: Schema check (pre-flight)
    print("\n[1/5] Running schema drift detection...")
    with get_cursor(commit=False) as cur:
        schema_results = run_schema_checks(cur)
    any_drift = any(r.get("drift") for r in schema_results)
    print(f"  → {'DRIFT detected — check alerts' if any_drift else 'No drift'}")

    # Step 2: KOSTAT collection
    kostat_stats = None
    if DATA_GO_KR_KEY:
        print("\n[2/5] Collecting KOSTAT prices...")
        with get_cursor(commit=False) as cur:
            kostat_stats = run_kostat(cur)
        print(f"  → {kostat_stats.get('total_records', 0)} records from "
              f"{kostat_stats.get('items_with_data', 0)} items")
    else:
        print("\n[2/5] Skipping KOSTAT (DATA_GO_KR_KEY not set)")

    # Step 3: ECOS collection
    ecos_stats = None
    if ECOS_API_KEY:
        print("\n[3/5] Collecting ECOS CPI...")
        with get_cursor(commit=False) as cur:
            ecos_stats = run_ecos(cur)
        print(f"  → {ecos_stats.get('rows_fetched', 0)} rows fetched")
    else:
        print("\n[3/5] Skipping ECOS (ECOS_API_KEY not set)")

    # Step 4: Quality validation
    print("\n[4/5] Running quality checks...")
    with get_cursor(commit=False) as cur:
        quality_results = run_all_checks(cur)
    fails = sum(1 for r in quality_results if r["status"] == "FAIL")
    warns = sum(1 for r in quality_results if r["status"] == "WARN")
    print(f"  → {fails} failures, {warns} warnings")

    # Step 5: Aggregation
    print("\n[5/5] Running aggregation...")
    with get_cursor(commit=False) as cur:
        agg_stats = run_aggregation(cur)
    print(f"  → {agg_stats}")

    # Summary alert
    summary_parts = []
    if kostat_stats:
        summary_parts.append(format_collection_summary("KOSTAT", kostat_stats))
    if ecos_stats:
        summary_parts.append(format_collection_summary("ECOS", ecos_stats))
    summary_parts.append(f"Quality: {fails} failures, {warns} warnings")
    summary_parts.append(f"Aggregation: {agg_stats}")

    send_alert(INFO, "Pipeline Run Complete", "\n".join(summary_parts))

    print(f"\n{'='*60}")
    print("Pipeline run complete.")
    print(f"{'='*60}")


def cmd_dashboard(args):
    """Launch the Streamlit dashboard."""
    import subprocess
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.py")
    cmd = ["streamlit", "run", dashboard_path]
    if args.port:
        cmd.extend(["--server.port", str(args.port)])
    subprocess.run(cmd)


def cmd_status(args):
    """Show recent collection logs."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT source, endpoint, status, records_fetched,
                   started_at, finished_at, error_message
            FROM raw.collection_log
            ORDER BY started_at DESC
            LIMIT %s
        """, (args.limit,))

        rows = cur.fetchall()
        if not rows:
            print("No collection logs found.")
            return

        print(f"\n{'Source':<10} {'Endpoint':<20} {'Status':<10} {'Records':>8} {'Started':>20} {'Duration':>10}")
        print("-" * 85)
        for row in rows:
            source, endpoint, status, records, started, finished, error = row
            duration = ""
            if finished and started:
                secs = (finished - started).total_seconds()
                duration = f"{secs:.1f}s"
            print(f"{source:<10} {endpoint:<20} {status:<10} {records or 0:>8} {str(started)[:19]:>20} {duration:>10}")
            if error and args.verbose:
                print(f"  ERROR: {error[:100]}")


def main():
    parser = argparse.ArgumentParser(
        description="Price Monitor Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Common verbose flag for all subcommands
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    # collect-kostat
    p_kostat = subparsers.add_parser("collect-kostat", parents=[common], help="Collect KOSTAT prices")
    p_kostat.add_argument("--date", help="Target date YYYYMMDD (auto-detects if omitted)")
    p_kostat.add_argument("--start", help="Start date YYYYMMDD (use with --end for range)")
    p_kostat.add_argument("--end", help="End date YYYYMMDD (use with --start for range)")
    p_kostat.add_argument("--items", help="Comma-separated item codes to collect")
    p_kostat.set_defaults(func=cmd_collect_kostat)

    # collect-ecos
    p_ecos = subparsers.add_parser("collect-ecos", parents=[common], help="Collect ECOS CPI data")
    p_ecos.add_argument("--start", help="Start period YYYYMM (default: current month)")
    p_ecos.add_argument("--end", help="End period YYYYMM (default: current month)")
    p_ecos.add_argument("--stat-code", default="901Y009", help="ECOS stat code")
    p_ecos.set_defaults(func=cmd_collect_ecos)

    # aggregate
    p_agg = subparsers.add_parser("aggregate", parents=[common], help="Run aggregation (raw → mart)")
    p_agg.add_argument("--date", help="Specific price date YYYY-MM-DD")
    p_agg.add_argument("--month", help="Specific month YYYYMM")
    p_agg.set_defaults(func=cmd_aggregate)

    # validate
    p_val = subparsers.add_parser("validate", parents=[common], help="Run data quality checks")
    p_val.set_defaults(func=cmd_validate)

    # schema-check
    p_schema = subparsers.add_parser("schema-check", parents=[common], help="Schema drift detection")
    p_schema.set_defaults(func=cmd_schema_check)

    # run-all
    p_all = subparsers.add_parser("run-all", parents=[common], help="Full pipeline run")
    p_all.set_defaults(func=cmd_run_all)

    # dashboard
    p_dash = subparsers.add_parser("dashboard", parents=[common], help="Launch Streamlit dashboard")
    p_dash.add_argument("--port", type=int, default=None, help="Server port (default: 8501)")
    p_dash.set_defaults(func=cmd_dashboard)

    # status
    p_status = subparsers.add_parser("status", parents=[common], help="Show collection logs")
    p_status.add_argument("-n", "--limit", type=int, default=10, help="Number of logs")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
