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

Before a fresh validation run, you can clear stale repo-related kind clusters with:

```bash
make kind-preflight-clean
```

Validation scripts now reuse an existing ready `kind-investigation` stack automatically when one is already running, and only tear the cluster down when the script created it itself.

If you want to keep a newly created cluster alive after a validation run for more prompt or agent iteration:

```bash
OPENAI_API_KEY=sk-... KEEP_CLUSTER=1 make kind-validate
```

The same warm-cluster behavior applies to:

```bash
OPENAI_API_KEY=sk-... make kind-validate-operator
OPENAI_API_KEY=sk-... make kind-validate-alert-entry
```

For the first multi-cluster routing validation path, use:

```bash
make kind-validate-multi
```

This exercises the local kind stack, deploys the smoke workload, runs the standard investigation prompt, runs an explicit planner-led prompt, and fails if the five-section report contract regresses.

For the first operator-backed validation path in the same kind cluster, use:

```bash
OPENAI_API_KEY=sk-... make kind-validate-operator
```

This reuses the existing investigation stack, builds and installs `homelab-operator` from `../homelab-operator`, applies a minimal `operator-smoke` fixture, and validates the same five-section report contract against an unhealthy operator-managed pod.

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
For `make kind-install-kagent`, `make kind-validate`, and `make kind-validate-operator`, the local kind flow now builds `investigation-poc:local` from the current checkout, loads it into kind, and rewrites the local overlay to use that image instead of `ghcr.io/erauner/investigation-poc:latest`.
The default `local-kind` overlay now also includes an in-cluster Prometheus plus kube-state-metrics bundle. If you want the older host-backed path instead, use `K8S_OVERLAY=k8s-overlays/local-kind-host-prometheus`.

## MCP topology

There are two different MCP surfaces in this setup, and they serve different roles:

- `investigation-mcp-server`: the repo-local tool server defined in `k8s/`. It exposes the low-level investigation tools that the custom agent uses behind the scenes.
  The intentional agent-visible subset is now planner-led: canonical control-plane tools plus a narrow owned evidence-plane set.
- `kagent-controller`: the higher-level controller MCP endpoint. It exposes agent-oriented tools such as `list_agents` and `invoke_agent`.

That distinction matters for clients:

- `Claude Code` can talk directly to the controller MCP endpoint and then invoke `kagent/incident-triage`.
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

Local metrics validation:

```bash
OPENAI_API_KEY=sk-... make kind-validate-metrics
```

Operator-backed local contract validation:

```bash
OPENAI_API_KEY=sk-... make kind-validate-operator
```

Host-routed multi-cluster validation:

```bash
make kind-validate-multi
```

Use [DEMO.md](/Users/erauner/git/side/investigation-poc/DEMO.md) as the repo-local source of truth for:

- the end-to-end kind demo
- the single-run local validation entrypoint
- the planner-led investigation prompts
- expected output semantics for Evidence, Related Data, and Limitations

This repo should be sufficient for local validation. The production GitOps rollout still happens from `homelab-k8s`, but the fast feedback loop should stay here.

If you are preparing to fork this into a non-homelab environment, use [PRE_FORK_PLAN.md](/Users/erauner/git/side/investigation-poc/PRE_FORK_PLAN.md) as the repo-local checklist for separating platform code, runtime overlays, and domain-specific behavior.

Shared client-facing semantics for Desktop and Claude Code live in [docs/interface-contract.md](/Users/erauner/git/side/investigation-poc/docs/interface-contract.md).

The current architecture direction for the investigation workflow is documented in [docs/adr/0001-artifact-oriented-investigation-workflow.md](/Users/erauner/git/side/investigation-poc/docs/adr/0001-artifact-oriented-investigation-workflow.md).

The next local metrics implementation phase is tracked in [docs/prometheus-kind-checklist.md](/Users/erauner/git/side/investigation-poc/docs/prometheus-kind-checklist.md).

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

## Claude Code plugin

This repo also includes a repo-local Claude Code plugin marketplace for testing a slash-command entrypoint before any future fork:

- [claude-code-marketplace/README.md](/Users/erauner/git/side/investigation-poc/claude-code-marketplace/README.md)

The included plugin is intentionally thin:

- shared plugin command: `/investigation-tools:investigate`
- shared plugin alert command: `/investigation-tools:investigate-alert`
- MCP path: `kagent-controller`
- agent path: `kagent/incident-triage`
- explicit alert command routes through the alert-specific top-level backend path
  The planner-led agent path now treats `render_investigation_report` as the canonical final report surface rather than teaching report-first compatibility wrappers.

Local test flow:

1. Port-forward the controller MCP endpoint:

```bash
./scripts/port-forward-controller-mcp.sh
```

For the plugin-dir or marketplace path, set the plugin MCP URL in your shell first:

```bash
export INVESTIGATION_REMOTE_MCP_URL="http://127.0.0.1:8083/mcp"
```

2. In Claude Code, add the repo-local marketplace and install the plugin:

```text
/plugin marketplace add ./claude-code-marketplace
/plugin install investigation-tools@investigation-poc-marketplace
```

3. Restart Claude Code and run:

```text
/investigation-tools:investigate Investigate the unhealthy pod in namespace kagent-smoke.
```

Alert-shaped example:

```text
/investigation-tools:investigate-alert Investigate PodCrashLooping for pod crashy-abc123 in namespace kagent-smoke.
```

For faster project-local iteration without plugin namespacing, this repo also includes a standalone command:

```text
/investigate Investigate the unhealthy pod in namespace kagent-smoke.
```

The local alert command is:

```text
/investigate-alert Investigate PodCrashLooping for pod crashy-abc123 in namespace kagent-smoke.
```

That local command lives at [.claude/commands/investigate.md](/Users/erauner/git/side/investigation-poc/.claude/commands/investigate.md). Per the current Claude Code behavior we just tested, `.claude/commands/` is the right path for user-invoked plain slash commands, while plugins are the right path once you want something shareable and versioned.

This repo also includes an optional skill form at [.claude/skills/investigation-helper/SKILL.md](/Users/erauner/git/side/investigation-poc/.claude/skills/investigation-helper/SKILL.md) if you want Claude to auto-discover the capability from natural-language requests, but that is not the primary manual entrypoint.

Once the standalone command UX is stable, the next local packaging test should use a plugin directory directly:

```bash
claude --plugin-dir ./claude-code-marketplace/investigation-tools
```

Then invoke the namespaced plugin command:

```text
/investigation-tools:investigate Investigate the unhealthy pod in namespace kagent-smoke.
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
