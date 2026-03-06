pipeline {
    agent {
        kubernetes {
            yaml '''
apiVersion: v1
kind: Pod
spec:
  containers:
    - name: python
      image: python:3.12-alpine
      command: ["cat"]
      tty: true
'''
        }
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
    }
}
