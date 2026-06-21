#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# 一键入口：依赖安装 → user_docs 建库 → 启动 API（日常只跑本脚本即可）。
#
#   bash scripts/one_click_demo.sh             # 默认纯本地离线，不请求模型 API
#   bash scripts/one_click_demo.sh --api       # 使用 .env 中 OpenAI-compatible API
#   bash scripts/one_click_demo.sh --qwen      # 千问文本生成 + 千问VL文档页面解析
#   bash scripts/one_click_demo.sh --full      # API + Redis + 本地 ColPali（需 Docker/GPU）
#   bash scripts/one_click_demo.sh --status    # 查看服务状态
#   bash scripts/one_click_demo.sh --stop      # 停止本脚本启动的服务
# 可选环境变量：
#   RAG_FORCE_REBUILD_KB=1  清空 data/user_pages.json、manifest、kb_pages 后全量重建
#   RAG_SKIP_INDEX_BUILD=1  跳过建库，沿用已有索引
#   RAG_LITE_MODE=0         启用 ColPali + Redis 全量链路
# 公网隧道默认关闭；确需临时分享时显式设置 RAG_ENABLE_PUBLIC_TUNNEL=1。
: "${RAG_LITE_MODE:=1}"
: "${RAG_OFFLINE_MODE:=1}"
: "${RAG_ENABLE_PUBLIC_TUNNEL:=0}"
QWEN_MODE=0

ACTION="start"
for arg in "$@"; do
  case "$arg" in
    --offline) RAG_OFFLINE_MODE=1 ;;
    --api) RAG_OFFLINE_MODE=0 ;;
    --qwen) RAG_OFFLINE_MODE=0; QWEN_MODE=1 ;;
    --full) RAG_OFFLINE_MODE=0; RAG_LITE_MODE=0 ;;
    --skip-index) RAG_SKIP_INDEX_BUILD=1 ;;
    --status) ACTION="status" ;;
    --stop) ACTION="stop" ;;
    -h|--help)
      sed -n '5,17p' "$0"
      exit 0
      ;;
    *) echo "未知参数: $arg（使用 --help 查看用法）" >&2; exit 2 ;;
  esac
done

mkdir -p logs

service_pid() {
  local file="$1"
  [ -f "$file" ] && tr -dc '0-9' < "$file" || true
}

stop_pid_file() {
  local file="$1" name="$2" pid
  pid="$(service_pid "$file")"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    echo "已停止 ${name}（PID ${pid}）"
  fi
  rm -f "$file"
}

if [ "$ACTION" = "status" ]; then
  if curl -fsS --max-time 2 http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "✅ API 正在运行: http://127.0.0.1:8000/chat"
    curl -fsS http://127.0.0.1:8000/capabilities || true
    echo
  else
    echo "⏹ API 未运行"
  fi
  exit 0
fi

if [ "$ACTION" = "stop" ]; then
  stop_pid_file logs/api.pid "FastAPI"
  stop_pid_file logs/colpali.pid "ColPali"
  stop_pid_file logs/cloudflared.pid "cloudflared"
  exit 0
fi

# pip：访问 pypi.org 若出现 SSLEOFError / 超时，默认走清华镜像；强制官方源：RAG_USE_PYPI_MIRROR=0
: "${RAG_USE_PYPI_MIRROR:=1}"
PIP_INDEX_ARGS=()
if [ "${RAG_USE_PYPI_MIRROR}" = "1" ]; then
  : "${RAG_PIP_MIRROR:=https://pypi.tuna.tsinghua.edu.cn/simple}"
  PIP_INDEX_ARGS=(-i "${RAG_PIP_MIRROR}" --trusted-host pypi.tuna.tsinghua.edu.cn)
  echo "== pip 使用镜像: ${RAG_PIP_MIRROR}（官方源请设 RAG_USE_PYPI_MIRROR=0）=="
fi

