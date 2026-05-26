#!/usr/bin/env bash
# 一键产出“视觉链路 vs OCR 基线”对照报告
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
TESTSET="${TESTSET:-data/rag_quality_testset.json}"
PAGES="${PAGES:-data/user_pages.json}"
OUT_DIR="${OUT_DIR:-logs/ocr_vs_visual}"
TS="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$OUT_DIR"
VISUAL_OUT="$OUT_DIR/visual_quality_$TS.json"
OCR_OUT="$OUT_DIR/ocr_baseline_$TS.json"
COMPARE_OUT="$OUT_DIR/compare_$TS.json"
COMPARE_MD="$OUT_DIR/compare_$TS.md"

python scripts/run_quality_eval.py --base "$BASE_URL" --testset "$TESTSET" --output "$VISUAL_OUT"
python scripts/run_ocr_baseline_eval.py --pages "$PAGES" --testset "$TESTSET" --output "$OCR_OUT"
python scripts/compare_ocr_vs_visual.py \
  --visual-report "$VISUAL_OUT" \
  --ocr-report "$OCR_OUT" \
  --output "$COMPARE_OUT" \
  --output-md "$COMPARE_MD"

echo "visual_report=$VISUAL_OUT"
echo "ocr_report=$OCR_OUT"
echo "compare_json=$COMPARE_OUT"
echo "compare_md=$COMPARE_MD"
