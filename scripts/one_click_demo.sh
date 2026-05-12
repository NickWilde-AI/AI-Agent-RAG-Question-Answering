#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# 默认轻量模式（普通模式）：不拉 ColPali 依赖、不启 ColPali 服务、不启 Docker Redis，
# 并在启动 API 时强制关闭「逐页远程 embedding / 多模态 embedding / ColPali / Plan Loop」，
# 避免大库启动或问答时把整机拖死。
# 需要 ColPali + Redis 全量链路时：RAG_LITE_MODE=0 bash scripts/one_click_demo.sh
: "${RAG_LITE_MODE:=1}"

# pip：访问 pypi.org 若出现 SSLEOFError / 超时，默认走清华镜像；强制官方源：RAG_USE_PYPI_MIRROR=0
: "${RAG_USE_PYPI_MIRROR:=1}"
PIP_INDEX_ARGS=()
if [ "${RAG_USE_PYPI_MIRROR}" = "1" ]; then
  : "${RAG_PIP_MIRROR:=https://pypi.tuna.tsinghua.edu.cn/simple}"
  PIP_INDEX_ARGS=(-i "${RAG_PIP_MIRROR}" --trusted-host pypi.tuna.tsinghua.edu.cn)
  echo "== pip 使用镜像: ${RAG_PIP_MIRROR}（官方源请设 RAG_USE_PYPI_MIRROR=0）=="
fi

mkdir -p logs

free_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "端口 $port 被占用，正在回收进程: $pids"
    kill -9 $pids >/dev/null 2>&1 || true
    sleep 1
  fi
}

echo "== 准备 Python 环境（RAG_LITE_MODE=${RAG_LITE_MODE}）=="
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install -U pip "${PIP_INDEX_ARGS[@]}" >/dev/null
python -m pip install -r requirements.txt "${PIP_INDEX_ARGS[@]}" >/dev/null
python -m pip install socksio "${PIP_INDEX_ARGS[@]}" >/dev/null
if [ "${RAG_LITE_MODE}" = "0" ]; then
  python -m pip install -r requirements-colpali.txt "${PIP_INDEX_ARGS[@]}" >/dev/null
fi

if [ "${RAG_LITE_MODE}" != "0" ]; then
  echo "== Redis：跳过（轻量模式；会话请使用 RAG_SESSION_BACKEND=memory）=="
else
  echo "== 启动 Redis =="
  if docker ps -a 2>/dev/null | grep -q " rag-redis$"; then
    docker start rag-redis >/dev/null || true
  else
    docker run -d --name rag-redis -p 6379:6379 redis:7 >/dev/null
  fi
fi

echo "== 载入环境变量 =="
set -a
# shellcheck disable=SC1091
source .env
set +a

mkdir -p user_docs kb_pages data

: "${RAG_SKIP_INDEX_BUILD:=0}"

echo "== 增量建库（递归处理 PDF / XLSX / DOCX / PPT）=="
doc_count="$(python - <<'PY'
from pathlib import Path
exts = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".pptx", ".ppt", ".txt"}
root = Path("user_docs")
print(sum(1 for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts))
PY
)"
if [ "${RAG_SKIP_INDEX_BUILD}" = "1" ]; then
  echo "已跳过（RAG_SKIP_INDEX_BUILD=1），使用已有 data/user_pages.json 或 demo_pages.json"
elif [ "$doc_count" -gt 0 ]; then
  echo "发现可建库文档 $doc_count 个，开始增量处理…（大 PDF 多时可能需数分钟）"
  # 页图 DPI：默认 144；高质量：RAG_BUILD_DPI=200 …（:- 避免 set -u 下 .env 未声明时报错）
  python scripts/build_index_incremental.py \
    --input-dir user_docs \
    --output-pages data/user_pages.json \
    --manifest data/index_manifest.json \
    --image-dir kb_pages \
    --lang zh \
    --dpi "${RAG_BUILD_DPI:-144}"
else
  echo "未发现可建库文档，跳过增量建库，默认使用 data/demo_pages.json"
fi

if [ "${RAG_LITE_MODE}" != "0" ]; then
  echo "== ColPali：跳过（轻量模式不占 GPU；需要时请 RAG_LITE_MODE=0）=="
  pkill -f "uvicorn scripts.colpali_rerank_service:app" >/dev/null 2>&1 || true
