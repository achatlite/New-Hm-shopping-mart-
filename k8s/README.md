# HM Shopping Mart Kubernetes

This folder keeps the application runnable in Kubernetes without changing the existing database data. The helper uses the current project directory for hostPath log mounts, so frontend/backend/database logs appear under `./logs/`.

Build the local images first:
```bash
docker build -t hm-shopping-mart-backend:latest ./backend
docker build -t hm-shopping-mart-frontend:latest ./frontend
```

Then run `./k8s/deploy.sh`. For a local single-node cluster, the same host must have the project directory mounted/accessible to the node. The script never deletes the MySQL PVC.
