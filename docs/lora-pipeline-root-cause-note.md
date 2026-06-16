# MiniCPM-V LoRA 流水线排查纪要

## 结论

本次排查锚点是 `scripts/lora/run_minicpm_lora_pipeline.sh` 的 LoRA 闭环：SFT 数据准备、可选 QLoRA 训练、base/LoRA 质量对比、上线门禁和评测汇总。

当前轻量校验通过：Shell 语法检查通过，LoRA 相关 Python 脚本可编译。需要重点解释或确认的行为是：流水线会生成 `lora_release_gate.json` / `lora_release_gate.md`，但门禁命令后接了 `|| true`，因此即使 `lora_release_gate.py` 判定 `BLOCK` 并返回非零，流水线仍会继续执行汇总并输出 done。

## 状态表

| 项 | 证据 | 状态 | 说明 |
|---|---|---|---|
| 数据准备 | `scripts/lora/prepare_sft_data.py` | 已接入 | 默认从 `data/user_pages.json` 和 `data/rag_quality_testset.json` 生成 train/val JSONL。 |
| 训练入口 | `scripts/lora/train_minicpm_lora.py` | 已接入 | 仅在 `train-and-eval` 模式执行，读取 `configs/lora/minicpm_v26_qlora.yaml`。 |
| 实验矩阵 | `scripts/lora/run_lora_experiment_matrix.py` | 已具备 | 支持 rank 8/16/32 和 attention/MLP target module 消融。 |
| base/LoRA 对比 | `scripts/lora/eval_lora_checkpoint.py` | 已接入 | 生成 `base_report.json`、`lora_report.json` 和 `compare_summary.json`。 |
| 上线门禁 | `scripts/lora/lora_release_gate.py` | 已接入 | 对比 pass rate、分类指标、延迟，可选通用能力回归。 |
| 门禁失败处理 | `run_minicpm_lora_pipeline.sh` | 已收敛 | 默认 `STRICT_GATE=1`，会先生成报告，再用 gate 退出码阻断发布；本地实验可设 `STRICT_GATE=0`。 |

## 根因判断

如果问题是“LoRA 验证失败后流水线为什么仍显示完成”，旧版本根因不是门禁脚本失效，而是流水线显式吞掉了门禁非零退出码。

现在脚本改为两阶段语义：

1. 无论 PASS/BLOCK，都先保留 `lora_release_gate.*` 和 summary，便于复盘。
2. 默认 `STRICT_GATE=1`，若 gate BLOCK，最终流水线返回非零，避免 CI/发布误判成功。
3. 本地实验可临时设 `STRICT_GATE=0`，表示只归档报告，不阻断脚本。

## 已验证命令

```bash
bash -n scripts/lora/run_minicpm_lora_pipeline.sh
python -m py_compile scripts/lora/lora_release_gate.py scripts/lora/run_lora_experiment_matrix.py scripts/lora/summarize_lora_eval.py scripts/lora/eval_lora_checkpoint.py
python scripts/lora/prepare_sft_data.py --pages data/demo_pages.json --quality data/rag_quality_testset.json --output-train /tmp/lora-train.jsonl --output-val /tmp/lora-val.jsonl
python scripts/lora/train_minicpm_lora.py --config configs/lora/minicpm_v26_qlora.yaml --dry-run
```

## 建议决策

| 场景 | 建议 |
|---|---|
| 面试证据链 | 使用默认 `STRICT_GATE=1` 更贴近真实发布，也能保留 BLOCK 报告。 |
| CI / 发布门禁 | 保持 `STRICT_GATE=1`，让自动化发布在 `BLOCK` 时失败。 |
| 本地实验 | 可设置 `STRICT_GATE=0`，确保 `lora_release_gate.md` 里能看到 BLOCK 原因和失败样本。 |

## 面试回答口径

我会把 LoRA 训练和上线验证拆成两层：训练/评测流水线负责生成完整证据链，release gate 负责给出 PASS/BLOCK 决策。当前脚本会先保留评测报告和 BLOCK 原因，再按 `STRICT_GATE` 决定是否用非零退出码阻断发布；真实 CI 保持默认 strict。
