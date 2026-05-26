#!/usr/bin/env python3
"""
OCR/TextRAG 对照基线（离线近似版）：
- 不依赖 VLM，仅基于页面文本词面召回
- 输出与当前链路的简易对照报告
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple


def tokenize(s: str) -> List[str]:
    s = (s or "").lower()
    toks = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{2,}", s)
    return toks


def load_pages(path: Path) -> List[Dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases(path: Path) -> List[Dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def retrieve_topk(query: str, pages: List[Dict], k: int = 10) -> List[Tuple[str, float]]:
    q = set(tokenize(query))
    scored = []
    for p in pages:
        text = (p.get("content", "") or "") + " " + " ".join((p.get("fields") or {}).keys())
        t = set(tokenize(text))
        overlap = len(q & t)
        score = overlap / max(len(q), 1)
        scored.append((p.get("page_id", ""), score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


def main() -> int:
    ap = argparse.ArgumentParser(description="Run OCR/Text baseline eval")
    ap.add_argument("--pages", default="data/user_pages.json")
    ap.add_argument("--testset", default="data/rag_quality_testset.json")
    ap.add_argument("--output", default="logs/ocr_baseline_report.json")
    args = ap.parse_args()

    pages_path = Path(args.pages)
    if not pages_path.exists():
        pages_path = Path("data/demo_pages.json")
    pages = load_pages(pages_path)
    cases = load_cases(Path(args.testset))

    hit_nonzero = 0
    top1_nonzero = 0
    by_category: Dict[str, Dict[str, int]] = {}
    for case in cases:
        q = str(case.get("query", ""))
        cat = str(case.get("category", "unknown"))
        by_category.setdefault(cat, {"total": 0, "top10_nonzero": 0, "top1_nonzero": 0})
        by_category[cat]["total"] += 1
        hits = retrieve_topk(q, pages, k=10)
        if hits:
            hit_nonzero += 1
            by_category[cat]["top10_nonzero"] += 1
            if hits[0][1] > 0:
                top1_nonzero += 1
                by_category[cat]["top1_nonzero"] += 1

    total = max(len(cases), 1)
    cat_rate = {}
    for cat, stat in by_category.items():
        denom = max(stat["total"], 1)
        cat_rate[cat] = {
            "total": stat["total"],
            "top10_nonzero_rate": round(stat["top10_nonzero"] / denom, 4),
            "top1_nonzero_rate": round(stat["top1_nonzero"] / denom, 4),
        }
    report = {
        "total_cases": len(cases),
        "top10_nonzero_rate": round(hit_nonzero / total, 4),
        "top1_nonzero_rate": round(top1_nonzero / total, 4),
        "by_category": cat_rate,
        "note": "该脚本是 OCR/TextRAG 近似词面基线，用于与主链路做趋势对照。",
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"saved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

