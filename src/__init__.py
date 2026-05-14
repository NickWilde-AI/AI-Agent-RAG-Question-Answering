"""
src — 视觉 RAG + Agent 演示包（**真源实现**所在命名空间）

================================================================================
【简历第一条：检索 → 路由 → 生成 → 校验 → 重试 —— 阅读顺序建议】
================================================================================
1. `config.py`        — 环境开关
2. `bootstrap.py`     — 组装 QAEngine
3. `pipeline.py`      — **QAEngine.ask** 主链路（必读）
4. `agent_loop.py`    — 可选外层扩 top-k 循环
5. `api.py`           — HTTP `/ask` 入口
6. `retriever.py` / `router.py` / `tools.py` / `verifier.py` / `memory.py`
7. `services.py` + `infra/vector_store.py` — 外部服务与向量库

兼容转发：`engine/*.py`、`core/*.py`、`interfaces/api.py` 多指向以上真源。

【Android 背景】可把 `QAEngine` 当单一 UseCase，`tools` 当多下游 RPC。

视觉 RAG Agent 演示版源码包。
"""

