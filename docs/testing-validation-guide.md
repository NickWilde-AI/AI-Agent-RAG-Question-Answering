# 测试与验收手册

本文档用于本地自测、企业交付验收、CI 门禁和问题复盘。所有命令默认在仓库根目录执行。

## 0. 改动边界

本项目测试分四层：

| 层级 | 目标 | 是否需要模型服务 | 推荐场景 |
|---|---|---:|---|
| 静态检查 | 脚本语法、Python 编译 | 否 | 每次提交前 |
| 轻量离线评测 | demo 索引、QAEngine、Agentic trace | 否 | 本地快速回归 |
| 在线 API 评测 | `/ask`、`/eval/run`、trace、citations | 是 | 交付验收、联调 |
| LoRA 验证 | SFT 数据、dry-run、base/lora 对比、release gate | 训练可选 | MiniCPM-V 微调闭环 |

不要把私有文档、`.env`、真实报告里的敏感内容提交到 Git。

## 1. 环境准备

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

如只做轻量链路测试，`.env` 可不配置真实模型 key。若要调用真实 LLM/embedding/VLM，再配置：

```bash
cp .env.example .env
```

推荐轻量测试时临时关闭远程能力，避免网络和 key 影响结果：

```bash
export OPENAI_API_KEY=
export OAPI_API_KEY=
export RAG_ENABLE_REAL_EMBEDDING=false
export RAG_ENABLE_MULTIMODAL_EMBEDDING=false
export RAG_ENABLE_LLM_ROUTER=false
export RAG_ENABLE_LLM_VERIFIER=false
```

## 2. 静态检查

```bash
bash -n scripts/lora/run_minicpm_lora_pipeline.sh
.venv/bin/python -m compileall src
.venv/bin/python -m py_compile \
  scripts/run_quality_eval.py \
  scripts/stage3_test_gate.py \
  scripts/smoke_test_qa.py \
  scripts/lora/prepare_sft_data.py \
  scripts/lora/train_minicpm_lora.py \
  scripts/lora/eval_lora_checkpoint.py \
  scripts/lora/lora_release_gate.py \
  scripts/lora/run_lora_experiment_matrix.py \
  scripts/lora/summarize_lora_eval.py \
  scripts/lora/merge_lora_adapter.py
```

通过标准：命令退出码为 0。

## 3. 轻量离线评测

不启动 HTTP 服务，直接构建 `data/demo_pages.json` 的 QAEngine：

```bash
OPENAI_API_KEY= OAPI_API_KEY= \
RAG_ENABLE_REAL_EMBEDDING=false \
RAG_ENABLE_MULTIMODAL_EMBEDDING=false \
RAG_ENABLE_LLM_ROUTER=false \
RAG_ENABLE_LLM_VERIFIER=false \
.venv/bin/python - <<'PY'
from src.bootstrap import build_engine
from src.eval_suite import DEFAULT_EVAL_SAMPLES, run_eval_report

engine = build_engine("data/demo_pages.json")
report = run_eval_report(engine)
print("sample_count", len(DEFAULT_EVAL_SAMPLES))
print("overall", report.overall.as_percent())
print("engineering", report.engineering)
PY
```

当前 demo 基线期望：

| 指标 | 期望 |
|---|---:|
| sample_count | 7 |
| recall_at_10 | 100% |
| accuracy | 100% |
| router_acc | 100% |

## 4. 本地启动 API

```bash
./run_offer.sh
```

或：

```bash
uvicorn offer_agent.api:app --host 0.0.0.0 --port 8000 --reload
```

健康检查：

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/capabilities | python -m json.tool
```

浏览器入口：

- `http://127.0.0.1:8000/chat`
- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/metrics`

## 5. API 冒烟测试

```bash
.venv/bin/python scripts/smoke_test_qa.py --base http://127.0.0.1:8000
```

通过标准：

- 所有用例 HTTP 200
- 闲聊问题不应有检索命中
- 业务问题应返回 `branch`、`answer`、`source_files`

## 6. Agentic RAG Trace 检查

普通业务问题：

```bash
curl -s http://127.0.0.1:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"query":"采购申请单的采购单号是多少？","topk":3,"session_id":"trace-ok"}' \
  | python -m json.tool
```

重点检查字段：

| 字段 | 说明 |
|---|---|
| `trace.stages[].stage=retrieve` | 应包含 `query_variants`、`fusion`、`doc_type` |
| `hits[].source_file` | 命中页来源 |
| `citations[].excerpt` | 页级证据片段 |
| `verified` | 是否通过可证性校验 |

低置信度问题：

```bash
curl -s http://127.0.0.1:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"query":"不存在的字段在哪里？","topk":3,"session_id":"trace-low"}' \
  | python -m json.tool
