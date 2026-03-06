#!/usr/bin/env bash
set -euo pipefail

NS="${NS:-kagent-smoke}"
ACTION="${1:-}"

usage() {
  cat <<USAGE
Usage: $0 <apply|delete>

Env vars:
  NS   Namespace for smoke workload (default: kagent-smoke)
USAGE
}

apply_smoke() {
  cat <<YAML | kubectl apply -f -
apiVersion: v1
kind: Namespace
metadata:
  name: ${NS}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: crashy
  namespace: ${NS}
  labels:
    app: crashy
spec:
  replicas: 1
  selector:
    matchLabels:
      app: crashy
  template:
    metadata:
      labels:
        app: crashy
    spec:
      containers:
        - name: crashy
          image: busybox:1.36
          command: ["sh", "-c", "echo starting && sleep 2 && exit 1"]
---
apiVersion: v1
kind: Service
metadata:
  name: crashy
  namespace: ${NS}
spec:
  selector:
    app: crashy
  ports:
    - name: http
      port: 80
      targetPort: 8080
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: whoami
  namespace: ${NS}
  labels:
    app: whoami
spec:
  replicas: 1
  selector:
    matchLabels:
      app: whoami
  template:
    metadata:
      labels:
        app: whoami
    spec:
      containers:
        - name: whoami
          image: traefik/whoami:v1.10.2
          ports:
            - containerPort: 80
YAML
}

case "${ACTION}" in
  apply)
    apply_smoke
    ;;
  delete)
    kubectl delete namespace "${NS}" --ignore-not-found
    ;;
  *)
    usage
    exit 1
    ;;
esac
