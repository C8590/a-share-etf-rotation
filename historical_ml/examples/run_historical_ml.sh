#!/usr/bin/env bash
set -euo pipefail

python -m historical_ml.cli run-all \
  --prices data/etf_daily.csv \
  --start 2024-09-24 \
  --end 2026-05-19 \
  --out artifacts/historical_ml \
  --format csv
