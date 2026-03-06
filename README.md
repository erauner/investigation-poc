# Investigation MCP

Minimal v1 scaffold for pod/workload investigation with an MCP-first cluster path.

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
  -d '{
    "namespace":"default",
    "target":"deployment/api",
    "profile":"service",
    "service_name":"api",
    "lookback_minutes":15
  }' | jq
```

## Kubernetes deployment (MCP default)

Base manifests are in `k8s/`. Local kind flow uses the local overlay:

```bash
kubectl apply -k k8s-overlays/local-kind
```

This applies the MCP server path used by the agent (`RemoteMCPServer -> investigation-mcp-server`).
The legacy HTTP debug API manifests are isolated in `k8s/optional-http/`.

## Repeatable local kind flow

```bash
# 1) Create/use kind cluster
make kind-up

# 2) Install kagent + MCP server + agent
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

## Use from Claude Code

After `make kind-install-kagent`, port-forward the controller MCP endpoint:

```bash
./scripts/port-forward-controller-mcp.sh
```

This repo includes a local Claude Code MCP config in [.mcp.json](/Users/erauner/git/side/investigation-poc/.mcp.json) that points at `http://127.0.0.1:8083/mcp`.

From the repo root, launch Claude Code:

```bash
claude
```

Suggested smoke test prompts:

```text
Use the kagent MCP server to list available agents and show their names.
```

```text
Use the kagent MCP server to invoke kagent/investigation-agent.
Task: Investigate the unhealthy pod in namespace kagent-smoke and return Diagnosis, Evidence, Recommendation.
```

To prove the custom MCP tool is in the loop, run:

```bash
make kagent-smoke-apply
make kagent-smoke-test TASK="Before answering, call functions.collect_workload_context exactly once with namespace kagent-smoke and target pod/crashy. Then return Diagnosis, Evidence, Recommendation."
make kagent-smoke-clean
```

Optional fast-loop mode: run `make run-mcp` and patch `k8s/investigation-remotemcpserver.yaml`
to `host.docker.internal:8001` if you want host-run MCP iteration.

Optional HTTP debug API deployment:

```bash
make kind-enable-http-debug
```
