#!/usr/bin/env python3
"""
对比视觉主链路与 OCR/TextRAG 基线结果，输出固定结构报告。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare visual pipeline report and OCR baseline report")
    ap.add_argument("--visual-report", required=True, help="run_quality_eval 产物 JSON")
    ap.add_argument("--ocr-report", required=True, help="run_ocr_baseline_eval 产物 JSON")
    ap.add_argument("--output", default="logs/ocr_vs_visual_compare.json")
    ap.add_argument("--output-md", default="logs/ocr_vs_visual_compare.md")
    args = ap.parse_args()

    visual = load_json(Path(args.visual_report))
    ocr = load_json(Path(args.ocr_report))
    summary = visual.get("summary", {})
    visual_pass_rate = float(summary.get("pass_rate", 0.0))
    ocr_top1 = float(ocr.get("top1_nonzero_rate", 0.0))
    ocr_top10 = float(ocr.get("top10_nonzero_rate", 0.0))

    out = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "visual_report": args.visual_report,
        "ocr_report": args.ocr_report,
        "visual": {
            "pass_rate": visual_pass_rate,
            "total": int(summary.get("total", 0)),
            "passed": int(summary.get("passed", 0)),
            "failed": int(summary.get("failed", 0)),
        },
        "ocr_text_baseline": {
            "top10_nonzero_rate": ocr_top10,
            "top1_nonzero_rate": ocr_top1,
            "total_cases": int(ocr.get("total_cases", 0)),
            "by_category": ocr.get("by_category", {}),
        },
        "delta": {
            "visual_pass_minus_ocr_top1_nonzero": round(visual_pass_rate - ocr_top1, 4),
            "visual_pass_minus_ocr_top10_nonzero": round(visual_pass_rate - ocr_top10, 4),
        },
        "note": "OCR 基线为词面近似，对比值用于趋势参考，不等价于严格同任务精度。",
    }

    md = "\n".join(
        [
            "# OCR vs 视觉链路对照",
            "",
            f"- 生成时间：{out['created_at']}",
            "",
            "## 核心指标",
            "",
            "| 指标 | 数值 |",
            "|---|---:|",
            f"| 视觉链路 pass_rate | {visual_pass_rate:.4f} |",
            f"| OCR top1_nonzero_rate | {ocr_top1:.4f} |",
            f"| OCR top10_nonzero_rate | {ocr_top10:.4f} |",
            f"| delta(视觉-ocr_top1) | {out['delta']['visual_pass_minus_ocr_top1_nonzero']:+.4f} |",
            f"| delta(视觉-ocr_top10) | {out['delta']['visual_pass_minus_ocr_top10_nonzero']:+.4f} |",
            "",
        ]
    )

    out_path = Path(args.output)
    md_path = Path(args.output_md)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(md, encoding="utf-8")
    print(f"saved_json={out_path}")
    print(f"saved_md={md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
