pipeline {
    agent { label 'certikart-build-agent' }

    options {
        skipDefaultCheckout(true)
        disableConcurrentBuilds()
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    parameters {
        booleanParam(name: 'RUN_CATALOG_MIGRATIONS', defaultValue: false, description: 'Run the pipeline-owned catalog schema migrations.')
        booleanParam(name: 'RUN_COLLECTION', defaultValue: false, description: 'Run a live mobile and laptop collection after an approval gate.')
        string(name: 'COLLECTION_LIMIT', defaultValue: '50', description: 'Maximum retailer references per category/source.')
    }

    environment {
        IMAGE = 'ghcr.io/trendly1406-tech/certikart-pipeline'
        DEPLOY_TARGET = 'certikart-deploy@161.118.177.85'
    }

    stages {
        stage('Checkout') {
            steps { checkout scm }
        }
        stage('Install and verify') {
            steps {
                sh 'uv sync --frozen --all-groups --python 3.11'
                sh 'make format-check'
                sh 'make lint'
                sh 'make typecheck'
                sh '''uv run python -m pytest --no-cov -q -k "not test_live_collect_command_runs and not test_bulk_collect_laptops_command_runs and not test_bulk_collect_all_command_runs and not test_http_source_transport_default_client"'''
            }
        }
        stage('Build and publish image') {
            when { branch 'main' }
            steps {
                withCredentials([usernamePassword(credentialsId: 'certikart-ghcr', usernameVariable: 'REGISTRY_USER', passwordVariable: 'REGISTRY_TOKEN')]) {
                    sh '''#!/bin/sh
                        set -eu
                        printf '%s' "$REGISTRY_TOKEN" | docker login ghcr.io --username "$REGISTRY_USER" --password-stdin
                        docker build --pull --target runtime --tag "$IMAGE:$GIT_COMMIT" --tag "$IMAGE:main" .
                        docker push "$IMAGE:$GIT_COMMIT"
                        docker push "$IMAGE:main"
                        docker logout ghcr.io
                    '''
                }
            }
        }
        stage('Approve catalog migrations') {
            when { allOf { branch 'main'; expression { params.RUN_CATALOG_MIGRATIONS } } }
            steps {
                input message: 'Apply the pipeline-owned catalog migrations to the configured database?', ok: 'Apply migrations'
                sshagent(credentials: ['certikart-deploy-ssh']) {
                    sh '''#!/bin/sh
                        set -eu
                        ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$DEPLOY_TARGET" \
                          "migrate-catalog $GIT_COMMIT"
                    '''
                }
            }
        }
        stage('Approve and run collection') {
            when { allOf { branch 'main'; expression { params.RUN_COLLECTION } } }
            steps {
                input message: "Collect up to ${params.COLLECTION_LIMIT} products per retailer for mobile and laptop, then persist to the catalog?", ok: 'Run collection'
                sshagent(credentials: ['certikart-deploy-ssh']) {
                    sh '''#!/bin/sh
                        set -eu
                        case "$COLLECTION_LIMIT" in *[!0-9]*|'') echo 'COLLECTION_LIMIT must be an integer' >&2; exit 2;; esac
                        ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$DEPLOY_TARGET" \
                          "collect-catalog $GIT_COMMIT $COLLECTION_LIMIT"
                    '''
                }
            }
        }
    }
}