```

通过标准：

- `answer` 应说明材料不足
- `trace.stages` 应包含 `agentic_critique`
- `agentic_critique.detail.retry_query` 应存在
- `retry_hits` 应保留第二轮检索结果，便于复盘

## 7. Stage-3 提测门禁

```bash
.venv/bin/python scripts/stage3_test_gate.py --base http://127.0.0.1:8000
```

该脚本检查：

1. `/health`
2. `/ask` trace
3. `/eval/run` 落盘
4. `/eval/last` 读取

通过标准：输出 `Stage-3 提测门禁通过。`

## 8. 质量集在线回归

完整质量集：

```bash
.venv/bin/python scripts/run_quality_eval.py \
  --base http://127.0.0.1:8000 \
  --topk 3 \
  --output logs/quality_eval_report.json
```

只跑某一类：

```bash
.venv/bin/python scripts/run_quality_eval.py \
  --base http://127.0.0.1:8000 \
  --category robustness \
  --fail-fast
```

只跑指定样本：

```bash
.venv/bin/python scripts/run_quality_eval.py \
  --base http://127.0.0.1:8000 \
  --ids F001,F002,R001
```

报告关注：

| 字段 | 说明 |
|---|---|
| `summary.pass_rate` | 总通过率 |
| `summary.by_category` | 分类通过率 |
| `results[].reasons` | 失败原因 |
| `results[].answer_preview` | 答案预览 |

## 9. LoRA 微调链路检查

### 9.1 准备 SFT 数据

```bash
.venv/bin/python scripts/lora/prepare_sft_data.py \
  --pages data/demo_pages.json \
  --quality data/rag_quality_testset.json \
  --output-train /tmp/lora-train.jsonl \
  --output-val /tmp/lora-val.jsonl
```

检查：

```bash
wc -l /tmp/lora-train.jsonl /tmp/lora-val.jsonl
head -n 3 /tmp/lora-train.jsonl
```

说明：

- 默认不会把 `expected_behavior` 当训练答案。
- 如只是实验需要行为类样本，可显式加 `--include-behavior-cases`。

### 9.2 训练 dry-run

无 GPU 或未安装训练依赖时，先做配置和数据校验：

```bash
.venv/bin/python scripts/lora/train_minicpm_lora.py \
  --config configs/lora/minicpm_v26_qlora.yaml \
  --dry-run
```

如果使用 `/tmp` 数据，可生成临时配置后 dry-run：

```bash
python - <<'PY'
from pathlib import Path
import yaml

