#!/bin/bash
# Price Monitor Pipeline — Cron wrapper
# Ensures correct virtualenv, env vars, and working directory.
#
# Usage (added to crontab):
#   0 9 * * 1   /path/to/scripts/cron_collect.sh kostat   # Mondays 9am
#   0 9 5 * *   /path/to/scripts/cron_collect.sh ecos     # 5th of month 9am
#   30 9 * * 1  /path/to/scripts/cron_collect.sh aggregate # Mondays 9:30am

set -euo pipefail

# ── Resolve project root (one level up from scripts/) ──
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SRC_DIR="$PROJECT_DIR/src"
LOG_DIR="$PROJECT_DIR/logs"

mkdir -p "$LOG_DIR"

# ── Load environment variables ──
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

# ── Activate virtualenv ──
if [ -f "$PROJECT_DIR/venv/bin/activate" ]; then
    source "$PROJECT_DIR/venv/bin/activate"
fi

# ── Timestamp for log file ──
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# ── Run the requested command ──
case "${1:-}" in
    kostat)
        echo "[$TIMESTAMP] Starting KOSTAT collection..."
        cd "$SRC_DIR" && python main.py collect-kostat \
            >> "$LOG_DIR/kostat_${TIMESTAMP}.log" 2>&1
        ;;
    ecos)
        echo "[$TIMESTAMP] Starting ECOS collection..."
        cd "$SRC_DIR" && python main.py collect-ecos \
            >> "$LOG_DIR/ecos_${TIMESTAMP}.log" 2>&1
        ;;
    aggregate)
        echo "[$TIMESTAMP] Starting aggregation..."
        cd "$SRC_DIR" && python main.py aggregate \
            >> "$LOG_DIR/aggregate_${TIMESTAMP}.log" 2>&1
        ;;
    run-all)
        echo "[$TIMESTAMP] Starting full pipeline..."
        cd "$SRC_DIR" && python main.py run-all \
            >> "$LOG_DIR/run_all_${TIMESTAMP}.log" 2>&1
        ;;
    *)
        echo "Usage: $0 {kostat|ecos|aggregate|run-all}"
        exit 1
        ;;
esac

echo "[$TIMESTAMP] Done. Exit code: $?"
