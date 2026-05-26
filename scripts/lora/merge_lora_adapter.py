#!/usr/bin/env python3
"""
将 LoRA adapter 合并回基础模型，便于单模型部署。
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    try:
        from peft import PeftModel
        from transformers import AutoModelForCausalLM
    except Exception as exc:
        raise RuntimeError("请先安装 transformers peft") from exc

    ap = argparse.ArgumentParser(description="Merge LoRA adapter into base model")
    ap.add_argument("--base-model", required=True)
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    model = AutoModelForCausalLM.from_pretrained(args.base_model, trust_remote_code=True, device_map="auto")
    model = PeftModel.from_pretrained(model, args.adapter)
    merged = model.merge_and_unload()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(str(out))
    print(f"merged model saved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

