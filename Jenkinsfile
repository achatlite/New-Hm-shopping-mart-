pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '15'))
    }

    parameters {
        choice(name: 'DEPLOY_ENV', choices: ['kubernetes'], description: 'Deployment target')
        string(name: 'IMAGE_TAG', defaultValue: '', description: 'Optional image tag. Leave empty to use BUILD_NUMBER.')
    }

    environment {
        REGISTRY = credentials('docker-registry-url')
        REGISTRY_HOST = 'docker.io'
        IMAGE_REPO = 'hm-shopping-mart'
        K8S_NAMESPACE = 'hm-shopping-mart'
    }

    stages {
        stage('Validate') {
            steps {
                sh '''
                    set -eux
                    python3 -m py_compile backend/app/main.py backend/scripts/*.py
                    test -f docker-compose.yml
                    test -f k8s/app.yaml.tpl
                    test -f k8s/deploy.sh
                '''
            }
        }

        stage('Compose Config Check') {
            steps {
                sh 'docker compose config >/tmp/hm-shopping-mart-compose.yml'
            }
        }

        stage('Build Images') {
            steps {
                script {
                    env.RELEASE_TAG = params.IMAGE_TAG?.trim() ? params.IMAGE_TAG.trim() : env.BUILD_NUMBER
                }
                sh '''
                    set -eux
                    docker build --pull -t ${REGISTRY}/${IMAGE_REPO}-backend:${RELEASE_TAG} ./backend
                    docker build --pull -t ${REGISTRY}/${IMAGE_REPO}-frontend:${RELEASE_TAG} ./frontend
                    docker tag ${REGISTRY}/${IMAGE_REPO}-backend:${RELEASE_TAG} ${REGISTRY}/${IMAGE_REPO}-backend:latest
                    docker tag ${REGISTRY}/${IMAGE_REPO}-frontend:${RELEASE_TAG} ${REGISTRY}/${IMAGE_REPO}-frontend:latest
                '''
            }
        }

        stage('Push Images') {
            steps {
                withCredentials([usernamePassword(credentialsId: 'docker-registry-credentials', usernameVariable: 'DOCKER_USER', passwordVariable: 'DOCKER_PASS')]) {
                    sh '''
                        set -eux
                        echo "$DOCKER_PASS" | docker login ${REGISTRY_HOST} -u "$DOCKER_USER" --password-stdin
                        docker push ${REGISTRY}/${IMAGE_REPO}-backend:${RELEASE_TAG}
                        docker push ${REGISTRY}/${IMAGE_REPO}-backend:latest
                        docker push ${REGISTRY}/${IMAGE_REPO}-frontend:${RELEASE_TAG}
                        docker push ${REGISTRY}/${IMAGE_REPO}-frontend:latest
                        docker logout ${REGISTRY_HOST}
                    '''
                }
            }
        }

        stage('Deploy Kubernetes') {
            steps {
                sh '''
                    set -eux
                    export HM_PROJECT_ROOT="$WORKSPACE"
                    envsubst < k8s/app.yaml.tpl | kubectl apply -f -
                    kubectl -n ${K8S_NAMESPACE} set image deployment/hm-shopping-mart-backend backend=${REGISTRY}/${IMAGE_REPO}-backend:${RELEASE_TAG}
                    kubectl -n ${K8S_NAMESPACE} set image deployment/hm-shopping-mart-frontend frontend=${REGISTRY}/${IMAGE_REPO}-frontend:${RELEASE_TAG}
                    kubectl -n ${K8S_NAMESPACE} rollout status deployment/hm-shopping-mart-backend --timeout=180s
                    kubectl -n ${K8S_NAMESPACE} rollout status deployment/hm-shopping-mart-frontend --timeout=180s
                    kubectl -n ${K8S_NAMESPACE} rollout status deployment/hm-shopping-mart-mysql --timeout=180s
                '''
            }
        }

        stage('Post Deploy Smoke Test') {
            steps {
                sh '''
                    set -eux
                    kubectl -n ${K8S_NAMESPACE} get pods -o wide
                    kubectl -n ${K8S_NAMESPACE} get svc
                    kubectl -n ${K8S_NAMESPACE} run hm-smoke-${BUILD_NUMBER} --rm --restart=Never --image=curlimages/curl:8.10.1 --command -- \
                      curl -fsS http://backend:8000/health
                '''
            }
        }
    }

    post {
        always {
            sh 'docker image prune -f || true'
        }
        failure {
            sh 'kubectl -n hm-shopping-mart get pods -o wide || true; kubectl -n hm-shopping-mart get events --sort-by=.lastTimestamp | tail -50 || true'
        }
    }
}
