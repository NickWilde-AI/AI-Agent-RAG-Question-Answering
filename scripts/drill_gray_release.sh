#!/usr/bin/env bash
# 灰度演练：生成比例 -> 应用到 Traefik 动态配置 -> 打印最终权重
set -euo pipefail

PRIMARY="${PRIMARY:-5}"
SHADOW="${SHADOW:-5}"

python scripts/release_rollout.py --primary "$PRIMARY" --shadow "$SHADOW" --output data/release_rollout.json
python scripts/apply_release_rollout.py --rollout-json data/release_rollout.json --traefik-yml deploy/traefik/dynamic/rollout.yml

echo "[gray-drill] rollout json:"
python - <<'PY'
import json
from pathlib import Path
cfg = json.loads(Path("data/release_rollout.json").read_text(encoding="utf-8"))
print(json.dumps(cfg, ensure_ascii=False, indent=2))
PY

echo "[gray-drill] traefik weights:"
python - <<'PY'
import yaml
from pathlib import Path
doc = yaml.safe_load(Path("deploy/traefik/dynamic/rollout.yml").read_text(encoding="utf-8"))
services = doc["http"]["services"]["rag-weighted"]["weighted"]["services"]
for x in services:
    print(f"  {x['name']}: {x['weight']}")
PY
