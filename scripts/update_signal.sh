#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

mkdir -p logs
LOG_FILE="logs/update_signal.log"

{
  echo "============================================================"
  echo "update_signal started at $(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "Project root: ${PROJECT_ROOT}"

  .venv/bin/python main.py update-data
  .venv/bin/python main.py qa-check
  .venv/bin/python main.py compare-signal

  echo "update_signal finished at $(date '+%Y-%m-%d %H:%M:%S %z')"
} >> "${LOG_FILE}" 2>&1
