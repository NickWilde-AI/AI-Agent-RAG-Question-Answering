#!/usr/bin/env bash
# 评测数据闭环：候选生成 -> 过滤 -> 版本化 -> 质量回归
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PAGES_JSON="${1:-data/demo_pages.json}"
OUT_DIR="${2:-data/eval_sets}"

python scripts/gen_eval_candidates.py --pages "$PAGES_JSON" --output "$OUT_DIR/_candidates.jsonl"
python scripts/filter_eval_queries.py --input "$OUT_DIR/_candidates.jsonl" --keep-output "$OUT_DIR/_filtered.jsonl" --drop-output "$OUT_DIR/_dropped.jsonl"
VERSION="$(python scripts/version_eval_dataset.py --input "$OUT_DIR/_filtered.jsonl" --root "$OUT_DIR" --print-version-only)"
echo "candidate dataset version: $VERSION"
echo "注意：自动生成内容仍是候选，不是人工金标。正式金标请使用 build_gold_candidates.py + gold_review_server.py + export_gold_dataset.py。"
