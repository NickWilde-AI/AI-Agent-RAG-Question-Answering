#!/usr/bin/env python3
"""
汇总 LoRA 对比评测结果，并输出可归档 JSON/Markdown。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def to_markdown(summary: Dict[str, Any]) -> str:
    lines = [
        "# MiniCPM-V LoRA 评测对比",
        "",
        f"- 生成时间：{summary['created_at']}",
        f"- 实验版本：`{summary['experiment_id']}`",
        f"- base_url：`{summary['base_url']}`",
        f"- lora_url：`{summary['lora_url']}`",
        "",
        "## 指标对比",
        "",
        "| 指标 | base | lora | delta |",
        "|---|---:|---:|---:|",
        f"| pass_rate | {summary['base_pass_rate']:.4f} | {summary['lora_pass_rate']:.4f} | {summary['delta_pass_rate']:+.4f} |",
        "",
        "## 结论",
        "",
        f"- 是否达标（delta >= {summary['min_delta']:.4f}）：**{summary['qualified']}**",
        f"- 备注：{summary['note']}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Summarize LoRA eval diff report")
    ap.add_argument("--compare", required=True, help="compare_summary.json 路径")
    ap.add_argument("--experiment-id", default="", help="实验版本号（默认自动时间戳）")
    ap.add_argument("--base-url", default="", help="base 模型地址")
    ap.add_argument("--lora-url", default="", help="lora 模型地址")
    ap.add_argument("--min-delta", type=float, default=0.0, help="判定达标的最小提升阈值")
    ap.add_argument("--note", default="自动汇总", help="备注")
    ap.add_argument("--output-json", default="", help="输出 JSON（默认与 compare 同目录）")
    ap.add_argument("--output-md", default="", help="输出 Markdown（默认与 compare 同目录）")
    args = ap.parse_args()

    compare_path = Path(args.compare)
    data = load_json(compare_path)
    exp_id = args.experiment_id or datetime.now().strftime("minicpm-v26-lora-%Y%m%d-%H%M%S")
    out_dir = compare_path.parent
    out_json = Path(args.output_json) if args.output_json else out_dir / "lora_eval_summary.json"
    out_md = Path(args.output_md) if args.output_md else out_dir / "lora_eval_summary.md"

    base_pass = float(data.get("base_pass_rate", 0.0))
    lora_pass = float(data.get("lora_pass_rate", 0.0))
    delta = float(data.get("delta_pass_rate", lora_pass - base_pass))
    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "experiment_id": exp_id,
        "base_url": args.base_url,
        "lora_url": args.lora_url,
        "base_pass_rate": base_pass,
        "lora_pass_rate": lora_pass,
        "delta_pass_rate": delta,
        "min_delta": args.min_delta,
        "qualified": delta >= args.min_delta,
        "note": args.note,
        "compare_source": str(compare_path),
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(to_markdown(summary), encoding="utf-8")
    print(f"summary_json={out_json}")
    print(f"summary_md={out_md}")
    print(f"qualified={summary['qualified']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
