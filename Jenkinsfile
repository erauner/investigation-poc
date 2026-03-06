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
'''
        }
    }

    environment {
        REGISTRY = 'docker.nexus.erauner.dev/homelab'
        IMAGE_NAME = 'investigation-poc'
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

        stage('Build & Push Image') {
            when {
                branch 'main'
            }
            steps {
                container('kaniko') {
                    script {
                        def shortCommit = sh(script: 'echo $GIT_COMMIT | cut -c1-7', returnStdout: true).trim()
                        sh """
                            /kaniko/executor \
                                --context=dir://${WORKSPACE} \
                                --dockerfile=${WORKSPACE}/Dockerfile \
                                --destination=${REGISTRY}/${IMAGE_NAME}:${shortCommit} \
                                --destination=${REGISTRY}/${IMAGE_NAME}:latest \
                                --cache=true \
                                --cache-repo=${REGISTRY}/${IMAGE_NAME}-cache
                        """
                    }
                }
            }
        }
    }
}
