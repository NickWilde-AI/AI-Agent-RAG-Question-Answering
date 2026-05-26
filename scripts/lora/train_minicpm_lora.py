#!/usr/bin/env python3
"""
MiniCPM-V QLoRA 训练入口（PEFT + Transformers）。

说明：
- 该脚本需要额外安装训练依赖：transformers/peft/datasets/accelerate/bitsandbytes/trl。
- 默认读取 configs/lora/minicpm_v26_qlora.yaml。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def require_deps():
    try:
        import torch  # noqa: F401
        from datasets import load_dataset  # noqa: F401
        from peft import LoraConfig, get_peft_model  # noqa: F401
        from transformers import (  # noqa: F401
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            Trainer,
            TrainingArguments,
        )
    except Exception as exc:
        raise RuntimeError(
            "缺少训练依赖。请先安装：pip install transformers peft datasets accelerate bitsandbytes trl"
        ) from exc


def main() -> int:
    require_deps()
    import torch
    from datasets import load_dataset
    from peft import LoraConfig, get_peft_model
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        Trainer,
        TrainingArguments,
    )

    ap = argparse.ArgumentParser(description="Train MiniCPM-V LoRA")
    ap.add_argument("--config", default="configs/lora/minicpm_v26_qlora.yaml")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))

    bnb_cfg = BitsAndBytesConfig(
        load_in_4bit=cfg["quantization"]["load_in_4bit"],
        bnb_4bit_use_double_quant=cfg["quantization"]["bnb_4bit_use_double_quant"],
        bnb_4bit_quant_type=cfg["quantization"]["bnb_4bit_quant_type"],
        bnb_4bit_compute_dtype=getattr(torch, cfg["quantization"]["bnb_4bit_compute_dtype"]),
    )

    tokenizer = AutoTokenizer.from_pretrained(cfg["model_name_or_path"], trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        cfg["model_name_or_path"],
        trust_remote_code=True,
        quantization_config=bnb_cfg,
        device_map="auto",
    )
    lora_cfg = LoraConfig(
        r=cfg["lora"]["r"],
        lora_alpha=cfg["lora"]["alpha"],
        lora_dropout=cfg["lora"]["dropout"],
        target_modules=cfg["lora"]["target_modules"],
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_cfg)

    ds = load_dataset("json", data_files={"train": cfg["dataset_path"], "validation": cfg["val_dataset_path"]})

    def preprocess(ex):
        prompt = f"问题：{ex['query']}\n回答："
        text = prompt + str(ex.get("answer", ""))
        tok = tokenizer(text, truncation=True, max_length=cfg["max_seq_length"])
        tok["labels"] = list(tok["input_ids"])
        return tok

    ds = ds.map(preprocess, remove_columns=ds["train"].column_names)
    out_dir = Path(cfg["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    train_args = TrainingArguments(
        output_dir=str(out_dir),
        num_train_epochs=cfg["num_train_epochs"],
        learning_rate=cfg["learning_rate"],
        per_device_train_batch_size=cfg["per_device_train_batch_size"],
        gradient_accumulation_steps=cfg["gradient_accumulation_steps"],
        warmup_ratio=cfg["warmup_ratio"],
        weight_decay=cfg["weight_decay"],
        logging_steps=cfg["logging_steps"],
        save_steps=cfg["save_steps"],
        eval_steps=cfg["eval_steps"],
        evaluation_strategy="steps",
        save_strategy="steps",
        bf16=True,
        report_to=[],
    )

    trainer = Trainer(
        model=model,
        args=train_args,
        train_dataset=ds["train"],
        eval_dataset=ds["validation"],
        tokenizer=tokenizer,
    )
    trainer.train()
    trainer.save_model(str(out_dir))
    (out_dir / "train_config.snapshot.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

