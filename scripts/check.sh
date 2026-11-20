#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python3 -m unittest discover -s tests -v
if [[ "${1:-}" == "--live" ]]; then
  mkdir -p reports
  python3 -m llm_failure_lab.ollama_smoke
  python3 -m llm_failure_lab.live_f01 --report reports/f01-live.json
  python3 -m llm_failure_lab.live_f02 --report reports/f02-live.json
  python3 -m llm_failure_lab.live_f03 --report reports/f03-live.json
  python3 -m llm_failure_lab.live_f04_f05 --report reports/f04-f05-live.json
elif [[ $# -gt 0 ]]; then
  echo "Usage: bash scripts/check.sh [--live]" >&2
  exit 2
fi
