#!/usr/bin/env bash
# 删除本地页级向量索引产物（保留 user_docs/ 原始文件）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

rm -f data/user_pages.json data/index_manifest.json
rm -rf kb_pages/*

echo "已清空：data/user_pages.json、data/index_manifest.json、kb_pages/*"
echo "未删除：user_docs/。请重新执行 scripts/build_index_incremental.py 再重启 API。"
