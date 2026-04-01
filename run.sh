#!/bin/bash
# Wrapper for launchd — aktiverer venv og kjører agenten
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

exec >> "$LOG_DIR/agent.log" 2>&1

echo ""
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="

cd "$SCRIPT_DIR"
source "$SCRIPT_DIR/.venv/bin/activate"
python3 "$SCRIPT_DIR/agent.py"
