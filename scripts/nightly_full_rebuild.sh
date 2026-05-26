#!/usr/bin/env bash
# 可选：每日全量重建保险任务（与增量建库互补）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG="data/logs/nightly_rebuild_$(date +%Y%m%d).log"
mkdir -p data/logs
echo "[$(date -Iseconds)] nightly full rebuild start" | tee -a "$LOG"
python scripts/build_index_incremental.py --full 2>&1 | tee -a "$LOG"
echo "[$(date -Iseconds)] done" | tee -a "$LOG"
