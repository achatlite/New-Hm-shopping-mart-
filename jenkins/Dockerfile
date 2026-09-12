FROM jenkins/jenkins:lts-jdk17
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git gettext-base python3 \
    && curl -fsSL https://get.docker.com -o /tmp/get-docker.sh \
    && sh /tmp/get-docker.sh \
    && rm -f /tmp/get-docker.sh \
    && curl -fsSL https://dl.k8s.io/release/v1.34.1/bin/linux/amd64/kubectl -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl \
    && rm -rf /var/lib/apt/lists/*
USER jenkins
