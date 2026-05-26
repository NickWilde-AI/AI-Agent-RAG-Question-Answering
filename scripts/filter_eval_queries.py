#!/usr/bin/env python3
"""
评测候选过滤：A 类保留（主体明确可定位），B 类丢弃（上下文不足/泛问）。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, Iterable, Tuple

from src.llm_client import LLMClient


GENERIC_PATTERNS = [
    r"^这(个|份)?(文档|页面)?(怎么|怎么样).*$",
    r"^会议是在哪里开的$",
    r"^这个系统会飞吗$",
]


def is_a_query_heuristic(query: str) -> bool:
    q = (query or "").strip()
    if len(q) < 4:
        return False
    for p in GENERIC_PATTERNS:
        if re.match(p, q):
            return False
    # 至少要有“实体/时间/字段”之一
    hints = ["202", "Q", "负责人", "采购", "合同", "金额", "哪个", "什么", "谁", "多少"]
    return any(h in q for h in hints)


def classify_with_llm(query: str, llm: LLMClient) -> bool:
    prompt = (
        "判断 query 属于 A 还是 B。\n"
        "A: 主体明确、可定位到具体文档页。\n"
        "B: 缺主体、依赖外部上下文、无法唯一定位。\n"
        "只输出 A 或 B。\n"
        f"query: {query}"
    )
    try:
        out = llm.chat_text("你是查询质量判别器。", prompt).strip().upper()
        return out.startswith("A")
    except Exception:
        return is_a_query_heuristic(query)


def iter_jsonl(path: Path) -> Iterable[Dict]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def main() -> int:
    ap = argparse.ArgumentParser(description="Filter eval candidate queries")
    ap.add_argument("--input", default="data/eval_candidates.jsonl")
    ap.add_argument("--keep-output", default="data/eval_candidates_kept.jsonl")
    ap.add_argument("--drop-output", default="data/eval_candidates_dropped.jsonl")
    args = ap.parse_args()

    llm = LLMClient.from_settings()
    keep_path = Path(args.keep_output)
    drop_path = Path(args.drop_output)
    keep_path.parent.mkdir(parents=True, exist_ok=True)
    drop_path.parent.mkdir(parents=True, exist_ok=True)

    keep_count, drop_count = 0, 0
    with keep_path.open("w", encoding="utf-8") as fk, drop_path.open("w", encoding="utf-8") as fd:
        for row in iter_jsonl(Path(args.input)):
            q = str(row.get("query", "")).strip()
            ok = classify_with_llm(q, llm) if llm.enabled else is_a_query_heuristic(q)
            if ok:
                fk.write(json.dumps(row, ensure_ascii=False) + "\n")
                keep_count += 1
            else:
                fd.write(json.dumps(row, ensure_ascii=False) + "\n")
                drop_count += 1

    print(f"kept={keep_count}, dropped={drop_count}")
    print(f"keep -> {keep_path}")
    print(f"drop -> {drop_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

