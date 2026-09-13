#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p \
  "$ROOT/logs/frontend" \
  "$ROOT/logs/backend" \
  "$ROOT/logs/database" \
  "$ROOT/k8s/mysql-data"

export HM_PROJECT_ROOT="$ROOT"

IMAGE_TAG="${IMAGE_TAG:-latest}"

export BACKEND_IMAGE="${BACKEND_IMAGE:-siddkhan/hm-shopping-mart-backend:${IMAGE_TAG}}"
export FRONTEND_IMAGE="${FRONTEND_IMAGE:-siddkhan/hm-shopping-mart-frontend:${IMAGE_TAG}}"

echo "========================================"
echo "HM Shopping Mart Kubernetes Deployment"
echo "========================================"
echo "Backend Image : $BACKEND_IMAGE"
echo "Frontend Image: $FRONTEND_IMAGE"
echo "Namespace     : hm-shopping-mart"
echo "========================================"

if ! kubectl get namespace hm-shopping-mart >/dev/null 2>&1; then
    echo "Namespace hm-shopping-mart does not exist. Creating it..."
    kubectl create namespace hm-shopping-mart
else
    echo "Namespace hm-shopping-mart already exists. Using it."
fi

envsubst '${HM_PROJECT_ROOT} ${BACKEND_IMAGE} ${FRONTEND_IMAGE}' \
  < "$ROOT/k8s/app.yaml.tpl" \
  | kubectl apply -f -

echo "Waiting for MySQL..."
kubectl rollout status deployment/hm-shopping-mart-mysql \
  -n hm-shopping-mart \
  --timeout=180s

echo "Waiting for Backend..."
kubectl rollout status deployment/hm-shopping-mart-backend \
  -n hm-shopping-mart \
  --timeout=180s

echo "Waiting for Frontend..."
kubectl rollout status deployment/hm-shopping-mart-frontend \
  -n hm-shopping-mart \
  --timeout=180s

echo "========================================"
echo "Deployment completed successfully."
echo "========================================"

kubectl get pods -n hm-shopping-mart
kubectl get svc -n hm-shopping-mart
