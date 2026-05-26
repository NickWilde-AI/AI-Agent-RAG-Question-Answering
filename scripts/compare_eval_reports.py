#!/usr/bin/env python3
"""
对比两版离线评测报告（reports/eval/eval-report-*.json）。

默认行为：若不传 --base/--target，则自动对比最近两份报告。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def pick_latest_two(report_dir: Path) -> Tuple[Path, Path]:
    files = sorted([p for p in report_dir.glob("eval-report-*.json") if p.is_file()])
    if len(files) < 2:
        raise FileNotFoundError("至少需要两份评测报告才能做对比")
    return files[-2], files[-1]


def _fmt_delta(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.4f}"


def _extract_metrics(payload: Dict[str, Any]) -> Dict[str, float]:
    report = payload.get("report", {})
    overall = report.get("overall", {})
    engineering = report.get("engineering", {})
    return {
        "overall.recall_at_10": float(overall.get("recall_at_10", 0.0)),
        "overall.accuracy": float(overall.get("accuracy", 0.0)),
        "overall.router_acc": float(overall.get("router_acc", 0.0)),
        "engineering.verifier_pass_rate": float(engineering.get("verifier_pass_rate", 0.0)),
        "engineering.fallback_rate": float(engineering.get("fallback_rate", 0.0)),
        "engineering.cache_hit_rate": float(engineering.get("cache_hit_rate", 0.0)),
    }


def _extract_per_category(payload: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    report = payload.get("report", {})
    rows: List[Dict[str, Any]] = list(report.get("per_category", []))
    for row in rows:
        cat = str(row.get("category", "unknown"))
        out[cat] = {
            "recall_at_10": float(row.get("recall_at_10", 0.0)),
            "accuracy": float(row.get("accuracy", 0.0)),
            "router_acc": float(row.get("router_acc", 0.0)),
            "verifier_pass_rate": float(row.get("verifier_pass_rate", 0.0)),
            "fallback_rate": float(row.get("fallback_rate", 0.0)),
        }
    return out


def print_summary(base_payload: Dict[str, Any], target_payload: Dict[str, Any]) -> None:
    base_metrics = _extract_metrics(base_payload)
    target_metrics = _extract_metrics(target_payload)
    print("== Overall & Engineering ==")
    for key in base_metrics:
        old = base_metrics[key]
        new = target_metrics.get(key, 0.0)
        print(f"{key:32s} {old:.4f} -> {new:.4f}  ({_fmt_delta(new - old)})")


def print_categories(base_payload: Dict[str, Any], target_payload: Dict[str, Any]) -> None:
    base = _extract_per_category(base_payload)
    target = _extract_per_category(target_payload)
    cats = sorted(set(base.keys()) | set(target.keys()))
    print("\n== Per Category (accuracy / recall@10) ==")
    for cat in cats:
        old_acc = base.get(cat, {}).get("accuracy", 0.0)
        new_acc = target.get(cat, {}).get("accuracy", 0.0)
        old_recall = base.get(cat, {}).get("recall_at_10", 0.0)
        new_recall = target.get(cat, {}).get("recall_at_10", 0.0)
        print(
            f"{cat}: "
            f"acc {old_acc:.4f}->{new_acc:.4f}({_fmt_delta(new_acc - old_acc)}), "
            f"recall {old_recall:.4f}->{new_recall:.4f}({_fmt_delta(new_recall - old_recall)})"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare two eval reports")
    ap.add_argument("--report-dir", default="reports/eval", help="报告目录")
    ap.add_argument("--base", default="", help="基线报告路径")
    ap.add_argument("--target", default="", help="目标报告路径")
    args = ap.parse_args()

    report_dir = Path(args.report_dir)
    if args.base and args.target:
        base_path = Path(args.base)
        target_path = Path(args.target)
    else:
        base_path, target_path = pick_latest_two(report_dir)

    base_payload = load_json(base_path)
    target_payload = load_json(target_path)
    print(f"base={base_path}")
    print(f"target={target_path}")
    print_summary(base_payload, target_payload)
    print_categories(base_payload, target_payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
