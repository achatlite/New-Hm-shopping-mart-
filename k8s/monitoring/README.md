# HM Shopping Mart Monitoring

This is a lightweight Kubernetes monitoring stack for the lab/demo environment:

- Prometheus: application/API, Kubernetes node and pod discovery
- Grafana: dashboards and Prometheus visualization
- kube-state-metrics: Kubernetes object/state metrics
- node-exporter: node CPU, memory, filesystem and network metrics
- FastAPI `/metrics`: request count, latency and HTTP status metrics

Install after the application is running:

```bash
./k8s/monitoring/install.sh
kubectl get pods -n monitoring
kubectl get svc -n monitoring
```

NodePorts:
- Prometheus: 30090
- Grafana: 30300

For a production environment, move Grafana credentials to a separately managed secret and use persistent storage for Prometheus/Grafana.