cfg = yaml.safe_load(Path("configs/lora/minicpm_v26_qlora.yaml").read_text(encoding="utf-8"))
cfg["dataset_path"] = "/tmp/lora-train.jsonl"
cfg["val_dataset_path"] = "/tmp/lora-val.jsonl"
cfg["output_dir"] = "/tmp/lora-dry-run-out"
Path("/tmp/lora-dry-run.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
PY

.venv/bin/python scripts/lora/train_minicpm_lora.py \
  --config /tmp/lora-dry-run.yaml \
  --dry-run
```

### 9.3 生成实验矩阵

```bash
.venv/bin/python scripts/lora/run_lora_experiment_matrix.py \
  --ranks 8,16,32 \
  --profiles attention_minimal,attention_full,attention_mlp \
  --output-dir artifacts/lora/experiment_matrix
```

产物：

- `artifacts/lora/experiment_matrix/experiment_matrix.json`
- `artifacts/lora/experiment_matrix/experiment_matrix.md`
- `artifacts/lora/experiment_matrix/configs/*.yaml`

### 9.4 真训练

需要 GPU 和训练依赖：

```bash
pip install transformers peft datasets accelerate bitsandbytes trl

.venv/bin/python scripts/lora/train_minicpm_lora.py \
  --config configs/lora/minicpm_v26_qlora.yaml
```

输出目录由 `configs/lora/minicpm_v26_qlora.yaml` 的 `output_dir` 控制。

### 9.5 base/LoRA 双服务对比

先分别启动 base 服务和 LoRA 服务，例如：

| 服务 | 地址 |
|---|---|
| base | `http://127.0.0.1:8000` |
| lora | `http://127.0.0.1:8010` |

执行对比：

```bash
.venv/bin/python scripts/lora/eval_lora_checkpoint.py \
  --base-url http://127.0.0.1:8000 \
  --lora-url http://127.0.0.1:8010 \
  --out-dir logs/lora_eval/minicpm-v26-domain-v1
```

### 9.6 LoRA 上线门禁

```bash
.venv/bin/python scripts/lora/lora_release_gate.py \
  --base-report logs/lora_eval/minicpm-v26-domain-v1/base_report.json \
  --lora-report logs/lora_eval/minicpm-v26-domain-v1/lora_report.json \
  --min-pass-delta 0.02 \
  --max-general-drop 0.02 \
  --max-latency-delta-ms 800
```

输出：

- `lora_release_gate.json`
- `lora_release_gate.md`
- 退出码 0 表示 PASS，退出码 1 表示 BLOCK

### 9.7 一键 LoRA 流水线

只评测：

```bash
BASE_URL=http://127.0.0.1:8000 \
LORA_URL=http://127.0.0.1:8010 \
STRICT_GATE=1 \
bash scripts/lora/run_minicpm_lora_pipeline.sh eval-only
```

训练并评测：

```bash
BASE_URL=http://127.0.0.1:8000 \
LORA_URL=http://127.0.0.1:8010 \
STRICT_GATE=1 \
bash scripts/lora/run_minicpm_lora_pipeline.sh train-and-eval
```

说明：

- `STRICT_GATE=1`：默认推荐，gate BLOCK 时脚本最终失败。
- `STRICT_GATE=0`：只保留报告，不阻断脚本，适合本地实验复盘。

## 10. Docker / 云端验证

低配 API：

```bash
cp .env.example .env
docker compose up --build
```

健康检查：

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/capabilities | python -m json.tool
```

监控栈：

```bash
bash scripts/smoke_monitoring_stack.sh
```

vLLM / GPU 栈：

```bash
bash scripts/smoke_vllm_stack.sh
```

## 11. 企业验收 Checklist

| 项 | 命令/入口 | 通过标准 |
|---|---|---|
| 服务健康 | `/health` | `{"status":"ok"}` |
| 能力开关 | `/capabilities` | Agentic、session、rerank 等状态符合部署预期 |
| 闲聊拦截 | `/ask: 你好` | 无 hits，不检索文档 |
| 事实问答 | `/ask: 采购申请单的采购单号是多少？` | 答案正确，有 citations |
| 图表问答 | `/ask: 产品线B销售额是多少？` | 答案正确，有 chart 分支或正确证据 |
| 低置信拒答 | `/ask: 不存在的字段在哪里？` | 有 `agentic_critique` 和 `retry_query` |
| 质量集 | `scripts/run_quality_eval.py` | pass_rate 达到约定阈值 |
| 提测门禁 | `scripts/stage3_test_gate.py` | 全部通过 |
| LoRA dry-run | `train_minicpm_lora.py --dry-run` | 配置和数据校验通过 |
| LoRA release gate | `lora_release_gate.py` | PASS 才能进入灰度 |

## 12. 常见问题

### 12.1 `/ask` 很慢

检查是否开启真实 embedding、LLM router、LLM verifier 或 VLM：

```bash
curl -s http://127.0.0.1:8000/capabilities | python -m json.tool
```

轻量测试时关闭远程能力。

### 12.2 质量集失败但答案看起来正确

查看报告里的 `reasons` 和 `answer_preview`。若是短字段值带有“依据文档”前缀导致误判，应调整评测金标或 `eval_metrics.py` 的匹配逻辑。

### 12.3 LoRA gate BLOCK

查看：

- `lora_release_gate.md`
- `lora_release_gate.json`
- `lora_failed_cases`

常见处理：

- 降低 rank
- 缩小 target modules
- 降低 learning rate
- 增加 replay/general 样本
- 清理重复或模板化 SFT 样本

### 12.4 文档更新后答案不变

确认是否重新建库并重启 API：

```bash
python scripts/build_index_incremental.py \
  --input-dir user_docs \
  --output-pages data/user_pages.json \
  --manifest data/index_manifest.json \
  --image-dir kb_pages \
  --lang zh
```

然后重启服务。

## 13. 推荐提交流程

```bash
git status --short
.venv/bin/python -m compileall src
bash -n scripts/lora/run_minicpm_lora_pipeline.sh
.venv/bin/python -m py_compile scripts/*.py scripts/lora/*.py
```

若本地服务已启动，再跑：

```bash
.venv/bin/python scripts/smoke_test_qa.py --base http://127.0.0.1:8000
.venv/bin/python scripts/stage3_test_gate.py --base http://127.0.0.1:8000
```

提交前确认没有把 `private/`、`.env`、`reports/`、真实客户资料提交。
