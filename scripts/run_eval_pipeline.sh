#!/usr/bin/env bash
# 评测数据闭环：候选生成 -> 过滤 -> 版本化 -> 质量回归
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PAGES_JSON="${1:-data/demo_pages.json}"
OUT_DIR="${2:-data/eval_sets}"

python scripts/gen_eval_candidates.py --pages "$PAGES_JSON" --output "$OUT_DIR/_candidates.jsonl"
python scripts/filter_eval_queries.py --input "$OUT_DIR/_candidates.jsonl" --output "$OUT_DIR/_filtered.jsonl"
VERSION="$(python scripts/version_eval_dataset.py --input "$OUT_DIR/_filtered.jsonl" --root "$OUT_DIR" --print-version-only)"
echo "eval dataset version: $VERSION"
python scripts/run_quality_eval.py || true
