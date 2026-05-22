#!/usr/bin/env python3
"""
第三阶段提测门禁脚本：
1) /health 连通
2) /ask 返回 trace
3) /eval/run 可执行并落盘
4) /eval/last 可读取最近报告

用法：
  python scripts/stage3_test_gate.py
  python scripts/stage3_test_gate.py --base http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Tuple


def http_get(url: str, timeout: float = 15.0) -> Tuple[int, Dict[str, Any]]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        return e.code, {"_http_error": raw}
    except Exception as exc:
        return 0, {"_error": str(exc)}


def http_post(url: str, payload: Dict[str, Any], timeout: float = 120.0) -> Tuple[int, Dict[str, Any]]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        return e.code, {"_http_error": raw}
    except Exception as exc:
        return 0, {"_error": str(exc)}


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    ap = argparse.ArgumentParser(description="Stage-3 release test gate")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="API 根地址")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print(f"[1/4] health check: {base}/health")
    code, data = http_get(base + "/health", timeout=8)
    require(code == 200, f"/health failed: {code} {data}")
    require(data.get("status") == "ok", f"/health body invalid: {data}")
    print("  -> ok")

    print("[2/4] ask + trace check")
    code, data = http_post(
        base + "/ask",
        {"query": "采购申请单的采购单号是多少？", "topk": 3, "session_id": "stage3-gate"},
        timeout=120,
    )
    require(code == 200, f"/ask failed: {code} {data}")
    require(isinstance(data.get("answer"), str) and data.get("answer"), "ask.answer empty")
    require("trace" in data and data["trace"] is not None, "ask.trace missing")
    trace = data["trace"]
    require("route_branch" in trace, "trace.route_branch missing")
    require(isinstance(trace.get("stages"), list), "trace.stages must be list")
    print(f"  -> ok branch={data.get('branch')} stages={len(trace.get('stages', []))}")

    print("[3/4] eval run + persist check")
    code, data = http_post(base + "/eval/run", {"persist": True, "tag": "stage3-gate"}, timeout=180)
    require(code == 200, f"/eval/run failed: {code} {data}")
    require(data.get("persisted") is True, "eval persisted should be true")
    require(bool(data.get("report_path")), "eval report_path missing")
    require(isinstance(data.get("summary"), dict), "eval summary missing")
    print(f"  -> ok report={data.get('report_path')}")

    print("[4/4] eval last check")
    code, data = http_get(base + "/eval/last", timeout=15)
    require(code == 200, f"/eval/last failed: {code} {data}")
    require(data.get("exists") is True, "eval last report not found")
    require(bool(data.get("report_path")), "eval last report_path missing")
    print("  -> ok")

    print("\nStage-3 提测门禁通过。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
