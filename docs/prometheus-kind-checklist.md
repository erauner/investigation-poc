# Prometheus Kind Checklist

This checklist captures the next implementation phase for adding a small, deterministic metrics path to the local kind validation flow.

The target architecture is:

- in-cluster Prometheus by default for `local-kind`
- `kube-state-metrics` for Kubernetes object-state metrics
- kubelet/cAdvisor scraping for pod/container resource metrics
- direct Prometheus scraping of workload `/metrics` endpoints
- no OpenTelemetry Collector in phase 1
- no dashboard-first dependency

The investigation model stays unchanged:

- canonical target first
- generic collection second
- Prometheus as additive evidence

`Backend` and `Frontend` remain workload producers. `Cluster` remains a tenant orchestration object that resolves to those components.

## Goal

Support the current investigation runtime with real Prometheus-backed evidence in kind for:

- workload/container signals:
  - `kube_pod_container_status_restarts_total`
  - `container_cpu_usage_seconds_total`
  - `container_memory_working_set_bytes`
- node signals:
  - `kube_node_status_allocatable`
  - `kube_pod_container_resource_requests`
- optional service enrichment:
  - `http_server_request_duration_seconds_count`
  - `http_server_request_duration_seconds_sum`
  - `http_server_request_duration_seconds_bucket`

The current host-backed `prometheus-gateway` path should remain available as an optional fallback, not the default local-kind architecture.

## Phase 1

Build the in-cluster metrics bundle and switch the default local-kind path to it.

### Add

- [k8s/optional-prometheus/kustomization.yaml](/Users/erauner/git/side/investigation-poc/k8s/optional-prometheus/kustomization.yaml)
- [k8s/optional-prometheus/prometheus-serviceaccount.yaml](/Users/erauner/git/side/investigation-poc/k8s/optional-prometheus/prometheus-serviceaccount.yaml)
- [k8s/optional-prometheus/prometheus-clusterrole.yaml](/Users/erauner/git/side/investigation-poc/k8s/optional-prometheus/prometheus-clusterrole.yaml)
- [k8s/optional-prometheus/prometheus-clusterrolebinding.yaml](/Users/erauner/git/side/investigation-poc/k8s/optional-prometheus/prometheus-clusterrolebinding.yaml)
- [k8s/optional-prometheus/prometheus-configmap.yaml](/Users/erauner/git/side/investigation-poc/k8s/optional-prometheus/prometheus-configmap.yaml)
- [k8s/optional-prometheus/prometheus-deployment.yaml](/Users/erauner/git/side/investigation-poc/k8s/optional-prometheus/prometheus-deployment.yaml)
- [k8s/optional-prometheus/prometheus-service.yaml](/Users/erauner/git/side/investigation-poc/k8s/optional-prometheus/prometheus-service.yaml)
- [k8s/optional-prometheus/kube-state-metrics-serviceaccount.yaml](/Users/erauner/git/side/investigation-poc/k8s/optional-prometheus/kube-state-metrics-serviceaccount.yaml)
- [k8s/optional-prometheus/kube-state-metrics-clusterrole.yaml](/Users/erauner/git/side/investigation-poc/k8s/optional-prometheus/kube-state-metrics-clusterrole.yaml)
- [k8s/optional-prometheus/kube-state-metrics-clusterrolebinding.yaml](/Users/erauner/git/side/investigation-poc/k8s/optional-prometheus/kube-state-metrics-clusterrolebinding.yaml)
- [k8s/optional-prometheus/kube-state-metrics-deployment.yaml](/Users/erauner/git/side/investigation-poc/k8s/optional-prometheus/kube-state-metrics-deployment.yaml)
- [k8s/optional-prometheus/kube-state-metrics-service.yaml](/Users/erauner/git/side/investigation-poc/k8s/optional-prometheus/kube-state-metrics-service.yaml)

### Change

- [k8s-overlays/local-kind/kustomization.yaml](/Users/erauner/git/side/investigation-poc/k8s-overlays/local-kind/kustomization.yaml)
  - add the in-cluster Prometheus bundle
  - stop making `prometheus-gateway.yaml` part of the default overlay
- [k8s-overlays/local-kind/patch-configmap.yaml](/Users/erauner/git/side/investigation-poc/k8s-overlays/local-kind/patch-configmap.yaml)
  - stop overriding `PROMETHEUS_URL` to the host gateway
- [k8s/cluster-registry-configmap.yaml](/Users/erauner/git/side/investigation-poc/k8s/cluster-registry-configmap.yaml)
  - change `local-kind.prometheus_url` to the in-cluster service
  - preferred value: `http://prometheus.kagent.svc.cluster.local:9090`

### Prometheus scrape requirements

The initial `prometheus.yml` should cover:

- `kube-state-metrics`
- kubelet `/metrics`
- kubelet `/metrics/cadvisor`
- pod `/metrics` scraping gated by annotations

Pod scrape should be annotation-based, not ServiceMonitor-based. The intended annotation model is:

- `prometheus.io/scrape: "true"`
- `prometheus.io/port: "<port>"`
- `prometheus.io/path: "/metrics"`

This keeps the first slice compatible with operator-managed workloads without adding Prometheus Operator CRDs.

## Phase 1 Validation

Add a dedicated validation lane for metrics readiness.

