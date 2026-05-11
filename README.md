# AI-Agent-RAG-Question-Answering

基于 **FastAPI** 的视觉 RAG 问答演示：检索（向量 + 词面混合）→ 路由 → 多工具（事实 / 跨页 / 图表 / 翻译）→ 校验 → 可选 Plan-Execute 循环。默认配置偏 **轻量**，避免大库逐页远程 embedding 与本地 ColPali 把机器拖死。

**运行与建库前，请始终在仓库根目录执行命令**（与 `Agent/` 子目录无关）。

---

## 环境要求

- Python 3.9+（与当前依赖一致即可）
- 若使用「全量一键」里的 ColPali / Redis：需要本机 Docker（仅 `RAG_LITE_MODE=0` 时会起 Redis）

---

## 1. 配置密钥（必做）

```bash
cp .env.example .env
```

编辑 `.env`，至少配置 **OpenAI 兼容** 的调用方式（问答里 `fact_qa` / 多页归纳等会走 LLM）：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`（如使用中转，填对应 `https://.../v1`）
- `OPENAI_CHAT_MODEL`

`.env.example` 中其余 `RAG_*` 默认多为 **关闭**（轻量模式）；需要真实 embedding、ColPali rerank、多轮 Loop 时再逐项打开，详见 `README-offer-interview.md` 与 `PDF功能接入完成度.md`。

**不要将 `.env` 提交到 Git**（已在 `.gitignore` 中）。

---

## 2. 启动服务（二选一）

### 方式 A：一键脚本（推荐首次与演示）

在**仓库根目录**：

```bash
bash scripts/one_click_demo.sh
```

- 默认 **`RAG_LITE_MODE=1`**：创建/使用 `.venv`、安装 `requirements.txt`、**不**装 ColPali 依赖、**不**起 Docker Redis、**不**起 ColPali 进程；若 `user_docs/` 下有文档则增量建库并写入 `data/user_pages.json`，否则用 `data/demo_pages.json`；后台启动 API（`http://0.0.0.0:8000`）。
- 全量（ColPali + Redis）：`RAG_LITE_MODE=0 bash scripts/one_click_demo.sh`

脚本内部会先 `cd` 到仓库根，因此也可写成 `bash ./scripts/one_click_demo.sh`。

### 方式 B：仅起 API（适合改代码联调）

在**仓库根目录**：

```bash
./run_offer.sh
```

会：若无则创建 `.venv`、`pip install -r requirements.txt`、`source .env`、前台 **`uvicorn offer_agent.api:app --reload`**（默认 `http://127.0.0.1:8000`）。

入口模块为 `offer_agent/api.py`，实际加载的是 `src.interfaces.api` 中的 `app`。

---

## 3. 打开页面与接口

| 地址 | 说明 |
|------|------|
| http://127.0.0.1:8000/chat | 聊天前端（`web/chat.html`） |
| http://127.0.0.1:8000/docs | Swagger |
| http://127.0.0.1:8000/health | 健康检查 |
| http://127.0.0.1:8000/metrics | Prometheus 指标 |

服务端日志默认写在仓库根下 **`logs/api.log`**（一键脚本会创建 `logs/`）。

---

## 4. 自建知识库（可选）

将 PDF / XLSX / DOCX / PPTX 等放入 **`user_docs/`**（可子目录），在仓库根目录执行：

```bash
source .venv/bin/activate
python scripts/build_index_incremental.py \
  --input-dir user_docs \
  --output-pages data/user_pages.json \
  --manifest data/index_manifest.json \
  --image-dir kb_pages \
  --lang zh
```

API 启动时：若存在 **`data/user_pages.json`** 则优先加载，否则使用 **`data/demo_pages.json`**（见 `src/api.py` 中数据路径逻辑）。

---

## 5. 其他命令

**离线跑 demo + 简化评测**（需在仓库根目录，且已安装依赖）：

```bash
source .venv/bin/activate
python main.py
```

**对 `/ask` 冒烟**（需 API 已启动）：

```bash
python scripts/smoke_test_qa.py --base http://127.0.0.1:8000
```

---

## 6. 目录说明（与代码一致）

| 路径 | 作用 |
|------|------|
| `src/` | 配置、检索、路由、工具、`pipeline`、`api` 等核心业务 |
| `offer_agent/` | Uvicorn 包入口，`offer_agent.api:app` |
| `scripts/` | `one_click_demo.sh`、`build_index_incremental.py`、ColPali 服务、冒烟脚本 |
| `web/chat.html` | 内置聊天页 |
| `data/demo_pages.json` | 随仓库提供的演示索引 |
| `user_docs/`、`kb_pages/`、`data/user_pages.json` | 本地资料与建库产物，**默认不入库**（见 `.gitignore`） |

---

## 7. 更多文档

- **`README-offer-interview.md`**：面试叙事、架构图、简历技术点与代码对应表（部分路径仍写为 `src/engine/...`，与当前 `src/` 下双轨模块并存，以实际文件为准）。
- **`PDF功能接入完成度.md`**：多格式建库、ColPali、能力开关等落地清单。

---

## 8. 克隆他人仓库后

```bash
git clone https://github.com/NickWilde-AI/AI-Agent-RAG-Question-Answering.git
cd AI-Agent-RAG-Question-Answering
cp .env.example .env
# 编辑 .env 填入 Key
bash scripts/one_click_demo.sh
```
