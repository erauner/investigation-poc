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

For the most important end-to-end local validation path, use:

```bash
OPENAI_API_KEY=sk-... make kind-validate
```

For the first multi-cluster routing validation path, use:

```bash
make kind-validate-multi
```

This exercises the local kind stack, deploys the smoke workload, runs the standard investigation prompt, runs the explicit `build_investigation_report` prompt, and fails if the five-section report contract regresses.

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

## MCP topology

There are two different MCP surfaces in this setup, and they serve different roles:

- `investigation-mcp-server`: the repo-local tool server defined in `k8s/`. It exposes the low-level investigation tools that the custom agent uses behind the scenes.
- `kagent-controller`: the higher-level controller MCP endpoint. It exposes agent-oriented tools such as `list_agents` and `invoke_agent`.

That distinction matters for clients:

- `Claude Code` can talk directly to the controller MCP endpoint and then invoke `kagent/homelab-k8s-custom-agent`.
- The `Claude Desktop` extension in this repo is intentionally controller-backed. It does not call raw investigation tools directly; it talks to the controller MCP endpoint and lets the controller invoke the custom agent.
- The raw `investigation-mcp-server` is still the correct backend surface for the agent itself and for lower-level debugging inside the cluster.

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

Full local contract validation:

```bash
OPENAI_API_KEY=sk-... make kind-validate
```

Host-routed multi-cluster validation:

```bash
make kind-validate-multi
```

Use [DEMO.md](/Users/erauner/git/side/investigation-poc/DEMO.md) as the repo-local source of truth for:

- the end-to-end kind demo
- the single-run local validation entrypoint
- the five-section investigation prompts
- expected output semantics for Evidence, Related Data, and Limitations

This repo should be sufficient for local validation. The production GitOps rollout still happens from `homelab-k8s`, but the fast feedback loop should stay here.

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
Task: Investigate the unhealthy pod in namespace kagent-smoke and return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.
```

To prove the custom MCP tool is in the loop, run:

```bash
make kagent-smoke-apply
make kagent-smoke-test TASK="Investigate the unhealthy pod in namespace kagent-smoke. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step."
make kagent-smoke-clean
```

Optional fast-loop mode: run `make run-mcp` and patch `k8s/investigation-remotemcpserver.yaml`
to `host.docker.internal:8001` if you want host-run MCP iteration.

Optional HTTP debug API deployment:

```bash
make kind-enable-http-debug
```

## Claude Desktop extension

This repo now also includes a Desktop packaging path in [desktop-extension/README.md](/Users/erauner/git/side/investigation-poc/desktop-extension/README.md).

Use that path when you want Claude Desktop to reach a remote MCP server through an installable `.mcpb` bundle. The extension is intentionally thin: it proxies a narrow investigation tool surface to the `kagent-controller` MCP endpoint and leaves diagnosis logic in the controller + agent + Python backend path.

Build it with:

```bash
./scripts/build-desktop-extension.sh
```

For a versioned release artifact plus SHA256 output:

```bash
./scripts/release-desktop-extension.sh
```

For this repo, the two client paths are:

- `Claude Code`: keep using [.mcp.json](/Users/erauner/git/side/investigation-poc/.mcp.json) or a managed remote MCP configuration pointed at the controller MCP endpoint.
- `Claude Desktop`: install the generated `.mcpb` and point it at the controller MCP URL you want Desktop users to reach.

For the homelab deployment, that controller URL is the published route for `kagent-controller`, not the raw `investigation-mcp-server` service.

You can also test the Desktop extension locally without Claude Desktop:

```bash
./scripts/test-desktop-extension.sh
```
