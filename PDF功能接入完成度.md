# PDF 功能接入完成度

> 目标：将《RAG 项目完整介绍 简历包装.pdf》中的企业级视觉 RAG + Agent 能力完整映射到当前工程，并记录当前可验证精度。

## 结论

当前工程已经完成 PDF 主链路的模块级接入：
L0 页级检索、
L1 Router Agent、
L2 生成工具链、Verifier、Session Memory、Milvus/Redis 可选后端、FastAPI 服务化、Prometheus 指标、七类文档评测指标。

外部依赖型能力已经按企业接入方式预留标准配置和适配层。未配置私有服务或 API Key 时，系统会自动回退到本地可运行路径。

## PDF 功能对齐表

| PDF 功能 | 当前接入状态 | 工程模块 | 说明 |
| --- | --- | --- | --- |
| PDF/PPT 页图统一编码 | 已接入接口 | `src/services.py`, `src/retriever.py` | 支持 `RAG_MULTIMODAL_EMBEDDING_API`，可接 ColPali / MiniCPM-V 页图 embedding 服务 |
| Text query embedding | 已接入 | `src/llm_client.py`, `src/services.py`, `src/retriever.py` | 优先多模态 embedding 服务，其次 OpenAI embedding，最后本地哈希降级 |
| Milvus 向量库 | 已接入 | `src/infra/vector_store.py`, `src/retriever.py` | 支持 `RAG_VECTOR_BACKEND=milvus`，无 Milvus 环境自动回退内存向量库 |
| ColPali 本地模型 | 已下载 | `models/colpali-v1.3`, `scripts/download_colpali_model.py` | 本地模型为 `vidore/colpali-v1.3`，已落盘到 `models/colpali-v1.3` |
| ColPali late-interaction rerank | 已接入接口 | `src/services.py`, `src/retriever.py` | 支持 `RAG_COLPALI_RERANK_API`，用于 top-k 候选页重排；本地推理服务可基于已下载模型继续封装 |
| Query rewrite | 已实现 | `src/retriever.py` | 当前包含业务术语替换，可继续扩展 LLM 改写和企业词典 |
| 文档类型预过滤 | 已实现 | `src/retriever.py` | 支持表单、报表、PPT、手册等类型推断 |
| GPT-4o-mini function calling Router | 已接入 | `src/llm_client.py`, `src/router.py` | 优先 function calling，失败后 LLM 文本路由，再失败走规则兜底 |
| 四分支工具编排 | 已实现 | `src/tools.py`, `src/pipeline.py` | `fact_qa` / `multi_page_qa` / `chart_qa` / `translate_qa` |
| 单图 VLM 问答 | 已接入接口 | `src/services.py`, `src/tools.py` | 支持 `RAG_VLM_API`，未配置时走结构化字段和文本规则 |
| 多图 VLM 跨页推理 | 已接入接口 | `src/services.py`, `src/tools.py` | 支持多页图像路径输入，未配置时走 people 聚合规则 |
| chart-parsing 数值解析 | 已接入接口 | `src/services.py`, `src/tools.py` | 支持 `RAG_CHART_PARSING_API`，未配置时走 `chart_data` |
| 图表数值校验 | 部分完成 | `src/tools.py`, `src/verifier.py` | 已有结构化数值读取和答案可证性校验，真实坐标轴解析依赖外部 chart 服务 |
| Google Translate | 已接入接口 | `src/services.py`, `src/tools.py` | 支持 `GOOGLE_TRANSLATE_API` 和 `GOOGLE_TRANSLATE_API_KEY` |
| DeepL | 已接入接口 | `src/services.py`, `src/tools.py` | 支持 `DEEPL_API_KEY` |
| GPT 翻译 | 已接入 | `src/llm_client.py`, `src/tools.py` | 支持 OpenAI 兼容模型，未配置时走本地翻译候选 |
| 翻译多引擎并行选优 | 已实现 | `src/tools.py` | 并行 Google / DeepL / GPT 候选，按术语覆盖评分选优 |
| Verifier 可证性校验 | 已实现 | `src/verifier.py`, `src/pipeline.py` | 优先 VLM verifier，其次 LLM verifier，最后关键词规则 |
| 未通过扩 top-k 重试 | 已实现 | `src/pipeline.py`, `src/agent_loop.py` | 支持 `RAG_TOPK_RETRY_MULTIPLIER` |
| Session Memory | 已实现 | `src/memory.py` | 记录历史 QA 和命中页 |
| Redis Memory | 已接入 | `src/infra/redis_memory.py`, `src/bootstrap.py` | 支持 `RAG_SESSION_BACKEND=redis` 和 TTL |
| PyMuPDF 建库入口 | 已增强 | `src/infra/pdf_ingest.py` | 支持 PDF 文本抽取和 200 DPI 页图渲染落盘 |
| 多格式文档建库 | 已增强 | `src/infra/document_ingest.py`, `scripts/build_index_incremental.py` | 支持递归扫描 PDF / XLSX / DOCX / PPTX，并兼容 CSV / TXT；旧版 DOC/XLS/PPT 可通过 LibreOffice 转 PDF |
| FastAPI 问答服务 | 已实现 | `src/api.py`, `src/interfaces/api.py` | `/ask`、`/health`、`/metrics` |
| 能力自检接口 | 已新增 | `src/api.py` | `/capabilities` 返回外部服务接入状态和基准指标 |
| Prometheus 指标 | 已实现 | `src/api.py` | 请求数、耗时、fallback 计数 |
| Recall@10 | 已实现 | `src/eval_metrics.py`, `src/eval_suite.py` | 支持离线评测集批量计算 |
| Accuracy | 已实现 | `src/eval_metrics.py`, `src/eval_suite.py` | 归一化 exact match |
| Router 决策准确率 | 已实现 | `src/eval_metrics.py`, `src/eval_suite.py` | 对比预测分支和金标分支 |
| 翻译引擎选择准确率 | 已实现指标 | `src/eval_metrics.py` | 需要翻译评测集接入后批量计算 |
| 七类文档评测任务 | 部分完成 | `src/eval_suite.py` | 当前覆盖报表、表单、PPT、跨语种；图表专项、信息图、看板需补充真实样本 |

