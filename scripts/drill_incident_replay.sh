#!/usr/bin/env bash
# 故障演练：跑质量评测 -> 回放失败用例
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
OUT_DIR="${OUT_DIR:-logs/incident_replay}"
TS="$(date +%Y%m%d-%H%M%S)"
REPORT="$OUT_DIR/quality_report_$TS.json"

mkdir -p "$OUT_DIR"
python scripts/run_quality_eval.py --base "$BASE_URL" --output "$REPORT" || true
python scripts/replay_failed_cases.py --report "$REPORT" --base "$BASE_URL" --output-dir "$OUT_DIR"

echo "quality_report=$REPORT"