free_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "端口 $port 被占用，正在回收进程: $pids"
    kill $pids >/dev/null 2>&1 || true
    sleep 1
  fi
}

echo "== 准备 Python 环境（RAG_LITE_MODE=${RAG_LITE_MODE}）=="
VENV_DIR=".venv"
if [ -d "$VENV_DIR" ] && ! "$VENV_DIR/bin/python" -c 'import sys' >/dev/null 2>&1; then
  VENV_DIR=".venv-runtime"
  echo "检测到旧 .venv 已失效（通常由仓库移动导致），改用 ${VENV_DIR}，不删除旧环境。"
fi
if [ ! -x "$VENV_DIR/bin/python" ]; then
  python3 -m venv "$VENV_DIR"
fi
# 不 source activate：其绝对路径会在仓库搬家后失效。直接固定当前仓库解释器。
export VIRTUAL_ENV="$PWD/$VENV_DIR"
export PATH="$VIRTUAL_ENV/bin:$PATH"
PYTHON="$VENV_DIR/bin/python"
DEPS_MARKER="$VENV_DIR/.rag-deps.sha256"
DEPS_HASH="$("$PYTHON" - "$RAG_LITE_MODE" <<'PY'
import hashlib, pathlib, sys
files=[pathlib.Path("requirements.txt")]
if sys.argv[1] == "0": files.append(pathlib.Path("requirements/colpali.txt"))
h=hashlib.sha256()
for path in files: h.update(path.read_bytes())
h.update(sys.version.encode())
print(h.hexdigest())
PY
)"
INSTALLED_HASH="$(test -f "$DEPS_MARKER" && tr -dc 'a-f0-9' < "$DEPS_MARKER" || true)"
if [ "${RAG_FORCE_INSTALL:-0}" = "1" ] || [ "$DEPS_HASH" != "$INSTALLED_HASH" ]; then
  echo "依赖首次安装或清单已变化，正在同步..."
  "$PYTHON" -m pip install -U pip "${PIP_INDEX_ARGS[@]}" >/dev/null
  "$PYTHON" -m pip install -r requirements.txt "${PIP_INDEX_ARGS[@]}" >/dev/null
  "$PYTHON" -m pip install socksio "${PIP_INDEX_ARGS[@]}" >/dev/null
  if [ "${RAG_LITE_MODE}" = "0" ]; then
    "$PYTHON" -m pip install -r requirements/colpali.txt "${PIP_INDEX_ARGS[@]}" >/dev/null
  fi
  printf '%s\n' "$DEPS_HASH" > "$DEPS_MARKER"
else
  echo "依赖清单未变化，跳过 pip 安装（强制安装请设 RAG_FORCE_INSTALL=1）"
fi
if ! "$PYTHON" -c 'import fastapi, multipart, uvicorn' >/dev/null 2>&1; then
  echo "依赖安装不完整（需要 fastapi/python-multipart/uvicorn），请查看上方 pip 输出。" >&2
  exit 1
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
set +u
if [ -f .env ]; then
  source .env
else
  echo "未发现 .env；复制 .env.example 作为本地默认配置。"
  cp .env.example .env
  source .env
fi
set -u
set +a

if [ "$QWEN_MODE" = "1" ]; then
  export RAG_ENABLE_QWEN_VISION_PARSER=true
  export RAG_VISION_PARSER_MODEL="${RAG_VISION_PARSER_MODEL:-qwen-vl-ocr}"
  export RAG_QWEN_VLM_MODEL="${RAG_QWEN_VLM_MODEL:-qwen3-vl-plus}"
  export RAG_QWEN_VLM_VERIFIER_MODEL="${RAG_QWEN_VLM_VERIFIER_MODEL:-qwen3-vl-flash}"
  export RAG_ENABLE_LLM_ROUTER=true RAG_ENABLE_LLM_VERIFIER=true
  RAG_BUILD_PAGE_IMAGES=1
fi

