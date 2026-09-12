#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/logs/frontend" "$ROOT/logs/backend" "$ROOT/logs/database" "$ROOT/k8s/mysql-data"
export HM_PROJECT_ROOT="$ROOT"
export BACKEND_IMAGE="${BACKEND_IMAGE:-hm-shopping-mart-backend:latest}"
export FRONTEND_IMAGE="${FRONTEND_IMAGE:-hm-shopping-mart-frontend:latest}"
envsubst < "$ROOT/k8s/app.yaml.tpl" | kubectl apply -f -
kubectl rollout status deployment/hm-shopping-mart-backend -n hm-shopping-mart --timeout=180s
kubectl rollout status deployment/hm-shopping-mart-frontend -n hm-shopping-mart --timeout=180s
kubectl rollout status deployment/hm-shopping-mart-mysql -n hm-shopping-mart --timeout=180s
