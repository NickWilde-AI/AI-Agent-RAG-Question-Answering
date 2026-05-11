# AI-Agent-RAG-Question-Answering

企业向视觉 RAG / Agent 问答演示工程，**代码在仓库根目录**（`src/`、`offer_agent/`、`scripts/` 等）。

## 快速开始

```bash
cp .env.example .env   # 填入 API Key 等，勿提交 .env
./scripts/one_click_demo.sh
```

浏览器打开 `http://127.0.0.1:8000/chat`。本地开发也可 `./run_offer.sh`（需已配置 `.env`）。

更完整的面试说明与架构表见 **`README-offer-interview.md`**。

## 仓库说明

- 本仓库**不包含**：`.env`、`user_docs/`、`kb_pages/`、`data/user_pages.json` 等（见 `.gitignore`）。
- 演示用页面数据：`data/demo_pages.json`。

## 目录结构（摘要）

| 路径 | 说明 |
|------|------|
| `src/` | 检索、路由、工具链、Pipeline、API 实现等 |
| `offer_agent/` | Uvicorn 入口（`offer_agent.api:app`） |
| `scripts/` | 一键启动、建库、ColPali 服务、冒烟测试 |
| `web/chat.html` | 聊天前端 |
| `data/demo_pages.json` | 离线演示页面索引 |
