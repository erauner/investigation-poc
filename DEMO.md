# Investigation MCP Demo

This document is the repo-local demo and validation guide for the `investigation-poc` stack.

Use this repo when you want to prove the full local path with kind:

`kind` -> `kagent` -> `investigation-agent` -> `investigation-mcp-server`

Keep this file aligned with the code and local manifests in this repository.

## Goal

Show that the local kind workflow can:

1. install the controller and the custom investigation MCP server
2. deploy a healthy and unhealthy smoke workload
3. exercise the standard investigation path end to end
4. verify the composed five-section incident report

## Prerequisites

- `kind`
- `kubectl`
- `helm`
- `kagent` CLI
- `OPENAI_API_KEY`

Recommended checks:

```bash
kind version
kubectl config current-context
kagent version
```

If your current `kubectl` context is not `kind-investigation`, run `make kind-up` first.

## One-Repo Kind Flow

Everything below is intended to run from this repository only.

## Single-Run Validation

If you want the highest-signal local check without manually stepping through the demo, run:

```bash
OPENAI_API_KEY=sk-... make kind-validate
```

This target:

- creates or reuses the local kind cluster
- installs `kagent` and the local investigation stack
- deploys the smoke workload
- runs the standard workload prompt
- runs the explicit `build_investigation_report` prompt
- verifies the five required sections are present
- fails if `Limitations` leaks correlated-change notes

For multi-cluster routing validation without changing the single-cluster contract lane, run:

```bash
make kind-validate-multi
```

### 1. Create or reuse the local cluster

```bash
make kind-up
```

This target creates the `investigation` kind cluster if needed and switches `kubectl` to `kind-investigation`.

### 2. Install kagent and the investigation stack

```bash
OPENAI_API_KEY=sk-... make kind-install-kagent
```

This installs:

- `kagent-crds`
- `kagent`
- the local investigation MCP server and RBAC from `k8s/`
- the local `investigation-agent`

### 3. Deploy the smoke workload

```bash
make kagent-smoke-apply
```

This creates:

- `kagent-smoke/crashy`
- `kagent-smoke/whoami`

`crashy` is expected to fail and enter a restart loop.

### 4. Run the standard investigation prompt

```bash
make kagent-smoke-test TASK="Investigate the unhealthy pod in namespace kagent-smoke. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step."
```

Expected result:

- the agent resolves the unhealthy workload
- the answer contains five sections
- `Evidence` contains current-state evidence
- `Related Data` contains only correlated changes or the empty-note
- `Limitations` does not repeat the empty correlated-change note

### 5. Cleanup

```bash
make kagent-smoke-clean
```

Optional full teardown:

```bash
make kind-down
```

## Demo Prompts

### Standard workload path

```text
Investigate the unhealthy pod in namespace kagent-smoke. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.
```

### Explicit top-level report path

```text
Use build_investigation_report for the investigation. Investigate the unhealthy pod in namespace kagent-smoke and return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.
```

### Alert-shaped workload prompt

```text
Investigate alert PodCrashLooping for pod crashy-abc123 in namespace kagent-smoke. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.
```

### Service-style prompt

```text
Investigate high latency for service/whoami in namespace kagent-smoke. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.
```

## What “Good” Looks Like

- `Diagnosis` identifies the current failure mode
- `Evidence` is based on current-state findings, events, logs, and metrics
- `Related Data` is isolated from `Limitations`
- empty `Related Data` is explicit instead of padded
- the agent stays read-only

## Notes

- This local kind flow is the preferred place to validate prompt, contract, and report-composition changes before rolling them into `homelab-k8s`.
- Production GitOps manifests still live in `homelab-k8s`, but the fast feedback loop should stay here.