### Add

- [scripts/kind-validate-metrics.sh](/Users/erauner/git/side/investigation-poc/scripts/kind-validate-metrics.sh)
- [Makefile](/Users/erauner/git/side/investigation-poc/Makefile)
  - add `kind-validate-metrics`

### Script checks

The script should:

1. stand up the local kind stack
2. wait for:
   - `deploy/prometheus`
   - `deploy/kube-state-metrics`
3. verify Prometheus readiness via `/-/ready`
4. verify the key PromQL families used by [prom_adapter.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/prom_adapter.py) actually return data for the smoke workload
5. optionally call the Python collection path and assert `prometheus_available == true`

### Minimum PromQL assertions

- `kube_pod_container_status_restarts_total{namespace="kagent-smoke",pod=~"crashy.*"}`
- `container_memory_working_set_bytes{namespace="kagent-smoke",pod=~"crashy.*"}`
- `container_cpu_usage_seconds_total{namespace="kagent-smoke",pod=~"crashy.*"}`

## Phase 1 Docs

Update:

- [README.md](/Users/erauner/git/side/investigation-poc/README.md)
  - local-kind should describe in-cluster Prometheus as the default path
  - host-backed Prometheus should be documented as an optional fallback
- [DEMO.md](/Users/erauner/git/side/investigation-poc/DEMO.md)
  - add a metrics validation step and expected behavior

## Phase 2

Add a small app-metrics demo fixture without changing the investigation model.

### Add

- a metrics-enabled smoke workload fixture
- a tiny long-running traffic generator

Suggested location:

- [test-fixtures/metrics-smoke/](/Users/erauner/git/side/investigation-poc/test-fixtures)
  - `namespace.yaml`
  - metrics-enabled backend deployment/service
  - traffic generator deployment
  - `kustomization.yaml`

### Purpose

This phase proves that:

- pod/container metrics come from cluster collectors
- app/service metrics come from workload `/metrics`
- `investigation-poc` can combine both

This should not become a separate “service investigation engine.” Instead:

- workload investigations remain workload investigations
- service metrics are attached as enrichment when `service_name` is known

## Phase 2 Runtime Work

Keep the runtime change narrow.

### Review

- [src/investigation_service/prom_adapter.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/prom_adapter.py)
- [src/investigation_service/tools.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/tools.py)
- [src/investigation_service/analysis.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/analysis.py)

### Desired behavior

- keep canonical scope logic unchanged
- keep `Backend/`, `Frontend/`, and `Cluster/` resolution behavior unchanged
- optionally collect a compact service-metric block during workload investigations when `service_name` is known
- derive additional findings only when those metrics are present

## Phase 2 Validation

Extend the operator lane with a running-but-degraded fixture.

### Keep

- [test-fixtures/operator-smoke/backend-crashy.yaml](/Users/erauner/git/side/investigation-poc/test-fixtures/operator-smoke/backend-crashy.yaml)
  - validates routing plus K8s/container failure evidence

### Add

- a second operator-backed workload that stays up, emits `/metrics`, and receives deterministic traffic

This is the right place to validate:

- request rate
- error rate
- p95 latency

It is not the right role for `Backend/crashy`, which is better for events, logs, and restart evidence.

## Optional Fallback Overlay

Keep the current host-backed path, but move it behind an explicit overlay.

### Add

- [k8s-overlays/local-kind-host-prometheus/kustomization.yaml](/Users/erauner/git/side/investigation-poc/k8s-overlays/local-kind-host-prometheus/kustomization.yaml)
- [k8s-overlays/local-kind-host-prometheus/patch-configmap.yaml](/Users/erauner/git/side/investigation-poc/k8s-overlays/local-kind-host-prometheus/patch-configmap.yaml)
- [k8s-overlays/local-kind-host-prometheus/patch-cluster-registry-configmap.yaml](/Users/erauner/git/side/investigation-poc/k8s-overlays/local-kind-host-prometheus/patch-cluster-registry-configmap.yaml)

### Reuse

- [k8s-overlays/local-kind/prometheus-gateway.yaml](/Users/erauner/git/side/investigation-poc/k8s-overlays/local-kind/prometheus-gateway.yaml)

## What Not To Do

- do not make `giraffe-testkit` a runtime dependency of the local validation lane
- do not add OpenTelemetry Collector in phase 1
- do not add Grafana
- do not move target-resolution behavior out of the current canonical target model just to support metrics
- do not turn service metrics into a separate operator-specific investigation engine

## Reference Material

Use these repos as references, not foundations:

- [grafana_prom_dash_as_code](/Users/erauner/git/side/grafana_prom_dash_as_code)
  - metric names
  - label/query patterns
  - schema verification ideas
- [giraffe-testkit](/Users/erauner/git/side/giraffe-testkit)
  - local observability topology ideas
  - optional later OTel overlay inspiration

## First Slice Exit Criteria

The first slice is complete when all of the following are true:

- default `local-kind` uses in-cluster Prometheus
- the host gateway path still exists as an optional fallback
- Prometheus in kind returns non-empty results for the current workload metrics queried by `prom_adapter.py`
- generic unhealthy-pod investigation still works
- operator-backed `Backend/crashy` investigation still works
- no new dashboard or OTel dependency was introduced
