# Jenkins for HM Shopping Mart

This lab setup runs Jenkins in Docker and gives the pipeline access to Docker and the host Kubernetes kubeconfig.

Start:

```bash
cd jenkins
export HOME_DIR="$HOME"
docker compose up -d --build
```

Open `http://localhost:8081` and complete the Jenkins setup.

Required Jenkins credentials:

1. `docker-registry-credentials` — Username with password for the image registry.
2. `docker-registry-url` — Secret text containing the registry/repository prefix, for example `docker.io/youruser`.

Create a Pipeline job pointing to the Git repository containing this project and select `Jenkinsfile` from SCM.

For a production setup, do not mount the host Docker socket; use a dedicated build service/agent instead.
