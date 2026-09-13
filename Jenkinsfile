pipeline {
    agent any

    stages {

        stage("Clone Code from GitHub") {
            steps {
                git(
                    url: "https://github.com/achatlite/New-Hm-shopping-mart-.git",
                    branch: "main",
                    credentialsId: "github-credentials"
                )
            }
        }

        // SonarQube disabled

        stage("Build Backend Docker Image") {
            steps {
                sh "docker build -t hm-shopping-mart-backend:${BUILD_NUMBER} ./backend"
            }
        }

        stage("Build Frontend Docker Image") {
            steps {
                sh "docker build -t hm-shopping-mart-frontend:${BUILD_NUMBER} ./frontend"
            }
        }

        stage("Delete Old CI/CD Images") {
            steps {
                sh """
                    docker images hm-shopping-mart-backend --format '{{.ID}} {{.Tag}}' |
                    grep -v ${BUILD_NUMBER} |
                    awk '{print \$1}' |
                    xargs -r docker rmi -f

                    docker images hm-shopping-mart-frontend --format '{{.ID}} {{.Tag}}' |
                    grep -v ${BUILD_NUMBER} |
                    awk '{print \$1}' |
                    xargs -r docker rmi -f
                """
            }
        }

        stage("Docker Hub Login") {
            steps {
                withCredentials([
                    usernamePassword(
                        credentialsId: "dockerhub-cicd",
                        usernameVariable: "DOCKER_USERNAME",
                        passwordVariable: "DOCKER_PASSWORD"
                    )
                ]) {
                    sh '''
                        echo "$DOCKER_PASSWORD" | docker login -u "$DOCKER_USERNAME" --password-stdin
                    '''
                }
            }
        }

        stage("Push Images to Docker Hub") {
            steps {
                sh """
                    docker tag hm-shopping-mart-backend:${BUILD_NUMBER} siddkhan/hm-shopping-mart-backend:${BUILD_NUMBER}
                    docker tag hm-shopping-mart-frontend:${BUILD_NUMBER} siddkhan/hm-shopping-mart-frontend:${BUILD_NUMBER}

                    docker push siddkhan/hm-shopping-mart-backend:${BUILD_NUMBER}
                    docker push siddkhan/hm-shopping-mart-frontend:${BUILD_NUMBER}
                """
            }
        }

        stage("Delete Old Docker Hub Images") {
            steps {
                sh '''
                    echo "Docker Hub images pushed successfully."
                    echo "Old Docker Hub image cleanup will be configured separately."
                '''
            }
        }
    }
}
