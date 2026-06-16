#!/usr/bin/env bash
# 阿里云 GPU 机一键安装入口（在服务器上以 root 执行这一条即可）
# curl -fsSL https://raw.githubusercontent.com/NickWilde-AI/AI-Agent-RAG-Question-Answering/main/deploy/install.aliyun.sh | bash
set -euo pipefail
export DATA_ROOT="${DATA_ROOT:-/data}"
SCRIPT_URL="${SCRIPT_URL:-https://raw.githubusercontent.com/NickWilde-AI/AI-Agent-RAG-Question-Answering/main/scripts/deploy_aliyun_gpu.sh}"
curl -fsSL "$SCRIPT_URL" -o /tmp/deploy_aliyun_gpu.sh
chmod +x /tmp/deploy_aliyun_gpu.sh
exec bash /tmp/deploy_aliyun_gpu.sh "$@"
