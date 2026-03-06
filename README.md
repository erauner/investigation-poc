# Investigation Service

Minimal v1 scaffold for pod/workload investigation.

## Local run

```bash
make install
make run
```

`make` targets prefer `uv` when installed, with a `pip/python` fallback.

By default, Prometheus is read from `http://localhost:9090`.
Override if needed:

```bash
PROMETHEUS_URL=http://localhost:9090 make run
```

## Test

```bash
make test
```

## Example investigate request

```bash
curl -s localhost:8080/investigate \
  -H 'content-type: application/json' \
  -d '{"namespace":"default","target":"pod/api-7d4c"}' | jq
```

## Kubernetes deployment

Manifests are in `k8s/`:

```bash
kubectl apply -k k8s/
```

## Repeatable local kind flow

```bash
# 1) Create/use kind cluster
make kind-up

# 2) Install kagent + service + agent
OPENAI_API_KEY=sk-... make kind-install-kagent

# 3) Run smoke test loop (apply workload, invoke agent, cleanup)
make kagent-smoke-loop

# 4) Tear down
make kind-down
```

Or one-shot:

```bash
OPENAI_API_KEY=sk-... make kind-smoke-loop
```
