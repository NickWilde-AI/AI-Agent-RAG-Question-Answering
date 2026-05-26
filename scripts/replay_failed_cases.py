#!/usr/bin/env python3
"""
失败用例回放工具：从 run_quality_eval 报告中抽取失败样本，重放 /ask 并落盘 trace。
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def post_ask(base: str, query: str, topk: int, timeout: float) -> Dict[str, Any]:
    url = base.rstrip("/") + "/ask"
    payload = json.dumps({"query": query, "topk": topk}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_failed(report_path: Path, max_cases: int) -> List[Dict[str, Any]]:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    rows = data.get("results", [])
    failed = [x for x in rows if not bool(x.get("passed"))]
    return failed[:max_cases]


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay failed /ask cases from quality report")
    ap.add_argument("--report", required=True, help="run_quality_eval 输出报告")
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="API 根地址")
    ap.add_argument("--topk", type=int, default=5, help="回放请求 topk")
    ap.add_argument("--timeout", type=float, default=120.0, help="请求超时秒")
    ap.add_argument("--max-cases", type=int, default=20, help="最多回放失败条数")
    ap.add_argument("--output-dir", default="logs/incident_replay", help="输出目录")
    args = ap.parse_args()

    report_path = Path(args.report)
    failed = load_failed(report_path, max_cases=max(1, args.max_cases))
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.output_dir) / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    replay_index: List[Dict[str, Any]] = []
    for item in failed:
        case_id = str(item.get("id", "unknown"))
        query = str(item.get("query", ""))
        payload: Dict[str, Any] = {
            "case_id": case_id,
            "query": query,
            "replayed_at": datetime.now().isoformat(timespec="seconds"),
            "origin_reasons": item.get("reasons", []),
            "origin_branch": item.get("branch", ""),
            "origin_hit_count": item.get("hit_count", 0),
        }
        try:
            resp = post_ask(base=args.base, query=query, topk=args.topk, timeout=args.timeout)
            payload["status"] = "ok"
            payload["response"] = resp
        except urllib.error.HTTPError as exc:
            payload["status"] = "http_error"
            payload["http_status"] = int(exc.code)
            payload["error"] = exc.read().decode("utf-8", errors="replace")[:1000]
        except Exception as exc:  # noqa: BLE001
            payload["status"] = "error"
            payload["error"] = str(exc)

        out = out_dir / f"{case_id}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        replay_index.append({"case_id": case_id, "status": payload["status"], "file": str(out)})
        print(f"replayed case={case_id} status={payload['status']}")

    index_path = out_dir / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "report": str(report_path.resolve()),
                "base": args.base,
                "count": len(replay_index),
                "items": replay_index,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"index={index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
