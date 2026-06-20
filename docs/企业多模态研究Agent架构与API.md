# 企业多模态研究 Agent：架构、API 与运行边界

## 调用关系

```mermaid
flowchart LR
  W[Workspace / Documents] --> P[ResearchPlanner]
  P --> J[ResearchJob / Steps]
  J --> R[Tool Registry]
  R --> Q[现有 QAEngine]
  Q --> X[PageRetriever → Router → 三工具 → Verifier → 有限重试]
  X --> E[Evidence / Findings]
  E --> G[ReportGenerator]
  G --> M[Markdown 真源 + 安全 HTML]
```

研究层只负责持久状态和步骤编排。每个 workspace 执行前按其资料构造现有 `QAEngine`，因此即时问答与研究任务共用同一套检索、路由、工具和校验实现。资料正文标为 untrusted context；现有工具的系统提示明确禁止文档指令覆盖系统约束。

## 本地轻量运行

```bash
python -m venv .venv-new
source .venv-new/bin/activate
pip install -r requirements.txt
RAG_ENABLE_REAL_EMBEDDING=false RAG_ENABLE_MULTIMODAL_EMBEDDING=false \
RAG_ENABLE_COLPALI_RERANK=false RAG_VECTOR_BACKEND=inmemory \
RAG_SESSION_BACKEND=memory uvicorn offer_agent.api:app --port 8000
python scripts/smoke_test_research.py
```

创建与研究：

```bash
curl -s -X POST http://127.0.0.1:8000/workspaces -H 'Content-Type: application/json' \
  -d '{"name":"季度研究","use_demo":true}'
curl -s -X POST http://127.0.0.1:8000/research/jobs -H 'Content-Type: application/json' \
  -d '{"workspace_id":"<workspace_id>","objective":"对比关键指标并识别风险"}'
curl -s http://127.0.0.1:8000/research/jobs/<job_id>
curl -OJ http://127.0.0.1:8000/research/jobs/<job_id>/report.md
```

上传资料使用 `multipart/form-data` 字段 `file`：

```bash
curl -F 'file=@./sample.pdf' http://127.0.0.1:8000/workspaces/<workspace_id>/documents
```

删除 workspace 会级联删除 SQLite 中的文档、页面、任务和报告，并清理该空间的本地上传目录；存在活动任务时返回 `409 workspace_busy`，需先取消或等待完成。删除单文档同样会在活动任务期间拒绝，成功后删除页面索引、上传目录并使 workspace 引擎缓存失效。即时问答传 `workspace_id` 时限定到该空间；不传时保持原行为。

## 真实边界

- 已实现：SQLite 真源、规则规划、三个注册工具、异步任务、取消检查、页级证据、Markdown/HTML 报告、上传与空间隔离、内置数据 fallback。
- 可选配置：OpenAI-compatible 生成、Milvus、ColPali、Redis、LangGraph、VLM；不可用时走现有轻量实现。
- 默认进程内线程池负责本地任务调度；面向多副本部署时，可沿 `submit(job_id)` 接口替换为 Kafka、RabbitMQ 或云任务队列。
- SQLite 提供开箱即用的持久化后端；面向集群部署时可将 Repository 替换为 PostgreSQL/云数据库，并加入分布式锁。workspace 引擎直接从隔离后的内存 Page 集合构建并按资料版本缓存。
- 面向万人员工与十万级文档资料库，可进一步组合对象存储、分片向量检索、无状态 API、网关鉴权和分布式限流，并通过仓库内评测与监控入口建立容量基线。
- Planner 在 OpenAI-compatible LLM 可用时校验其结构化 JSON 计划，非法输出或服务不可用时使用确定性规则 fallback。生产向量索引的在线增删与跨进程恢复派发仍属后续规划。
