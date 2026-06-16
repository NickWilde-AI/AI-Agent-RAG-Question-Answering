#!/usr/bin/env python3
"""
从评测/样本数据转换为 LoRA SFT 训练集。
输出 JSONL：{"image_paths":[...], "query":"", "answer":"", "task_type":""}
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _image_paths(page: Dict[str, Any]) -> List[str]:
    img = str(page.get("image_path") or "").strip()
    return [img] if img else []


def _add_row(rows: List[Dict[str, Any]], query: str, answer: str, image_paths: List[str], task_type: str) -> None:
    query = " ".join(str(query or "").split())
    answer = " ".join(str(answer or "").split())
    if not query or not answer:
        return
    row = {
        "query": query,
        "answer": answer,
        "image_paths": image_paths,
        "task_type": task_type or "general",
    }
    key = (row["query"], row["answer"], tuple(row["image_paths"]))
    if key not in _add_row.seen:
        rows.append(row)
        _add_row.seen.add(key)


_add_row.seen = set()  # type: ignore[attr-defined]


def main() -> int:
    _add_row.seen = set()  # type: ignore[attr-defined]
    ap = argparse.ArgumentParser(description="Prepare SFT dataset from quality/eval artifacts")
    ap.add_argument("--pages", default="data/user_pages.json")
    ap.add_argument("--quality", default="data/rag_quality_testset.json")
    ap.add_argument("--output-train", default="data/lora/train.jsonl")
    ap.add_argument("--output-val", default="data/lora/val.jsonl")
    ap.add_argument("--val-ratio", type=float, default=0.1)
    ap.add_argument(
        "--include-behavior-cases",
        action="store_true",
        help="把只有 expected_behavior 的质量用例也写入 SFT；默认关闭，避免把验收规则当标准答案训练。",
    )
    args = ap.parse_args()
    if not 0 < args.val_ratio < 0.5:
        raise ValueError("--val-ratio should be in (0, 0.5)")

    pages_path = Path(args.pages)
    if not pages_path.exists():
        pages_path = Path("data/demo_pages.json")
    pages = read_json(pages_path)

    quality = read_json(Path(args.quality))
    rows: List[Dict[str, Any]] = []
    for item in quality:
        q = str(item.get("query", "")).strip()
        if not q:
            continue
        answer = (
            item.get("gold_answer")
            or item.get("expected_answer")
            or item.get("answer")
            or (item.get("expected_behavior") if args.include_behavior_cases else "")
        )
        _add_row(rows, q, str(answer or ""), [], str(item.get("category", "general")))

    # 从页面结构中抽取可监督样本；没有页图时也保留文本样本，避免本地索引全无 image_path 时产出空训练集。
    for p in pages[:1000]:
        fields = p.get("fields") or {}
        imgs = _image_paths(p)
        for k, v in list(fields.items())[:2]:
            _add_row(
                rows,
                f"{k}是什么？",
                str(v),
                imgs,
                str(p.get("doc_type", "fact_qa")),
            )
        chart_data = p.get("chart_data") or {}
        for k, v in list(chart_data.items())[:3]:
            _add_row(rows, f"{k}的数值是多少？", f"{k}（{v}）", imgs, "chart_qa")
        if chart_data:
            best = max(chart_data, key=chart_data.get)
            _add_row(rows, "哪个指标数值最高？", f"{best}（{chart_data[best]}）", imgs, "chart_qa")
        for person in list(p.get("people") or [])[:3]:
            _add_row(rows, f"文档中提到的人员是谁？", str(person), imgs, "entity_lookup")
        for code in re.findall(r"\b[A-Z]+-\d+\b", str(p.get("content") or "").upper()):
            sentence = next((s.strip() for s in re.split(r"(?<=[。.!?])\s*", p.get("content") or "") if code in s.upper()), "")
            _add_row(rows, f"故障代码 {code} 是什么？", sentence or code, imgs, "manual_qa")

    if not rows:
        raise RuntimeError("没有生成任何 SFT 样本：请检查 pages/quality 数据，或提供 gold_answer/fields/chart_data。")

    cut = int(len(rows) * (1 - args.val_ratio))
    cut = min(max(cut, 1), len(rows) - 1) if len(rows) > 1 else len(rows)
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