if [ "$RAG_OFFLINE_MODE" = "1" ]; then
  # 默认演示不把文档内容发送到任何远端模型或服务。
  export OPENAI_API_KEY="" OAPI_API_KEY=""
  export RAG_ENABLE_REAL_EMBEDDING=false
  export RAG_ENABLE_LLM_ROUTER=false RAG_ENABLE_LLM_VERIFIER=false RAG_ENABLE_FUNCTION_CALLING_ROUTER=false
  export RAG_ENABLE_MULTIMODAL_EMBEDDING=false RAG_ENABLE_COLPALI_RERANK=false
  export RAG_MULTIMODAL_EMBEDDING_API="" RAG_COLPALI_RERANK_API="" RAG_VLM_API="" RAG_CHART_PARSING_API=""
  export RAG_VECTOR_BACKEND=inmemory RAG_SESSION_BACKEND=memory
  echo "运行模式：纯本地离线（规则规划 + 哈希/BM25 检索 + demo fallback，不调用远端模型 API）"
else
  if [ "$QWEN_MODE" = "1" ]; then
    echo "运行模式：千问 Agentic（qwen-plus 路由/生成 + ${RAG_VISION_PARSER_MODEL} 入库解析 + ${RAG_QWEN_VLM_MODEL} 页图推理）"
  else
    echo "运行模式：API（使用 .env 中显式配置的 OpenAI-compatible 服务）"
  fi
fi
if [ "$QWEN_MODE" = "1" ] && [ "${RAG_SKIP_QWEN_PREFLIGHT:-0}" != "1" ]; then
  echo "== 千问 API 预检（仅发送合成测试内容）=="
  "$PYTHON" scripts/check_qwen_api.py
fi
# 可选变量：.env 未声明时给默认值，避免 set -u 与后续命令报错
RAG_BUILD_DPI="${RAG_BUILD_DPI:-144}"
RAG_SKIP_INDEX_BUILD="${RAG_SKIP_INDEX_BUILD:-0}"
RAG_BUILD_PAGE_IMAGES="${RAG_BUILD_PAGE_IMAGES:-$([ "$RAG_LITE_MODE" = "0" ] && echo 1 || echo 0)}"

mkdir -p user_docs kb_pages data

run_index_build() {
  image_args=()
  if [ "$RAG_BUILD_PAGE_IMAGES" != "1" ]; then image_args+=(--skip-page-images); fi
  "$PYTHON" scripts/build_index_incremental.py \
    --input-dir user_docs \
    --output-pages data/user_pages.json \
    --manifest data/index_manifest.json \
    --image-dir kb_pages \
    --lang zh \
    --dpi "${RAG_BUILD_DPI:-144}" \
    --clean-removed \
    "${image_args[@]}"
}

count_user_docs() {
  "$PYTHON" - <<'PY'
from pathlib import Path
exts = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".pptx", ".ppt", ".txt"}
root = Path("user_docs")
print(sum(1 for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts))
PY
}

count_index_pages() {
  "$PYTHON" - <<'PY'
import json
from pathlib import Path
p = Path("data/user_pages.json")
if not p.exists():
    print(0)
else:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        print(len(data) if isinstance(data, list) else 0)
    except Exception:
        print(0)
PY
}

echo "== 知识库建库（user_docs → data/user_pages.json）=="
doc_count="$(count_user_docs)"
RAG_FORCE_REBUILD_KB="${RAG_FORCE_REBUILD_KB:-0}"

if [ "${RAG_SKIP_INDEX_BUILD}" = "1" ]; then
  echo "已跳过建库（RAG_SKIP_INDEX_BUILD=1），使用已有 data/user_pages.json 或 demo_pages.json"
elif [ "$doc_count" -eq 0 ]; then
  echo "user_docs/ 无文档，跳过建库；问答将使用 data/demo_pages.json（若存在）"
