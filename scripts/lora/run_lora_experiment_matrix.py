#!/usr/bin/env python3
"""
Generate MiniCPM-V LoRA experiment configs for rank / target-module ablation.

This script is intentionally lightweight: it creates reproducible config files,
a command manifest, and an interview-friendly Markdown report. Passing
--execute will run the generated training commands one by one.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import yaml


ROOT = Path(__file__).resolve().parents[2]

TARGET_PROFILES: Dict[str, List[str]] = {
    "attention_minimal": ["q_proj", "v_proj"],
    "attention_full": ["q_proj", "k_proj", "v_proj", "o_proj"],
    "attention_mlp": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
}


def load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def experiment_reason(rank: int, profile: str) -> str:
    if rank <= 8 and profile == "attention_minimal":
        return "低容量 baseline，优先验证是否只靠 q/v attention adapter 就能学到领域术语。"
    if rank == 16 and profile == "attention_mlp":
        return "主推荐配置，兼顾表达能力、显存成本与过拟合风险。"
    if rank >= 32:
        return "高容量对照组，用于观察验证集收益是否继续提升，以及通用能力是否下降。"
    if profile == "attention_full":
        return "中等范围 attention 对照，验证 k/o projection 是否带来稳定收益。"
    return "消融实验，用于定位 rank 与 target modules 的收益边界。"


def to_markdown(payload: Dict[str, Any]) -> str:
    lines = [
        "# MiniCPM-V LoRA 实验矩阵",
        "",
        f"- 生成时间：{payload['created_at']}",
        f"- base config：`{payload['base_config']}`",
        f"- 实验目录：`{payload['output_dir']}`",
        "",
        "## 设计口径",
        "",
        "- rank 从低到高观察容量收益：低 rank 成本低但表达能力弱，高 rank 表达强但更容易过拟合。",
        "- target modules 从 q/v attention 到 full attention，再到 attention+MLP，逐步扩大可训练范围。",
        "- 训练后必须用 base/lora 评测和通用集回归判断是否存在过拟合或灾难性遗忘。",
        "",
        "## 实验列表",
        "",
        "| 实验 | rank | target profile | target modules | 训练命令 | 设计原因 |",
        "|---|---:|---|---|---|---|",
    ]
    for exp in payload["experiments"]:
        mods = ", ".join(exp["target_modules"])
        lines.append(
            f"| `{exp['name']}` | {exp['rank']} | {exp['target_profile']} | {mods} | "
            f"`{exp['command']}` | {exp['reason']} |"
        )
    lines.extend(
        [
            "",
            "## 面试回答提示",
            "",
            "如果被问 rank 为什么这么选，可以回答：先从 8/16 建 baseline，观察验证集和通用集，"
            "如果 32 没有带来验证集收益或通用能力下降，就回退到 16。",
            "",
            "如果被问 target modules 为什么这么选，可以回答：先动 q_proj/v_proj，"
            "因为它们影响注意力关注；收益不足再扩到 k/o 和 MLP；多模态模型不轻易大范围动视觉 encoder。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate LoRA rank / target-module experiment configs")
    ap.add_argument("--base-config", default="configs/lora/minicpm_v26_qlora.yaml")
    ap.add_argument("--ranks", default="8,16,32", help="comma separated ranks")
    ap.add_argument(
        "--profiles",
        default="attention_minimal,attention_full,attention_mlp",
        help=f"comma separated profiles: {','.join(TARGET_PROFILES)}",
    )
    ap.add_argument("--output-dir", default="artifacts/lora/experiment_matrix")
    ap.add_argument("--execute", action="store_true", help="run training commands after generating configs")
    args = ap.parse_args()

    base_config_path = (ROOT / args.base_config).resolve()
    base_cfg = load_yaml(base_config_path)
    ranks = [int(x.strip()) for x in args.ranks.split(",") if x.strip()]
    profiles = [x.strip() for x in args.profiles.split(",") if x.strip()]
    unknown = [p for p in profiles if p not in TARGET_PROFILES]
    if unknown:
        raise ValueError(f"unknown target profiles: {unknown}")

    output_dir = (ROOT / args.output_dir).resolve()
    config_dir = output_dir / "configs"
    experiments: List[Dict[str, Any]] = []

    for rank in ranks:
        for profile in profiles:
            cfg = deepcopy(base_cfg)
            name = f"r{rank}_{profile}"
            cfg["output_dir"] = str(output_dir / "checkpoints" / name)
            cfg["lora"]["r"] = rank
            cfg["lora"]["alpha"] = rank * 2
            cfg["lora"]["target_modules"] = TARGET_PROFILES[profile]
            config_path = config_dir / f"{name}.yaml"
            write_yaml(config_path, cfg)
            try:
                config_arg = str(config_path.relative_to(ROOT))
            except ValueError:
                config_arg = str(config_path)
            command_args = [
                sys.executable,
                "scripts/lora/train_minicpm_lora.py",
                "--config",
                config_arg,
            ]
            command = " ".join(command_args)
            experiments.append(
                {
                    "name": name,
                    "rank": rank,
                    "target_profile": profile,
                    "target_modules": TARGET_PROFILES[profile],
                    "config_path": str(config_path),
                    "command": command,
                    "command_args": command_args,
                    "reason": experiment_reason(rank, profile),
                }
            )

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "base_config": str(base_config_path),
        "output_dir": str(output_dir),
        "experiments": experiments,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "experiment_matrix.json"
    report_path = output_dir / "experiment_matrix.md"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(to_markdown(manifest), encoding="utf-8")
    print(f"manifest={manifest_path}")
    print(f"report={report_path}")

    if args.execute:
        for exp in experiments:
            print(f"[run] {exp['name']}: {exp['command']}", flush=True)
            rc = subprocess.run(exp["command_args"], cwd=str(ROOT), check=False).returncode
            if rc != 0:
                print(f"[failed] {exp['name']} rc={rc}", file=sys.stderr)
                return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
