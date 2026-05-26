#!/usr/bin/env python3
"""
LoRA 检查点评估入口：
- 调用 /ask 批量跑质量集
- 对比 base 与 lora checkpoint（依赖你把服务分别启动在不同 base url）
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run_quality(base: str, output: Path) -> int:
    cmd = [
        sys.executable,
        "scripts/run_quality_eval.py",
        "--base",
        base,
        "--output",
        str(output),
    ]
    return subprocess.run(cmd, cwd=str(ROOT), check=False).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate LoRA checkpoint with quality suite")
    ap.add_argument("--base-url", required=True, help="base 模型服务地址")
    ap.add_argument("--lora-url", required=True, help="LoRA 模型服务地址")
    ap.add_argument("--out-dir", default="logs/lora_eval")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    base_out = out_dir / "base_report.json"
    lora_out = out_dir / "lora_report.json"

    c1 = run_quality(args.base_url, base_out)
    c2 = run_quality(args.lora_url, lora_out)
    if c1 != 0 or c2 != 0:
        print("quality eval failed")
        return 1

    b = json.loads(base_out.read_text(encoding="utf-8"))
    l = json.loads(lora_out.read_text(encoding="utf-8"))
    summary = {
        "base_pass_rate": b["summary"]["pass_rate"],
        "lora_pass_rate": l["summary"]["pass_rate"],
        "delta_pass_rate": round(l["summary"]["pass_rate"] - b["summary"]["pass_rate"], 6),
    }
    summary_path = out_dir / "compare_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"saved -> {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

