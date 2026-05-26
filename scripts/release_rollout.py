#!/usr/bin/env python3
"""
灰度发布/影子流量配置生成器（配置层工具）。

用途：
- 生成 Nginx / Gateway 可读取的流量比例配置
- 支持 5% -> 30% -> 100% 等阶段化 rollout
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Dict


def build_config(primary: int, shadow: int) -> Dict:
    if primary < 0 or shadow < 0 or primary + shadow > 100:
        raise ValueError("primary/shadow 百分比非法")
    return {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "traffic": {
            "primary_percent": primary,
            "shadow_percent": shadow,
            "stable_percent": max(0, 100 - primary - shadow),
        },
        "notes": "由 scripts/release_rollout.py 生成",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate rollout traffic config")
    ap.add_argument("--primary", type=int, default=5, help="新链路灰度流量百分比")
    ap.add_argument("--shadow", type=int, default=5, help="影子流量百分比")
    ap.add_argument("--output", default="data/release_rollout.json")
    args = ap.parse_args()

    cfg = build_config(primary=args.primary, shadow=args.shadow)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"rollout config written: {out}")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

