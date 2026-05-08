#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

missing=()

if ! command -v python3 >/dev/null 2>&1; then
  missing+=("python3")
else
  if ! python3 -m venv --help >/dev/null 2>&1; then
    missing+=("python3-venv")
  fi
fi

if ! command -v git >/dev/null 2>&1; then
  missing+=("git")
fi

if [ "${#missing[@]}" -gt 0 ]; then
  echo "Missing required packages: ${missing[*]}"
  echo
  echo "On Ubuntu 22.04 / 24.04, install them with:"
  echo "  sudo apt update"
  echo "  sudo apt install -y python3 python3-venv git"
  exit 1
fi

if [ ! -f "requirements.txt" ]; then
  echo "requirements.txt not found in ${PROJECT_ROOT}"
  exit 1
fi

echo "Project root: ${PROJECT_ROOT}"
echo "Creating or reusing .venv ..."
python3 -m venv .venv

echo "Installing Python dependencies ..."
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements.txt

echo "Ensuring helper scripts are executable ..."
chmod +x scripts/deploy_linux.sh scripts/run_streamlit.sh scripts/update_signal.sh 2>/dev/null || true

if [ -f "config/current_position.yaml" ]; then
  echo "Keeping existing config/current_position.yaml unchanged."
else
  echo "config/current_position.yaml not found. No position file will be created or overwritten."
fi

echo "Running qa-check ..."
.venv/bin/python main.py qa-check

echo "Running compare-signal ..."
.venv/bin/python main.py compare-signal

echo "Linux deployment preparation complete."
