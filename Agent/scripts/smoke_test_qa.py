#!/usr/bin/env python3
"""
对本地 /ask 做多类型冒烟请求（需已启动 FastAPI，默认 http://127.0.0.1:8000）。

用法（在 Agent 目录下）:
  python scripts/smoke_test_qa.py
  python scripts/smoke_test_qa.py --base http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def post_ask(base: str, query: str, topk: int = 5) -> tuple[int, dict]:
    url = base.rstrip("/") + "/ask"
    payload = json.dumps({"query": query, "topk": topk}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")[:800]
        return e.code, {"_http_error": raw}


def main() -> int:
    ap = argparse.ArgumentParser(description="RAG /ask 冒烟测试")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="API 根地址")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    health_url = base + "/health"
    try:
        with urllib.request.urlopen(health_url, timeout=5) as r:
            if r.status != 200:
                print(f"health 非 200: {r.status}", file=sys.stderr)
                return 2
    except Exception as exc:
        print(f"无法连接 {health_url}: {exc}", file=sys.stderr)
        return 2

    cases: list[tuple[str, int]] = [
        ("你好", 3),
        ("手车互联", 5),
        ("汤梅娟", 5),
        ("采购单号", 5),
        ("asdf weird 123 !!!", 5),
        ("销售额 最高", 5),
        ("请用一句话说明本知识库里有什么类型的内容", 6),
    ]

    ok = 0
    for query, topk in cases:
        code, data = post_ask(base, query, topk)
        ans = data.get("answer") or data.get("_http_error") or ""
        preview = str(ans)[:140].replace("\n", " ")
        src = data.get("source_files") or []
        br = data.get("branch", "")
        print(f"[HTTP {code}] branch={br!r} q={query!r}")
        print(f"    answer_preview={preview!r}")
        print(f"    source_files={src}")
        if code == 200:
            ok += 1
        print()

    print(f"通过: {ok}/{len(cases)} 条 HTTP 200")
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
