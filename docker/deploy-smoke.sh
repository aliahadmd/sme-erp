#!/usr/bin/env bash
# Smoke-test the production compose stack (started by `make deploy-check`).
set -euo pipefail

PORT="${PROXY_PORT:-8080}"
BASE="http://localhost:${PORT}"

echo "Waiting for the stack…"
for i in $(seq 1 60); do
  if curl -sf "${BASE}/healthz" > /dev/null 2>&1; then break; fi
  [ "$i" = 60 ] && { echo "FAIL: /api/healthz never became ready"; exit 1; }
  sleep 3
done

echo "1) SPA served:"
curl -sf "${BASE}/" | grep -q "<div id=\"root\">" && echo "   ok" || { echo "   FAIL"; exit 1; }

echo "2) API health via proxy:"
curl -sf "${BASE}/healthz" | grep -q '"status":"ok"' && echo "   ok" || { echo "   FAIL"; exit 1; }

echo "3) Seed bootstrap data (idempotent):"
docker compose -f docker-compose.prod.yml exec -T api python -m app.core.seed > /dev/null

echo "4) Login through the proxy:"
TOKEN=$(curl -sf -X POST "${BASE}/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${ADMIN_EMAIL:?}\",\"password\":\"${ADMIN_PASSWORD:?}\"}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
echo "   ok"

echo "5) Authenticated /api/auth/me through the proxy:"
curl -sf "${BASE}/api/auth/me" -H "Authorization: Bearer ${TOKEN}" | grep -q "email" \
  && echo "   ok" || { echo "   FAIL"; exit 1; }

echo "Deploy smoke: ALL GREEN"
