@Library('homelab-jenkins-library@main') _

pipeline {
    agent {
        kubernetes {
            yaml homelab.podTemplate('default')
        }
    }

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

    post {
        failure {
            script {
                homelab.notifyDiscord(status: 'FAILURE')
            }
        }
    }
}
