# Investigation Service

Minimal v1 scaffold for pod/workload investigation.

## Local run

```bash
make install
make run
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
