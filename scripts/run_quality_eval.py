#!/usr/bin/env python3
"""
批量跑 data/rag_quality_testset.json，对 /ask 做自动化质量回归。

用法（仓库根目录，需先启动 API）:
  python scripts/run_quality_eval.py
  python scripts/run_quality_eval.py --base http://127.0.0.1:8000 --topk 3
  python scripts/run_quality_eval.py --category smalltalk --fail-fast
  python scripts/run_quality_eval.py --output logs/quality_eval_report.json

退出码: 全部用例通过为 0，否则为 1。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TESTSET = ROOT / "data" / "rag_quality_testset.json"

FALLBACK_MARKERS = ("【关键摘录（最多2条）】", "暂未生成稳定归纳答案", "【下一步建议】")
REJECT_MARKERS = ("材料中未找到足够依据", "不在知识库检索范围内", "请补充更具体的关键词")
SMALLTALK_MARKERS = ("知识库问答助手", "不在知识库", "闲聊", "文档内容")
GOOD_ANSWER_MARKERS = ("依据文档", "结论", "摘录", "【简要结论】")


@dataclass
class CaseResult:
    case_id: str
    category: str
    query: str
    expected_behavior: str
    http_status: int
    passed: bool
    reasons: List[str] = field(default_factory=list)
    branch: str = ""
    verified: bool = False
    hit_count: int = 0
    top1_score: float = 0.0
    cost_ms: int = 0
    answer_preview: str = ""
    source_files: List[str] = field(default_factory=list)


def post_ask(base: str, query: str, topk: int, timeout: float) -> Tuple[int, Dict[str, Any]]:
    url = base.rstrip("/") + "/ask"
    payload = json.dumps({"query": query, "topk": topk}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")[:1200]
        return e.code, {"_http_error": raw}
    except Exception as exc:
        return 0, {"_error": str(exc)}


def _is_fallback_answer(answer: str) -> bool:
    return any(m in answer for m in FALLBACK_MARKERS)


def _is_reject_answer(answer: str) -> bool:
    return any(m in answer for m in REJECT_MARKERS)


def _is_smalltalk_answer(answer: str) -> bool:
    return any(m in answer for m in SMALLTALK_MARKERS)


def _is_good_synthesis(answer: str) -> bool:
    if _is_fallback_answer(answer):
        return False
    if len(answer.strip()) < 40:
        return False
    return any(m in answer for m in GOOD_ANSWER_MARKERS) or "。" in answer or "\n" in answer


def _top1_score(hits: List[Dict[str, Any]]) -> float:
    if not hits:
        return 0.0
    try:
        return float(hits[0].get("score", 0.0))
    except (TypeError, ValueError):
        return 0.0


def score_case(category: str, query: str, data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    if data.get("_http_error") or data.get("_error"):
        return False, [f"请求失败: {data.get('_http_error') or data.get('_error')}"]

    answer = str(data.get("answer") or "")
    hits = data.get("hits") or []
    verified = bool(data.get("verified"))
    hit_count = len(hits)
    top1 = _top1_score(hits)

    if category == "smalltalk":
        ok = _is_smalltalk_answer(answer) and hit_count == 0
        if not _is_smalltalk_answer(answer):
            reasons.append("未识别为闲聊拒答话术")
        if hit_count > 0:
            reasons.append(f"闲聊不应有检索命中，实际 hits={hit_count}")
        if ok:
            reasons.append("闲聊拦截符合预期")
        return ok, reasons

    if category == "robustness":
        q = query.strip()
        smalltalk_like = any(x in q for x in ("笑话", "天气", "股票", "老板", "你是谁", "你的名字"))
        if smalltalk_like:
            ok = _is_smalltalk_answer(answer) or _is_reject_answer(answer)
            if not ok:
                reasons.append("应拒答闲聊/越界，但返回了普通检索答案")
            else:
                reasons.append("越界/闲聊拒答符合预期")
            return ok, reasons

        ok = _is_reject_answer(answer) or (_is_fallback_answer(answer) and hit_count <= 1)
        if top1 >= 0.45 and not _is_reject_answer(answer) and _is_good_synthesis(answer):
            # 极低置信 gibberish 偶尔误命中高相关页，若答案结构正常可放宽
            ok = True
            reasons.append("虽为鲁棒性用例，但命中质量可接受（放宽通过）")
            return ok, reasons
        if not ok:
            if not _is_reject_answer(answer):
                reasons.append("应低置信拒答或明确不足，但返回了看似正常答案")
            if hit_count > 2 and not _is_reject_answer(answer):
                reasons.append(f"鲁棒性问题命中过多 hits={hit_count}")
        else:
            reasons.append("鲁棒性拒答符合预期")
        return ok, reasons

    # 业务问答类：entity / fact / chart / xlsx / tech
    if hit_count == 0:
        if _is_reject_answer(answer):
            # T001 等允许明确不足
            if category == "tech_query" and any(x in query.lower() for x in ("permission", "权限")):
                reasons.append("技术权限类允许「材料不足」拒答")
                return True, reasons
            reasons.append("无检索命中且拒答")
            return False, reasons

    if _is_fallback_answer(answer):
        reasons.append("落入规则兜底摘录，未生成归纳答案")
        return False, reasons

    if len(answer) > 6000:
        reasons.append("答案过长，疑似原文堆砌")
        return False, reasons

    if top1 < 0.25 and not _is_good_synthesis(answer):
        reasons.append(f"top1 分数过低 score={top1:.3f}")
        return False, reasons

    if not _is_good_synthesis(answer):
        reasons.append("缺少结构化结论（无「依据文档/结论」等）")
        return False, reasons

    reasons.append("检索命中且答案结构可接受")
    return True, reasons


def load_testset(path: Path, category: Optional[str], case_ids: Optional[List[str]]) -> List[Dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"测试集格式错误，应为 JSON 数组: {path}")
    out = raw
    if category:
        out = [c for c in out if c.get("category") == category]
    if case_ids:
        wanted = set(case_ids)
        out = [c for c in out if c.get("id") in wanted]
    return out


def run_eval(
    base: str,
    testset_path: Path,
    topk: int,
    timeout: float,
    category: Optional[str],
    case_ids: Optional[List[str]],
    fail_fast: bool,
) -> Tuple[List[CaseResult], Dict[str, Any]]:
    cases = load_testset(testset_path, category, case_ids)
    results: List[CaseResult] = []

    for i, case in enumerate(cases, start=1):
        cid = str(case.get("id", f"case_{i}"))
        cat = str(case.get("category", ""))
        query = str(case.get("query", ""))
        expected = str(case.get("expected_behavior", ""))

        t0 = time.perf_counter()
        status, data = post_ask(base, query, topk=topk, timeout=timeout)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        if status != 200:
            cr = CaseResult(
                case_id=cid,
                category=cat,
                query=query,
                expected_behavior=expected,
                http_status=status,
                passed=False,
                reasons=[f"HTTP {status}"],
                cost_ms=elapsed_ms,
                answer_preview=str(data.get("_http_error") or data.get("_error") or "")[:200],
            )
        else:
            passed, reasons = score_case(cat, query, data)
            hits = data.get("hits") or []
            cr = CaseResult(
                case_id=cid,
                category=cat,
                query=query,
                expected_behavior=expected,
                http_status=status,
                passed=passed,
                reasons=reasons,
                branch=str(data.get("branch") or ""),
                verified=bool(data.get("verified")),
                hit_count=len(hits),
                top1_score=_top1_score(hits),
                cost_ms=int(data.get("cost_ms") or elapsed_ms),
                answer_preview=(str(data.get("answer") or ""))[:240].replace("\n", " "),
                source_files=list(data.get("source_files") or []),
            )
        results.append(cr)

        mark = "PASS" if cr.passed else "FAIL"
        print(f"[{mark}] {cr.case_id} ({cr.category}) q={query!r}")
        print(f"       branch={cr.branch!r} verified={cr.verified} hits={cr.hit_count} top1={cr.top1_score:.3f} cost={cr.cost_ms}ms")
        print(f"       {cr.reasons[0] if cr.reasons else ''}")
        if not cr.passed:
            print(f"       preview={cr.answer_preview!r}")
        print()

        if fail_fast and not cr.passed:
            break

    summary = build_summary(results)
    return results, summary


def build_summary(results: List[CaseResult]) -> Dict[str, Any]:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    by_cat: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0})
    latencies = [r.cost_ms for r in results if r.http_status == 200]

    for r in results:
        by_cat[r.category]["total"] += 1
        if r.passed:
            by_cat[r.category]["passed"] += 1

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "latency_ms": {
            "avg": int(sum(latencies) / len(latencies)) if latencies else 0,
            "max": max(latencies) if latencies else 0,
            "min": min(latencies) if latencies else 0,
        },
        "by_category": dict(by_cat),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="RAG 质量测试集自动化回归")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="API 根地址")
    ap.add_argument("--testset", default=str(DEFAULT_TESTSET), help="测试集 JSON 路径")
    ap.add_argument("--topk", type=int, default=3, help="传给 /ask 的 topk")
    ap.add_argument("--timeout", type=float, default=120.0, help="单条请求超时（秒）")
    ap.add_argument("--category", default="", help="只跑某一类，如 smalltalk / fact_qa")
    ap.add_argument("--ids", default="", help="只跑指定 id，逗号分隔，如 E001,F001")
    ap.add_argument("--output", default="", help="写入完整报告 JSON（含每条明细）")
    ap.add_argument("--fail-fast", action="store_true", help="遇到第一条失败即停止")
    args = ap.parse_args()

    testset_path = Path(args.testset).resolve()
    if not testset_path.exists():
        print(f"测试集不存在: {testset_path}", file=sys.stderr)
        return 2

    base = args.base.rstrip("/")
    try:
        with urllib.request.urlopen(base + "/health", timeout=5) as r:
            if r.status != 200:
                print(f"health 非 200: {r.status}", file=sys.stderr)
                return 2
    except Exception as exc:
        print(f"无法连接 {base}/health，请先 bash scripts/one_click_demo.sh\n  {exc}", file=sys.stderr)
        return 2

    case_ids = [x.strip() for x in args.ids.split(",") if x.strip()] if args.ids else None
    category = args.category.strip() or None

    print(f"测试集: {testset_path}")
    print(f"API: {base}  topk={args.topk}  timeout={args.timeout}s")
    if category:
        print(f"过滤类别: {category}")
    if case_ids:
        print(f"过滤 id: {case_ids}")
    print("─" * 56)

    results, summary = run_eval(
        base=base,
        testset_path=testset_path,
        topk=args.topk,
        timeout=args.timeout,
        category=category,
        case_ids=case_ids,
        fail_fast=args.fail_fast,
    )

    print("═" * 56)
    print(f"总计: {summary['passed']}/{summary['total']} 通过  通过率 {summary['pass_rate']:.1%}")
    print(
        f"时延(ms): avg={summary['latency_ms']['avg']} "
        f"min={summary['latency_ms']['min']} max={summary['latency_ms']['max']}"
    )
    print("按类别:")
    for cat, stat in sorted(summary["by_category"].items()):
        t, p = stat["total"], stat["passed"]
        rate = p / t if t else 0.0
        print(f"  - {cat}: {p}/{t} ({rate:.1%})")

    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "summary": summary,
            "results": [
                {
                    "id": r.case_id,
                    "category": r.category,
                    "query": r.query,
                    "expected_behavior": r.expected_behavior,
                    "passed": r.passed,
                    "reasons": r.reasons,
                    "http_status": r.http_status,
                    "branch": r.branch,
                    "verified": r.verified,
                    "hit_count": r.hit_count,
                    "top1_score": r.top1_score,
                    "cost_ms": r.cost_ms,
                    "source_files": r.source_files,
                    "answer_preview": r.answer_preview,
                }
                for r in results
            ],
        }
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写入: {out_path}")

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