## 当前精度记录

PDF 中给出的企业级目标 / 历史实验指标：

| 指标 | PDF 目标值 |
| --- | --- |
| 七类文档平均 Recall@10 | 89.40% |
| 七类文档端到端 Accuracy | 58.70% |
| Router 决策准确率 | 92.00% |
| translate_qa 通用 domain Accuracy | 80.60% |
| translate_qa 公司术语 domain Accuracy | 70.40% |
| 信息图 / 宣传类 Accuracy | TextRAG 约 25%，页图 + Agent 约 51% |
| 离线单页处理耗时 | OCR 链路约 312ms，页图编码约 121ms |
| 在线 query 编码 + Milvus 检索 | 约 54ms |

当前工程可验证指标：

| 指标 | 当前状态 | 说明 |
| --- | --- | --- |
| 小样本 Recall@10 | 100.00% | 由 `src/eval_suite.py` 在本地 4 条样本上计算 |
| 小样本 Accuracy | 100.00% | 由 `src/eval_suite.py` 在本地 4 条样本上计算 |
| 小样本 Router Accuracy | 100.00% | 由 `src/eval_suite.py` 在本地 4 条样本上计算 |
| 小样本翻译引擎选择准确率 | 未计算 | 当前默认样本未提供 offline best engine 标注，指标函数已在 `src/eval_metrics.py` 实现 |
| 企业级七类 Recall@10 | 接口已具备，需接真实评测集 | 当前仓库没有十万级文档与完整金标集 |
| 企业级七类 Accuracy | 接口已具备，需接真实评测集 | 当前仓库没有完整 VLM 服务与七类标注数据 |
| 单页 121ms 处理耗时 | 需接真实 embedding 服务压测 | 当前已提供 PyMuPDF 渲染和 embedding 接口 |
| Milvus 54ms 检索耗时 | 需接 Milvus 环境压测 | 当前已提供 Milvus 后端配置 |

## 环境变量

核心接入配置如下：

