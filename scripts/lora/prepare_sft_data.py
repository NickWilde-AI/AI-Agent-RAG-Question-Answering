#!/usr/bin/env python3
"""
从评测/样本数据转换为 LoRA SFT 训练集。
输出 JSONL：{"image_paths":[...], "query":"", "answer":"", "task_type":""}
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepare SFT dataset from quality/eval artifacts")
    ap.add_argument("--pages", default="data/user_pages.json")
    ap.add_argument("--quality", default="data/rag_quality_testset.json")
    ap.add_argument("--output-train", default="data/lora/train.jsonl")
    ap.add_argument("--output-val", default="data/lora/val.jsonl")
    ap.add_argument("--val-ratio", type=float, default=0.1)
    args = ap.parse_args()

    pages_path = Path(args.pages)
    if not pages_path.exists():
        pages_path = Path("data/demo_pages.json")
    pages = read_json(pages_path)

    quality = read_json(Path(args.quality))
    rows: List[Dict] = []
    for item in quality:
        q = str(item.get("query", "")).strip()
        if not q:
            continue
        rows.append(
            {
                "query": q,
                "answer": str(item.get("expected_behavior", "")).strip() or "请根据图像证据给出答案",
                "image_paths": [],
                "task_type": str(item.get("category", "general")),
            }
        )

    # 简单补充：从页面字段中抽取 fact 样本
    for p in pages[:1000]:
        fields = p.get("fields") or {}
        img = p.get("image_path")
        if not img:
            continue
        for k, v in list(fields.items())[:2]:
            rows.append(
                {
                    "query": f"{k}是什么？",
                    "answer": str(v),
                    "image_paths": [img],
                    "task_type": p.get("doc_type", "fact_qa"),
                }
            )

    cut = int(len(rows) * (1 - args.val_ratio))
    train, val = rows[:cut], rows[cut:]

    out_train = Path(args.output_train)
    out_val = Path(args.output_val)
    out_train.parent.mkdir(parents=True, exist_ok=True)
    out_val.parent.mkdir(parents=True, exist_ok=True)
    out_train.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in train) + ("\n" if train else ""), encoding="utf-8")
    out_val.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in val) + ("\n" if val else ""), encoding="utf-8")
    print(f"train={len(train)} -> {out_train}")
    print(f"val={len(val)} -> {out_val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

