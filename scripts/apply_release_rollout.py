#!/usr/bin/env python3
"""
把 data/release_rollout.json 的比例同步到 Traefik 动态配置。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Tuple

import yaml


def read_rollout(path: Path) -> Tuple[int, int, int]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    traffic = cfg.get("traffic", {})
    stable = int(traffic.get("stable_percent", 0))
    canary = int(traffic.get("primary_percent", 0))
    shadow = int(traffic.get("shadow_percent", 0))
    if stable < 0 or canary < 0 or shadow < 0 or stable + canary + shadow != 100:
        raise ValueError("rollout 比例非法，需满足三者非负且总和=100")
    return stable, canary, shadow


def patch_traefik_weights(doc: Dict, stable: int, canary: int, shadow: int) -> Dict:
    services = doc.setdefault("http", {}).setdefault("services", {})
    weighted = services.setdefault("rag-weighted", {}).setdefault("weighted", {}).setdefault("services", [])
    if not weighted:
        weighted.extend(
            [
                {"name": "rag-stable", "weight": stable},
                {"name": "rag-canary", "weight": canary},
                {"name": "rag-shadow", "weight": shadow},
            ]
        )
        return doc
    target_map = {"rag-stable": stable, "rag-canary": canary, "rag-shadow": shadow}
    for item in weighted:
        name = str(item.get("name", ""))
        if name in target_map:
            item["weight"] = target_map[name]
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply release rollout json to Traefik weights")
    ap.add_argument("--rollout-json", default="data/release_rollout.json")
    ap.add_argument("--traefik-yml", default="deploy/traefik/dynamic/rollout.yml")
    args = ap.parse_args()

    rollout = Path(args.rollout_json)
    traefik = Path(args.traefik_yml)
    stable, canary, shadow = read_rollout(rollout)

    doc = yaml.safe_load(traefik.read_text(encoding="utf-8")) or {}
    doc = patch_traefik_weights(doc, stable=stable, canary=canary, shadow=shadow)
    traefik.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")

    print(f"applied stable={stable} canary={canary} shadow={shadow}")
    print(f"updated={traefik}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
