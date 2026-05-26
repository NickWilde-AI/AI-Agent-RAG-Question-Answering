#!/usr/bin/env python3
"""
从页面索引自动生成评测候选（query-answer）。

优先使用 LLM 生成；无 LLM 时回退规则模板，保证离线可跑。
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Dict, List

from src.llm_client import LLMClient
from src.models import Page


def load_pages(path: Path) -> List[Page]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Page(**x) for x in data]


def llm_generate(page: Page, llm: LLMClient, n: int = 3) -> List[Dict[str, Any]]:
    prompt = (
        "你是评测集构造器。请基于页面内容生成问答对。"
        "输出 JSON 数组，每项包含 query, answer, category。"
        f"最多 {n} 条，query 必须可被页面直接回答。\n"
        f"页面内容：{(page.content or '')[:3000]}\n"
        f"结构化字段：{json.dumps(page.fields or {}, ensure_ascii=False)}"
    )
    try:
        raw = llm.chat_text("只输出 JSON。", prompt)
        arr = json.loads(raw)
        out = []
        for item in arr[:n]:
            q = str(item.get("query", "")).strip()
            a = str(item.get("answer", "")).strip()
            c = str(item.get("category", page.doc_type)).strip() or page.doc_type
            if q and a:
                out.append(
                    {
                        "query": q,
                        "answer": a,
                        "category": c,
                        "gold_pages": [page.page_id],
                        "source_file": page.source_file or page.doc_id,
                    }
                )
        return out
    except Exception:
        return []


def fallback_generate(page: Page) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for k, v in list((page.fields or {}).items())[:3]:
        out.append(
            {
                "query": f"{k}是什么？",
                "answer": str(v),
                "category": page.doc_type,
                "gold_pages": [page.page_id],
                "source_file": page.source_file or page.doc_id,
            }
        )
    if not out and page.content:
        snippet = "".join((page.content or "").strip().split())[:36]
        if snippet:
            out.append(
                {
                    "query": "请概括该页面的核心信息。",
                    "answer": snippet,
                    "category": page.doc_type,
                    "gold_pages": [page.page_id],
                    "source_file": page.source_file or page.doc_id,
                }
            )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate eval candidates from page index")
    ap.add_argument("--pages", default="data/user_pages.json", help="页面索引 JSON")
    ap.add_argument("--output", default="data/eval_candidates.jsonl", help="输出 JSONL")
    ap.add_argument("--sample-pages", type=int, default=200, help="最多采样页数")
    ap.add_argument("--pairs-per-page", type=int, default=3, help="每页最多生成问答数")
    ap.add_argument("--seed", type=int, default=42, help="随机种子")
    args = ap.parse_args()

    pages_path = Path(args.pages)
    if not pages_path.exists():
        pages_path = Path("data/demo_pages.json")
    pages = load_pages(pages_path)
    random.seed(args.seed)
    if len(pages) > args.sample_pages:
        pages = random.sample(pages, args.sample_pages)

    llm = LLMClient.from_settings()
    rows: List[Dict[str, Any]] = []
    for page in pages:
        generated = llm_generate(page, llm, n=args.pairs_per_page) if llm.enabled else []
        if not generated:
            generated = fallback_generate(page)
        rows.extend(generated[: args.pairs_per_page])

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"generated={len(rows)} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

