pipeline {
    agent any

    stages {
        stage('Install') {
            steps {
                sh '''
                    set -euo pipefail
                    python3 -m pip install --upgrade pip
                    python3 -m pip install -e .[dev]
                '''
            }
        }

        stage('Test') {
            steps {
                sh '''
                    set -euo pipefail
                    pytest -q
                '''
            }
        }
    }
}
