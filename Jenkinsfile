pipeline {
    agent {
        kubernetes {
            yaml '''
apiVersion: v1
kind: Pod
spec:
  imagePullSecrets:
    - name: nexus-registry-credentials
  containers:
    - name: python
      image: python:3.12-alpine
      command: ["cat"]
      tty: true
      resources:
        requests:
          cpu: "250m"
          memory: "512Mi"
        limits:
          cpu: "1000m"
          memory: "1Gi"
    - name: kaniko
      image: gcr.io/kaniko-project/executor:debug
      command: ["sleep", "3600"]
      tty: true
      volumeMounts:
        - name: nexus-creds
          mountPath: /kaniko/.docker
      resources:
        requests:
          cpu: "500m"
          memory: "1Gi"
        limits:
          cpu: "1000m"
          memory: "2Gi"
    - name: jnlp
      resources:
        requests:
          cpu: "100m"
          memory: "256Mi"
        limits:
          cpu: "500m"
          memory: "512Mi"
  volumes:
    - name: nexus-creds
      secret:
        secretName: nexus-registry-credentials
        items:
          - key: config.json
            path: config.json
'''
        }
    }

    environment {
        IMAGE_NAME = 'docker.nexus.erauner.dev/homelab/investigation-poc'
        SHADOW_IMAGE_NAME = 'docker.nexus.erauner.dev/homelab/investigation-shadow-runtime'
        LOKI_MCP_IMAGE_NAME = 'docker.nexus.erauner.dev/homelab/loki-mcp-server'
        ALERTMANAGER_MCP_IMAGE_NAME = 'docker.nexus.erauner.dev/homelab/alertmanager-mcp-server'
        DOCKER_CONFIG = '/kaniko/.docker'
    }

    stages {
        stage('Install') {
            steps {
                container('python') {
                    sh '''
                        set -euo pipefail
                        python3 -m pip install --upgrade pip
                        python3 -m pip install -e .[dev]
                    '''
                }
            }
        }

        stage('Test') {
            steps {
                container('python') {
                    sh '''
                        set -euo pipefail
                        pytest -q
                    '''
                }
            }
        }

        stage('Build & Push Images') {
            when {
                branch 'main'
            }
            steps {
                container('kaniko') {
                    script {
                        def shortCommit = sh(script: 'echo $GIT_COMMIT | cut -c1-7', returnStdout: true).trim()
                        sh """
                            test -f /kaniko/.docker/config.json
                            /kaniko/executor \
                                --dockerfile=Dockerfile \
                                --context=dir://. \
                                --destination=${IMAGE_NAME}:${shortCommit} \
                                --destination=${IMAGE_NAME}:latest \
                                --cache=true \
                                --cache-repo=${IMAGE_NAME}-cache \
                                --skip-tls-verify-registry=docker.nexus.erauner.dev \
                                --custom-platform=linux/amd64

                            /kaniko/executor \
                                --dockerfile=Dockerfile.loki-mcp \
                                --context=dir://. \
                                --destination=${LOKI_MCP_IMAGE_NAME}:${shortCommit} \
                                --destination=${LOKI_MCP_IMAGE_NAME}:latest \
                                --cache=true \
                                --cache-repo=${LOKI_MCP_IMAGE_NAME}-cache \
                                --skip-tls-verify-registry=docker.nexus.erauner.dev \
                                --custom-platform=linux/amd64

                            /kaniko/executor \
                                --dockerfile=Dockerfile.alertmanager-mcp \
                                --context=dir://. \
                                --destination=${ALERTMANAGER_MCP_IMAGE_NAME}:${shortCommit} \
                                --destination=${ALERTMANAGER_MCP_IMAGE_NAME}:latest \
                                --cache=true \
                                --cache-repo=${ALERTMANAGER_MCP_IMAGE_NAME}-cache \
                                --skip-tls-verify-registry=docker.nexus.erauner.dev \
                                --custom-platform=linux/amd64

                            /kaniko/executor \
                                --dockerfile=Dockerfile.shadow \
                                --context=dir://. \
                                --build-arg=BASE_IMAGE=${IMAGE_NAME}:${shortCommit} \
                                --destination=${SHADOW_IMAGE_NAME}:${shortCommit} \
                                --destination=${SHADOW_IMAGE_NAME}:latest \
                                --cache=false \
                                --skip-tls-verify-registry=docker.nexus.erauner.dev \
                                --custom-platform=linux/amd64
                        """
                    }
                }
            }
        }
    }
}