```bash
export RAG_MULTIMODAL_EMBEDDING_API="http://embedding-service/embed"
export RAG_COLPALI_RERANK_API="http://rerank-service/rerank"
export RAG_VLM_API="http://vlm-service/qa"
export RAG_CHART_PARSING_API="http://chart-service/parse"

export RAG_VECTOR_BACKEND="milvus"
export MILVUS_URI="http://localhost:19530"
export MILVUS_COLLECTION="kb_pages"

export RAG_SESSION_BACKEND="redis"
export REDIS_URL="redis://localhost:6379/0"

export OPENAI_API_KEY="..."
export RAG_ENABLE_FUNCTION_CALLING_ROUTER=true
export RAG_ENABLE_LLM_VERIFIER=true
export RAG_ENABLE_LLM_TRANSLATION=true

export GOOGLE_TRANSLATE_API="http://google-translate-proxy/translate"
export GOOGLE_TRANSLATE_API_KEY="..."
export DEEPL_API_KEY="..."
export OAPI_CHAT_COMPLETIONS_URL="https://oapi.uk/v1/chat/completions"
export OAPI_API_KEY="..."

export COLPALI_MODEL_ID="vidore/colpali-v1.3"
export COLPALI_MODEL_DIR="models/colpali-v1.3"
```

## 文件方式目录约定（当前推荐）

当前阶段采用“文档放本地目录 + 增量建库脚本”的方式，目录建议如下：

```text
（仓库根目录）
├─ user_docs/                    # 你手工上传/拷贝的原始文档（pdf/xlsx/docx/pptx 等，支持子目录）
├─ kb_pages/                     # 建库时渲染出来的页图（按 doc_id_page 命名）
├─ data/
│  ├─ user_pages.json            # 页面级索引（问答引擎实际读取）
│  ├─ index_manifest.json        # 增量状态（mtime/size）
│  └─ demo_pages.json            # 演示兜底数据
├─ logs/
│  ├─ api.log                    # FastAPI 日志
│  ├─ colpali.log                # ColPali rerank 服务日志
│  └─ cloudflared.log            # 公网隧道日志（可选）
├─ scripts/
│  ├─ build_index_incremental.py # 增量建库
│  ├─ colpali_rerank_service.py  # 本地 ColPali rerank 服务
│  └─ one_click_demo.sh          # 一键启动脚本
└─ web/
   └─ chat.html                  # 聊天页面（当前为日间风格）
```

文档上传方式：把 PDF / XLSX / DOCX / PPTX 放入 `user_docs/` 任意子目录，再执行增量建库脚本：

```bash
python scripts/build_index_incremental.py \
  --input-dir user_docs \
  --output-pages data/user_pages.json \
  --manifest data/index_manifest.json \
  --image-dir kb_pages \
  --lang zh
```

## ColPali 模型如何使用

ColPali 在本项目里主要用于“视觉文档检索”和“候选页重排”，它不是普通的单向量文本 embedding 模型，而是基于 PaliGemma + ColBERT late interaction 的多向量视觉检索模型。

使用位置分两段：

1. 离线建库阶段：PDF / PPT 页面先通过 PyMuPDF 或 Office 转图能力渲染成页面图片，然后用 ColPali 对每一页图片生成 image multi-vector 表示，保存页面 ID、图片路径、文档类型、页码等元数据。
2. 在线检索阶段：用户 query 用 ColPali 编码成 query multi-vector，再和候选页面的 image multi-vector 做 late interaction 打分，用于召回或 rerank。当前工程里预留在 `src/retriever.py` 和 `src/services.py`，通过 `RAG_COLPALI_RERANK_API` 接入。

本地模型已经下载到：

```bash
models/colpali-v1.3
```

如果要在本地直接加载，需要安装 ColPali 推理依赖：

```bash
pip install "colpali-engine>=0.3.0,<0.4.0" torch pillow
```

最小加载方式如下：

```python
import torch
from PIL import Image
from colpali_engine.models import ColPali, ColPaliProcessor

model_path = "models/colpali-v1.3"

model = ColPali.from_pretrained(
    model_path,
    torch_dtype=torch.bfloat16,
    device_map="mps",  # Apple Silicon 可用 mps；NVIDIA 机器可改成 cuda:0
).eval()

processor = ColPaliProcessor.from_pretrained(model_path)

images = [Image.open("kb_pages/example_page.png").convert("RGB")]
queries = ["采购申请单的采购单号是多少？"]

batch_images = processor.process_images(images).to(model.device)
batch_queries = processor.process_queries(queries).to(model.device)

with torch.no_grad():
    image_embeddings = model(**batch_images)
    query_embeddings = model(**batch_queries)

scores = processor.score_multi_vector(query_embeddings, image_embeddings)
print(scores)
```