else
  echo "== 启动 ColPali rerank 服务 =="
  pkill -f "uvicorn scripts.colpali_rerank_service:app" >/dev/null 2>&1 || true
  free_port 9001
  nohup .venv/bin/python -m uvicorn scripts.colpali_rerank_service:app --host 127.0.0.1 --port 9001 > logs/colpali.log 2>&1 &
fi

echo "== 启动 FastAPI 主服务 =="
pkill -f "uvicorn offer_agent.api:app" >/dev/null 2>&1 || true
free_port 8000
if [ "${RAG_LITE_MODE}" != "0" ]; then
  # 强制覆盖 .env 里可能仍为 true 的重型开关，避免再次卡死
  nohup env \
    RAG_ENABLE_REAL_EMBEDDING=false \
    RAG_ENABLE_MULTIMODAL_EMBEDDING=false \
    RAG_ENABLE_COLPALI_RERANK=false \
    RAG_COLPALI_RERANK_API= \
    RAG_ENABLE_PLAN_EXECUTE_LOOP=false \
    .venv/bin/python -m uvicorn offer_agent.api:app --host 0.0.0.0 --port 8000 > logs/api.log 2>&1 &
else
  nohup env RAG_ENABLE_REAL_EMBEDDING=false RAG_ENABLE_MULTIMODAL_EMBEDDING=false \
    RAG_ENABLE_PLAN_EXECUTE_LOOP=false \
    RAG_ENABLE_COLPALI_RERANK=true RAG_COLPALI_RERANK_API=http://127.0.0.1:9001/rerank \
    .venv/bin/python -m uvicorn offer_agent.api:app --host 0.0.0.0 --port 8000 > logs/api.log 2>&1 &
fi

echo "等待服务启动..."
for _ in $(seq 1 30); do
  if curl -sS --max-time 2 "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
    if [ "${RAG_LITE_MODE}" != "0" ]; then
      break
    fi
    if curl -sS --max-time 2 "http://127.0.0.1:9001/health" >/dev/null 2>&1; then
      break
    fi
  fi
  sleep 1
done

echo "== 健康检查 =="
if [ "${RAG_LITE_MODE}" = "0" ]; then
  if ! curl -sS --max-time 5 "http://127.0.0.1:9001/health" >/dev/null; then
    echo "ColPali 服务启动失败，请看日志: logs/colpali.log"
    exit 1
  fi
fi
if ! curl -sS --max-time 5 "http://127.0.0.1:8000/health" >/dev/null; then
  echo "FastAPI 启动失败，请看日志: logs/api.log"
  exit 1
fi

echo ""
echo "✅ 本地服务已启动（RAG_LITE_MODE=${RAG_LITE_MODE}）"
echo " - Chat 页面: http://127.0.0.1:8000/chat"
echo " - API 文档 : http://127.0.0.1:8000/docs"
echo " - Health   : http://127.0.0.1:8000/health"
if [ "${RAG_LITE_MODE}" != "0" ]; then
  echo " - 模式说明: 轻量/普通模式（哈希检索 + 规则路由，无 ColPali 进程）"
else
  colpali_status="$(curl -sS --max-time 2 http://127.0.0.1:9001/health 2>/dev/null || true)"
  if echo "$colpali_status" | grep -q '"model_status":"ready"'; then
    echo " - ColPali : ready"
  else
    echo " - ColPali : 已启动但本地权重不完整时会自动降级（详见 logs/colpali.log）"
  fi
fi

if command -v cloudflared >/dev/null 2>&1; then
  echo ""
  echo "检测到 cloudflared，正在创建公网测试地址..."
  pkill -f "cloudflared tunnel --url http://127.0.0.1:8000" >/dev/null 2>&1 || true
  nohup cloudflared tunnel --url http://127.0.0.1:8000 > logs/cloudflared.log 2>&1 &
  for _ in $(seq 1 20); do
    url="$(python - <<'PY'
from pathlib import Path
import re
p = Path("logs/cloudflared.log")
if not p.exists():
    print("")
else:
    m = re.findall(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", p.read_text(encoding="utf-8", errors="ignore"))
    print(m[-1] if m else "")
PY
)"
    if [ -n "$url" ]; then
      echo "🌍 公网测试地址: $url/chat"
      echo "（任何人打开该链接即可访问聊天页面）"
      exit 0
    fi
    sleep 1
  done
  echo "cloudflared 已启动，但暂未抓到公网 URL，请查看 logs/cloudflared.log"
else
  echo ""
  echo "未安装 cloudflared。若要一键公网分享，请先安装："
  echo "  brew install cloudflared"
  echo "安装后重跑本脚本即可自动生成 trycloudflare 链接。"
fi
