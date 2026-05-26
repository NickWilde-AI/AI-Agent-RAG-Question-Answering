#!/usr/bin/env bash
# vLLM + RAG API 冒烟：用于 P0-3 验收
set -euo pipefail

RAG_BASE="${RAG_BASE:-http://127.0.0.1:8000}"
VLLM_BASE="${VLLM_BASE:-http://127.0.0.1:8001}"

echo "[smoke-vllm] check rag health..."
curl -fsS "$RAG_BASE/health" >/dev/null

echo "[smoke-vllm] check rag chat page..."
curl -fsS "$RAG_BASE/chat" >/dev/null

echo "[smoke-vllm] check vllm models..."
curl -fsS "$VLLM_BASE/v1/models" >/dev/null

echo "[smoke-vllm] check capabilities..."
curl -fsS "$RAG_BASE/capabilities" >/dev/null

echo "[smoke-vllm] PASS"
