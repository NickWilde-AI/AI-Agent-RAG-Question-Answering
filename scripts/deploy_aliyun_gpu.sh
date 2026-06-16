#!/usr/bin/env bash
# 阿里云 GPU 机一键部署（Ubuntu 24.04 + NVIDIA A10 等）
# 在服务器上执行：bash scripts/deploy_aliyun_gpu.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/NickWilde-AI/AI-Agent-RAG-Question-Answering.git}"
APP_DIR="${APP_DIR:-/data/rag/AI-Agent-RAG-Question-Answering}"
DATA_ROOT="${DATA_ROOT:-/data}"
BRANCH="${BRANCH:-main}"
COMPOSE_FILE="${COMPOSE_FILE:-deploy/compose/docker-compose.aliyun.yml}"

log() { echo "[deploy] $*"; }

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "请使用 root 执行，或 sudo bash $0" >&2
    exit 1
  fi
}

install_docker() {
  if command -v docker >/dev/null 2>&1; then
    log "docker 已安装: $(docker --version)"
    return
  fi
  log "安装 Docker..."
  apt-get update -y
  apt-get install -y ca-certificates curl git
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "${VERSION_CODENAME:-noble}") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
}

docker_gpu_ok() {
  if docker info 2>/dev/null | grep -qi 'nvidia'; then
    return 0
  fi
  if command -v nvidia-ctk >/dev/null 2>&1; then
    return 0
  fi
  # 网络不稳时拉 cuda 镜像易失败；仅作辅助检测
  timeout 90 docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi >/dev/null 2>&1
}

install_nvidia_container() {
  if docker_gpu_ok; then
    log "Docker GPU 运行时已就绪（阿里云 GPU 镜像通常已预装），跳过 Toolkit 安装"
    return
  fi

  log "尝试安装 NVIDIA Container Toolkit..."
  if apt-cache show nvidia-container-toolkit >/dev/null 2>&1; then
    apt-get install -y nvidia-container-toolkit || true
    nvidia-ctk runtime configure --runtime=docker 2>/dev/null || true
    systemctl restart docker 2>/dev/null || true
    if docker_gpu_ok; then
      log "已通过 apt 安装 NVIDIA Container Toolkit"
      return
    fi
  fi

  # 官方源（国内可能 reset；失败不阻断，阿里云镜像常已具备驱动）
  if curl -fsSL --connect-timeout 15 https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --batch --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg 2>/dev/null; then
    curl -fsSL --connect-timeout 15 https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
      | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
      > /etc/apt/sources.list.d/nvidia-container-toolkit.list || true
    apt-get update -y || true
    apt-get install -y nvidia-container-toolkit || true
    nvidia-ctk runtime configure --runtime=docker 2>/dev/null || true
    systemctl restart docker 2>/dev/null || true
  else
    log "警告: 无法从 nvidia.github.io 拉取 gpg（Connection reset 常见），将尝试直接启动 compose"
  fi

  if docker_gpu_ok; then
    log "NVIDIA Container Toolkit 配置完成"
  else
    log "警告: 未验证 Docker GPU；若 vLLM 启动失败，请在阿里云控制台确认已选 GPU 镜像"
  fi
}

clone_repo() {
  mkdir -p "$(dirname "$APP_DIR")"
  if [ -d "$APP_DIR/.git" ]; then
    log "更新仓库 $APP_DIR"
    git -C "$APP_DIR" fetch origin
    git -C "$APP_DIR" checkout "$BRANCH"
    git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
  else
    log "克隆仓库 -> $APP_DIR"
    git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$APP_DIR"
  fi
}

prepare_env() {
  cd "$APP_DIR"
  mkdir -p "$DATA_ROOT/huggingface" "$DATA_ROOT/rag-logs" user_docs kb_pages data logs
  export DATA_ROOT

  if [ ! -f .env ]; then
    cp .env.example .env
    log "已生成 .env，请按需编辑 OPENAI_API_KEY / HF_TOKEN"
  fi

  # 追加阿里云推荐项（不覆盖已有配置）
  grep -q '^DATA_ROOT=' .env 2>/dev/null || echo "DATA_ROOT=$DATA_ROOT" >> .env
  grep -q '^HF_ENDPOINT=' .env 2>/dev/null || echo "HF_ENDPOINT=https://hf-mirror.com" >> .env
  grep -q '^RAG_ENABLE_LANGGRAPH=' .env || true

  if ! grep -q '^OPENAI_API_KEY=.\+' .env 2>/dev/null; then
    log "警告: OPENAI_API_KEY 为空时，LLM 路由/校验将走规则与本地降级"
  fi
}

open_firewall_hint() {
  log "请在阿里云安全组放行入站：TCP 8000（必选），3000/9090（监控可选）"
}

compose_up() {
  cd "$APP_DIR"
  export DATA_ROOT
  log "构建并启动 compose（首次拉取 MiniCPM 模型可能 20~60 分钟）..."
  docker compose -f "$COMPOSE_FILE" pull redis 2>/dev/null || true
  docker compose -f "$COMPOSE_FILE" up -d --build

  log "等待 vLLM 健康（最长约 30 分钟）..."
  for i in $(seq 1 120); do
    if curl -fsS http://127.0.0.1:8001/v1/models >/dev/null 2>&1; then
      log "vLLM 就绪"
      break
    fi
    if [ "$i" -eq 120 ]; then
      log "vLLM 尚未就绪，请查看: docker compose -f $COMPOSE_FILE logs -f vllm"
      exit 1
    fi
    sleep 15
  done

  log "等待 RAG API..."
  for i in $(seq 1 40); do
    if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
      log "RAG API 就绪"
      break
    fi
    sleep 5
  done

  PUBLIC_IP="$(curl -fsS --max-time 3 http://100.100.100.200/latest/meta-data/eipv4 2>/dev/null || hostname -I | awk '{print $1}')"
  log "部署完成"
  echo "  本地: http://127.0.0.1:8000/chat"
  echo "  公网: http://${PUBLIC_IP}:8000/chat"
  echo "  能力: curl -s http://127.0.0.1:8000/capabilities | python3 -m json.tool"
}

quick_smoke() {
  cd "$APP_DIR"
  if [ -x scripts/smoke_vllm_stack.sh ]; then
    RAG_BASE=http://127.0.0.1:8000 VLLM_BASE=http://127.0.0.1:8001 bash scripts/smoke_vllm_stack.sh || true
  fi
  curl -fsS -X POST http://127.0.0.1:8000/ask \
    -H 'Content-Type: application/json' \
    -d '{"query":"采购申请单的采购单号是多少？","topk":3}' | head -c 500
  echo ""
}

main() {
  need_root
  install_docker
  clone_repo
  install_nvidia_container
  prepare_env
  open_firewall_hint
  compose_up
  quick_smoke
}

main "$@"
