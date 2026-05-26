#!/usr/bin/env bash
# Prometheus + Grafana 冒烟：用于 P0-4 验收
set -euo pipefail

PROM_BASE="${PROM_BASE:-http://127.0.0.1:9090}"
GRAFANA_BASE="${GRAFANA_BASE:-http://127.0.0.1:3000}"
GRAFANA_USER="${GRAFANA_USER:-admin}"
GRAFANA_PASSWORD="${GRAFANA_PASSWORD:-admin}"

echo "[smoke-monitoring] check prometheus healthy..."
curl -fsS "$PROM_BASE/-/healthy" >/dev/null

echo "[smoke-monitoring] check prometheus targets api..."
curl -fsS "$PROM_BASE/api/v1/targets" >/dev/null

echo "[smoke-monitoring] check grafana healthy..."
curl -fsS "$GRAFANA_BASE/api/health" >/dev/null

echo "[smoke-monitoring] check grafana datasources..."
curl -fsS -u "$GRAFANA_USER:$GRAFANA_PASSWORD" "$GRAFANA_BASE/api/datasources" >/dev/null

echo "[smoke-monitoring] PASS"
