#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
kubectl apply -f "$ROOT/k8s/monitoring/prometheus-config.yaml"
kubectl apply -f "$ROOT/k8s/monitoring/monitoring.yaml"
kubectl -n monitoring rollout status deployment/prometheus --timeout=180s
kubectl -n monitoring rollout status deployment/grafana --timeout=180s
kubectl -n monitoring rollout status deployment/kube-state-metrics --timeout=180s
kubectl -n monitoring rollout status daemonset/node-exporter --timeout=180s
echo "Prometheus: http://<NODE-IP>:30090"
echo "Grafana:    http://<NODE-IP>:30300"
echo "Grafana default lab login: admin / admin123 (change it after first login)"