工程化落地时建议把这段本地推理封装成一个独立服务，例如：

```bash
RAG_COLPALI_RERANK_API=http://127.0.0.1:9001/rerank
```

当前工程已经提供本地 ColPali rerank 服务：

```bash
cd Agent
pip install -r requirements-colpali.txt
uvicorn scripts.colpali_rerank_service:app --host 127.0.0.1 --port 9001
```

然后在 `.env` 中打开开关：

```bash
RAG_ENABLE_COLPALI_RERANK=true
RAG_COLPALI_RERANK_API=http://127.0.0.1:9001/rerank
COLPALI_MODEL_DIR=models/colpali-v1.3
```

主检索链路中的使用方式是：

1. `src/retriever.py` 先用向量检索 + 词面匹配得到候选页。
2. 如果候选页带有 `image_path`，则把 `query + page_id + image_path` 发给 `RAG_COLPALI_RERANK_API`。
3. `scripts/colpali_rerank_service.py` 加载本地 `models/colpali-v1.3`，用 ColPali 对 query 和页面图像做 multi-vector late interaction 打分。
4. `src/retriever.py` 将原始召回分和 ColPali rerank 分做融合，重新排序后返回 top-k。

这样主问答服务只负责调用 rerank API，不直接持有大模型，更接近生产部署：索引服务、检索服务、VLM 服务和问答编排服务可以独立扩缩容。

## 更新记录（本次会话）

> 说明：以下为本次从“可跑 demo”到“一键可分享 demo”的关键更新，便于复盘。

1. 完成企业级能力接口接入：多模态 embedding / ColPali rerank / VLM / chart-parsing / 翻译引擎 / function-calling router。
2. 新增 ColPali 本地模型下载脚本，模型落盘到 `models/colpali-v1.3`。
3. 新增本地 ColPali rerank 服务：`scripts/colpali_rerank_service.py`，并在检索链路中接入页图重排。
4. 修复 Python 3.9 类型注解兼容问题（`str | None` -> `Optional[str]`）。
5. 修复 FastAPI 跨域问题，增加 CORS 中间件，支持独立 HTML 调 `/ask`。
6. 增加 `/` 与 `/chat` 页面路由，FastAPI 可直接托管聊天页。
7. 新增增量建库脚本：`scripts/build_index_incremental.py`，支持新增/变更文档增量处理，避免全量重算。
8. 新增一键启动脚本：`scripts/one_click_demo.sh`，自动完成依赖安装、Redis 启动、增量建库、服务拉起、健康检查、公网隧道（可选）。
9. 增强一键脚本端口冲突处理（自动回收 8000/9001 占用）。
10. 更新聊天页面为更产品化 UI，并改为明显日间风格（当前版本）。
11. 增强增量建库脚本为多格式入口：递归支持 PDF / XLSX / DOCX / PPTX，同时支持 CSV / TXT，旧版 DOC / XLS / PPT 可借助 LibreOffice 转 PDF 后入库。
12. 增强一键启动脚本：自动统计 `user_docs` 下可建库文档，不再只判断根目录 PDF。
13. 一键脚本默认启用本地 ColPali rerank；聊天页折叠展示运行详情并支持导出浏览器端会话 JSON；`fact_qa` / `multi_page_qa` 在规则抽取前优先用 LLM 基于检索正文生成较长回答。

## 剩余差距

1. 真实十万级文档、七类完整金标评测集不在当前仓库内，需要接入企业数据源后才能复现 PDF 中的全量 Recall@10 和 Accuracy。
2. ColPali 模型已下载并已封装成本地 rerank 推理服务；MiniCPM-V / VLM / chart-parsing 仍以 HTTP 服务方式接入。
3. Google / DeepL 真实效果依赖外部 API Key，当前无密钥时使用本地候选翻译保证链路可运行。
4. 权限 ACL、审计日志、增量同步、异步建库任务队列和大规模压测报告仍属于生产运维层，需要在真实企业环境补齐。
