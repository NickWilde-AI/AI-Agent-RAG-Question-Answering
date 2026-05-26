#!/usr/bin/env python3
"""
评测集版本化打包：
- 输入过滤后的 JSONL
- 输出 data/eval_sets/vYYYYMMDD-HHMMSS/*
- 生成 meta.json
- 导出 run_quality_eval 可用测试集格式
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List


def read_jsonl(path: Path) -> List[Dict]:
    rows: List[Dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def to_quality_cases(rows: List[Dict]) -> List[Dict]:
    out: List[Dict] = []
    for i, row in enumerate(rows, start=1):
        out.append(
            {
                "id": row.get("id") or f"AUTO{i:04d}",
                "category": row.get("category", "auto_eval"),
                "query": row.get("query", ""),
                "expected_behavior": "命中 gold_pages 并给出可证答案",
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Version eval dataset")
    ap.add_argument("--input", default="data/eval_candidates_kept.jsonl")
    ap.add_argument("--root", default="data/eval_sets")
    ap.add_argument("--tag", default="", help="版本后缀")
    ap.add_argument("--generator-model", default="")
    ap.add_argument("--filter-strategy", default="A/B filter")
    ap.add_argument("--print-version-only", action="store_true", help="仅输出版本号，便于 shell 脚本引用")
    args = ap.parse_args()

    rows = read_jsonl(Path(args.input))
    ts = datetime.now().strftime("v%Y%m%d-%H%M%S")
    version = f"{ts}-{args.tag}" if args.tag else ts
    out_dir = Path(args.root) / version
    out_dir.mkdir(parents=True, exist_ok=True)

    categories = Counter(str(r.get("category", "unknown")) for r in rows)
    (out_dir / "eval_candidates_kept.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    quality_cases = to_quality_cases(rows)
    (out_dir / "rag_quality_testset.json").write_text(
        json.dumps(quality_cases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    meta = {
        "version": version,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "sample_count": len(rows),
        "categories": dict(categories),
        "generator_model": args.generator_model,
        "filter_strategy": args.filter_strategy,
        "source_file": str(Path(args.input).resolve()),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # 输出 latest 软链接文件（复制）
    latest = Path("data/rag_quality_testset.generated.json")
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(quality_cases, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.print_version_only:
        print(version)
    else:
        print(f"version={version}")
        print(f"out={out_dir}")
        print(f"latest={latest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

