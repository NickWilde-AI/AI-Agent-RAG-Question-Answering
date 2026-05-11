# AI-Agent-RAG-Question-Answering

企业向视觉 RAG / Agent 问答演示工程，核心代码在 **`Agent/`** 目录。

## 快速开始

```bash
cd Agent
cp .env.example .env   # 填入 API Key 等，勿提交 .env
./scripts/one_click_demo.sh
```

浏览器打开 `http://127.0.0.1:8000/chat` 。默认脚本为轻量模式（不启 ColPali、不逐页远程 embedding），详见 `Agent/README.md`。

## 仓库说明

- 本仓库**不包含**：`.env`、本地知识库目录 `Agent/user_docs/`、页图 `Agent/kb_pages/`、个人索引 `user_pages.json` 等（见根目录 `.gitignore`）。
- 演示用页面数据：`Agent/data/demo_pages.json`。
