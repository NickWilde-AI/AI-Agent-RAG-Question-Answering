# MiniCPM-V LoRA 面试证据链

本文档用于回答“你是否真的做过 MiniCPM-V LoRA 微调与在线验证”这类追问。

## 一句话口径

本项目实现了 MiniCPM-V QLoRA 领域适配闭环：SFT 数据准备、rank/target modules 消融配置、adapter 训练、base/lora 双服务质量评测、上线门禁与灰度验证报告。

## 代码证据

| 面试追问 | 代码证据 | 说明 |
|---|---|---|
| SFT 数据怎么来 | `scripts/lora/prepare_sft_data.py` | 从页面索引字段、图表、人员、故障码和带金标答案的质量集生成 `train.jsonl` / `val.jsonl` |
| LoRA 参数在哪里 | `configs/lora/minicpm_v26_qlora.yaml` | MiniCPM-V 2.6、rank、alpha、dropout、target modules、4bit QLoRA |
| 训练入口在哪里 | `scripts/lora/train_minicpm_lora.py` | PEFT `LoraConfig` + Transformers `Trainer` |
| rank 怎么选 | `scripts/lora/run_lora_experiment_matrix.py` | 自动生成 rank / target modules 消融实验配置 |
| target modules 怎么选 | `scripts/lora/run_lora_experiment_matrix.py` | 提供 q/v、q/k/v/o、attention+MLP 三档对照 |
| base 和 LoRA 怎么对比 | `scripts/lora/eval_lora_checkpoint.py` | 分别调用 base_url / lora_url 跑质量集 |
| 上线前怎么验收 | `scripts/lora/lora_release_gate.py` | 对比 pass_rate、分类指标、延迟、通用能力回归 |
| 报告怎么沉淀 | `scripts/lora/summarize_lora_eval.py` | 输出 JSON + Markdown 归档报告 |
| 一键闭环 | `scripts/lora/run_minicpm_lora_pipeline.sh` | 数据准备 -> 可选训练 -> 评测 -> 门禁 -> 汇总 |

## 推荐命令

生成 rank / target modules 实验矩阵：

```bash
python scripts/lora/run_lora_experiment_matrix.py \
  --ranks 8,16,32 \
  --profiles attention_minimal,attention_full,attention_mlp \
  --output-dir artifacts/lora/experiment_matrix
```

准备 SFT 数据：

```bash
python scripts/lora/prepare_sft_data.py \
  --pages data/user_pages.json \
  --quality data/rag_quality_testset.json \
  --output-train data/lora/train.jsonl \
  --output-val data/lora/val.jsonl
```

> 默认不会把 `expected_behavior` 当作标准答案训练，避免模型学到“验收规则描述”而不是实际回答。若只是本地实验需要行为类样本，可显式加 `--include-behavior-cases`。

训练 MiniCPM-V QLoRA：

```bash
python scripts/lora/train_minicpm_lora.py \
  --config configs/lora/minicpm_v26_qlora.yaml
```

无 GPU 或未安装训练依赖时，可先做链路校验：

```bash
python scripts/lora/train_minicpm_lora.py \
  --config configs/lora/minicpm_v26_qlora.yaml \
  --dry-run
```

对比 base / LoRA 服务：

```bash
python scripts/lora/eval_lora_checkpoint.py \
  --base-url http://127.0.0.1:8000 \
  --lora-url http://127.0.0.1:8010 \
  --out-dir logs/lora_eval/minicpm-v26-domain-v1
```

执行上线门禁：

```bash
python scripts/lora/lora_release_gate.py \
  --base-report logs/lora_eval/minicpm-v26-domain-v1/base_report.json \
  --lora-report logs/lora_eval/minicpm-v26-domain-v1/lora_report.json \
  --min-pass-delta 0.02 \
  --max-general-drop 0.02 \
  --max-latency-delta-ms 800
```

一键流水线默认 `STRICT_GATE=1`：即使 gate BLOCK，也会先生成报告，再用非零退出码阻断发布；本地实验只想看报告可设置 `STRICT_GATE=0`。

## 面试回答模板

### 你们 rank 怎么选？

我不会直接固定一个 rank，而是做消融实验。低容量从 rank=8 开始，观察领域术语和图表类验证集是否有提升；主实验用 rank=16，兼顾表达能力和过拟合风险；rank=32 作为高容量对照，如果验证集收益不明显或通用能力下降，就不会采用。项目里 `run_lora_experiment_matrix.py` 会自动生成这些配置和训练命令。

### target modules 怎么选？

先从 attention 的 `q_proj`、`v_proj` 做最小适配，因为它们影响模型关注哪些文本/图像区域；如果收益不足，再扩展到 `k_proj`、`o_proj`；如果领域表达仍不够，再加入 MLP 的 `gate_proj`、`up_proj`、`down_proj`。多模态模型里不会一开始大范围动视觉 encoder，避免破坏通用图文理解能力。

### 怎么判断过拟合？

我看训练 loss、领域验证集和通用回归集三类信号。如果训练 loss 持续下降，但领域验证集 pass_rate 不涨甚至下降，说明模型可能过拟合训练样本。处理方式是降低 rank、降低学习率、增加 LoRA dropout、减少 epoch、early stopping，并清理重复/模板化样本。

### 怎么判断灾难性遗忘？

如果领域验证集提升，但通用图文理解回归集下降明显，比如超过 2 个百分点，就认为存在遗忘风险。项目里 `lora_release_gate.py` 支持传入 general base/lora report，用 `max_general_drop` 做门禁。如果不通过，就不能上线 adapter，需要混入通用 replay 样本、降低 rank 或缩小 target modules。

### 怎么在线验证？

训练完成后不会直接替换线上模型，而是同时启动 base 服务和 LoRA 服务。`eval_lora_checkpoint.py` 分别调用两个服务跑相同质量集，`lora_release_gate.py` 对比 pass_rate、分类指标、平均延迟和通用能力回归。只有 LoRA 在领域集提升、通用集不明显下降、延迟可接受时，才进入 shadow 或灰度验证。

## 当前默认配置

`configs/lora/minicpm_v26_qlora.yaml` 中默认配置：

```yaml
model_name_or_path: openbmb/MiniCPM-V-2_6
num_train_epochs: 2
learning_rate: 0.0002
weight_decay: 0.01

lora:
  r: 16
  alpha: 32
  dropout: 0.05
  target_modules:
    - q_proj
    - k_proj
    - v_proj
    - o_proj
    - gate_proj
    - up_proj
    - down_proj

quantization:
  load_in_4bit: true
  bnb_4bit_quant_type: nf4
  bnb_4bit_compute_dtype: bfloat16
```