else
  if [ "${RAG_FORCE_REBUILD_KB}" = "1" ]; then
    echo "RAG_FORCE_REBUILD_KB=1：清空本地索引后全量重建…"
    rm -f data/user_pages.json data/index_manifest.json
    rm -rf kb_pages/*
    mkdir -p kb_pages
  else
  page_count="$(count_index_pages)"
  if [ "$page_count" -eq 0 ] && [ -f data/index_manifest.json ]; then
    echo "检测到页面索引为空但 manifest 仍在，自动清理 manifest 避免建库被跳过…"
    rm -f data/index_manifest.json
  fi
  fi

  echo "发现可建库文档 ${doc_count} 个，开始处理（大 PDF 可能需数分钟）…"
  run_index_build

  page_count="$(count_index_pages)"
  if [ "$page_count" -eq 0 ]; then
    echo "建库后仍为 0 页，自动再试一次全量重建…"
    rm -f data/index_manifest.json data/user_pages.json
    run_index_build
    page_count="$(count_index_pages)"
  fi
  if [ "$page_count" -eq 0 ]; then
    echo "错误：建库失败，data/user_pages.json 仍为空。请检查 user_docs 内文件是否可解析，或查看上方 WARN。" >&2
    exit 1
  fi
  echo "建库完成：共 ${page_count} 页（data/user_pages.json）"
fi

if [ "${RAG_LITE_MODE}" != "0" ]; then
  echo "== ColPali：跳过（轻量模式不占 GPU；需要时请 RAG_LITE_MODE=0）=="
  pkill -f "uvicorn scripts.colpali_rerank_service:app" >/dev/null 2>&1 || true
else
  echo "== 启动 ColPali rerank 服务 =="
  pkill -f "uvicorn scripts.colpali_rerank_service:app" >/dev/null 2>&1 || true
  free_port 9001
  nohup "$VENV_DIR/bin/python" -m uvicorn scripts.colpali_rerank_service:app --host 127.0.0.1 --port 9001 > logs/colpali.log 2>&1 &
  echo $! > logs/colpali.pid
fi

echo "== 启动 FastAPI 主服务 =="
pkill -f "uvicorn offer_agent.api:app" >/dev/null 2>&1 || true
free_port 8000

REAL_EMB="${RAG_ENABLE_REAL_EMBEDDING:-false}"
if [ "${REAL_EMB}" = "true" ] || [ "${REAL_EMB}" = "1" ]; then
  page_count="$("$PYTHON" - <<'PY'
import json
from pathlib import Path
for p in (Path("data/user_pages.json"), Path("data/demo_pages.json")):
    if p.exists():
        print(len(json.loads(p.read_text(encoding="utf-8"))))
        break
else:
    print(0)
PY
)"
  echo " - 文本向量: 已启用（.env RAG_ENABLE_REAL_EMBEDDING=${REAL_EMB}，模型 ${OPENAI_EMBEDDING_MODEL:-未设置}）"
  if [ "${page_count}" -gt 50 ]; then
    echo " - 提示: 索引约 ${page_count} 页，启动时将逐页请求 embedding，可能需数分钟；日志见 logs/api.log"
  fi
else
  echo " - 文本向量: 本地哈希模拟（.env 设 RAG_ENABLE_REAL_EMBEDDING=true 可改用 DashScope 等）"
fi

if [ "${RAG_LITE_MODE}" != "0" ]; then
  # 轻量：仅关闭 GPU/多模态/ColPali/Loop；RAG_ENABLE_REAL_EMBEDDING 沿用 .env
  nohup env PYTHONUNBUFFERED=1 \
    RAG_ENABLE_MULTIMODAL_EMBEDDING=false \
    RAG_ENABLE_COLPALI_RERANK=false \
    RAG_COLPALI_RERANK_API= \
    RAG_ENABLE_PLAN_EXECUTE_LOOP=false \
    "$VENV_DIR/bin/python" -m uvicorn offer_agent.api:app --host 0.0.0.0 --port 8000 > logs/api.log 2>&1 &
else
  nohup env PYTHONUNBUFFERED=1 \
    RAG_ENABLE_MULTIMODAL_EMBEDDING=false \
    RAG_ENABLE_PLAN_EXECUTE_LOOP=false \
    RAG_ENABLE_COLPALI_RERANK=true \
    RAG_COLPALI_RERANK_API=http://127.0.0.1:9001/rerank \
    "$VENV_DIR/bin/python" -m uvicorn offer_agent.api:app --host 0.0.0.0 --port 8000 > logs/api.log 2>&1 &
fi
echo $! > logs/api.pid

# 真实 embedding 建索引较慢，按页数延长健康检查等待
health_wait=30
if [ "${REAL_EMB}" = "true" ] || [ "${REAL_EMB}" = "1" ]; then
  health_wait=600
  if [ "${page_count:-0}" -gt 200 ]; then
    # 约 2s/页上限估算，最多等 2 小时
    est=$((page_count * 2))
    if [ "$est" -gt 7200 ]; then est=7200; fi
    if [ "$est" -gt "$health_wait" ]; then health_wait="$est"; fi
  fi
fi

echo "等待服务启动（最多 ${health_wait}s，真实 embedding 时请耐心）..."
for _ in $(seq 1 "${health_wait}"); do
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

CF_PUBLIC_URL=""

echo ""
echo "✅ 本地服务已启动（RAG_LITE_MODE=${RAG_LITE_MODE}）"
if [ "${RAG_LITE_MODE}" != "0" ]; then
  if [ "${REAL_EMB}" = "true" ] || [ "${REAL_EMB}" = "1" ]; then
    echo " - 模式: 轻量 + 远程文本 embedding（${OPENAI_EMBEDDING_MODEL:-} / 无 ColPali）"
  else
    echo " - 模式: 轻量（哈希检索 + 规则路由，无 ColPali）"
  fi
else
  colpali_status="$(curl -sS --max-time 2 http://127.0.0.1:9001/health 2>/dev/null || true)"
  if echo "$colpali_status" | grep -q '"model_status":"ready"'; then
    echo " - ColPali: ready"
  else
    echo " - ColPali: 已启动（权重不全时会降级，见 logs/colpali.log）"
  fi
fi

if [ "$RAG_ENABLE_PUBLIC_TUNNEL" = "1" ] && command -v cloudflared >/dev/null 2>&1; then
  echo ""
  echo "== 公网临时隧道（cloudflared）=="
  pkill -f "cloudflared tunnel --url http://127.0.0.1:8000" >/dev/null 2>&1 || true
  nohup cloudflared tunnel --url http://127.0.0.1:8000 > logs/cloudflared.log 2>&1 &
  echo $! > logs/cloudflared.pid
  for _ in $(seq 1 35); do
    CF_PUBLIC_URL="$("$PYTHON" - <<'PY'
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
    if [ -n "$CF_PUBLIC_URL" ]; then
      break
    fi
    sleep 1
  done
  if [ -z "$CF_PUBLIC_URL" ]; then
    echo "隧道已后台启动，若下面未显示公网地址，请稍后执行: grep trycloudflare logs/cloudflared.log"
  fi
elif [ "$RAG_ENABLE_PUBLIC_TUNNEL" = "1" ]; then
  echo ""
  echo "（未安装 cloudflared，仅本地访问。公网分享请: brew install cloudflared 后重跑本脚本）"
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  在浏览器打开（聊天页）："
echo ""
echo "    http://127.0.0.1:8000/chat"
if [ -n "$CF_PUBLIC_URL" ]; then
  echo ""
  echo "  公网临时地址（trycloudflare，过期需重跑脚本）："
  echo ""
  echo "    ${CF_PUBLIC_URL}/chat"
fi
echo ""
echo "  API 文档: http://127.0.0.1:8000/docs   日志: logs/api.log"
echo "════════════════════════════════════════════════════════════"
echo ""
