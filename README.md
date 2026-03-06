# Investigation Service

Minimal v1 scaffold for pod/workload investigation.

## Local run

```bash
make install
make run
```

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
