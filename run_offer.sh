#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

VENV_DIR=".venv"
if [ -d "$VENV_DIR" ] && ! "$VENV_DIR/bin/python" -c 'import sys' >/dev/null 2>&1; then
  VENV_DIR=".venv-runtime"
  echo "检测到旧 .venv 已失效，改用 ${VENV_DIR}。"
fi
if [ ! -x "$VENV_DIR/bin/python" ]; then python3 -m venv "$VENV_DIR"; fi

export VIRTUAL_ENV="$PWD/$VENV_DIR"
export PATH="$VIRTUAL_ENV/bin:$PATH"
"$VENV_DIR/bin/python" -m pip install -r requirements.txt >/dev/null

if [ -f .env ]; then set -a; source .env; set +a; fi

echo "[offer-mode] http://127.0.0.1:8000/docs"
"$VENV_DIR/bin/python" -m uvicorn offer_agent.api:app --host 0.0.0.0 --port 8000 --reload
