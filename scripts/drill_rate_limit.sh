#!/usr/bin/env bash
# 限流演练：并发压 /ask，统计 200 与 429
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
TOTAL="${TOTAL:-40}"
CONCURRENCY="${CONCURRENCY:-10}"
QUERY="${QUERY:-采购申请单的采购单号是多少？}"

TMP="$(mktemp)"
export BASE_URL QUERY TMP

seq 1 "$TOTAL" | xargs -P "$CONCURRENCY" -I{} bash -c '
  code=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"$QUERY\"}" \
    "$BASE_URL/ask" || true)
  echo "$code" >> "$TMP"
'

OK=$(rg "^200$" "$TMP" -c || true)
TOO_MANY=$(rg "^429$" "$TMP" -c || true)
ALL=$(wc -l < "$TMP" | tr -d ' ')
OTHER=$((ALL - OK - TOO_MANY))

echo "rate-limit drill result:"
echo "  total=$TOTAL concurrency=$CONCURRENCY"
echo "  http_200=$OK"
echo "  http_429=$TOO_MANY"
echo "  other=$OTHER"

rm -f "$TMP"
