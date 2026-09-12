#!/usr/bin/env bash
set -euo pipefail
BASE_URL="${BASE_URL:-http://localhost:8000}"
curl -fsS "${BASE_URL}/health" >/dev/null
curl -fsS "${BASE_URL}/ready" >/dev/null
curl -fsS "${BASE_URL}/metrics" | grep -q '^http_requests_total'
echo "HM Shopping Mart backend health/metrics checks passed"
