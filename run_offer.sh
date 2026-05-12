#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install -r requirements.txt >/dev/null

set -a
source .env
set +a

echo "[offer-mode] http://127.0.0.1:8000/docs"
python -m uvicorn offer_agent.api:app --host 0.0.0.0 --port 8000 --reload

