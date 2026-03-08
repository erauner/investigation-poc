# Pre-Fork Plan

This document is the concrete cleanup plan before creating a Medallia-oriented fork from this repo.

The goal is not to add more features here. The goal is to make the current repo easier to fork cleanly by separating:

- reusable investigation platform code
- homelab deployment/runtime choices
- future Medallia-specific domain behavior

## Desired End State

After this pass, it should be obvious which parts of the repo:

- fork unchanged as platform core
- get replaced by environment overlays
- become Medallia/domain packs

## Work Order

Do the work in this order. Earlier steps reduce confusion in later ones.

### 1. Add explicit architecture ownership docs

Goal:
- Make the platform/runtime/domain boundary visible in the repo without reading the whole codebase.

Tasks:
- Add a short architecture section to [README.md](/Users/erauner/git/side/investigation-poc/README.md) that names:
  - platform core
  - runtime adapters
  - overlay/domain content
- Add a small doc if needed, such as `ARCHITECTURE.md`, if the README summary becomes too dense.

Primary files:
- [README.md](/Users/erauner/git/side/investigation-poc/README.md)
- [src/investigation_service/models.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/models.py)
- [src/investigation_service/reporting.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/reporting.py)
- [src/investigation_service/synthesis.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/synthesis.py)
- [src/investigation_service/correlation.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/correlation.py)

Acceptance check:
- A new reader can tell in under 2 minutes which code is meant to fork unchanged.

### 2. Neutralize public-facing homelab naming

Goal:
- Keep homelab-specific names in overlays/config, not in reusable client or platform surfaces.

Tasks:
- Audit for `homelab-*`, `erauner-*`, and other local branding in public-facing names.
- Rename only the identifiers that will look wrong in a shared/internal fork.
- Leave clearly overlay-owned values alone if they are already isolated.

Primary targets:
- [desktop-extension/manifest.json](/Users/erauner/git/side/investigation-poc/desktop-extension/manifest.json)
- [desktop-extension/server/index.js](/Users/erauner/git/side/investigation-poc/desktop-extension/server/index.js)
- [k8s/agent.yaml](/Users/erauner/git/side/investigation-poc/k8s/agent.yaml)
- [k8s/investigation-skill-configmap.yaml](/Users/erauner/git/side/investigation-poc/k8s/investigation-skill-configmap.yaml)
- [README.md](/Users/erauner/git/side/investigation-poc/README.md)

Examples to review:
- `incident-triage`
- `homelab-investigation-remote`

Acceptance check:
- Reusable docs and clients read as neutral platform assets instead of copied homelab artifacts.

### 3. Lock the rule that domain behavior enters through guidelines first

Goal:
- Prevent the future Medallia fork from turning core logic into a product-specific codebase too early.

Tasks:
- Review current logic for hidden environment or product assumptions.
- Add comments or docs where needed to make the rule explicit:
  - generic Kubernetes reasoning belongs in core
  - product/domain behavior belongs in guidelines first
  - code adapters are only added when guidelines become insufficient

Primary review files:
- [src/investigation_service/analysis.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/analysis.py)
- [src/investigation_service/tools.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/tools.py)
- [src/investigation_service/reporting.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/reporting.py)
- [src/investigation_service/guidelines.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/guidelines.py)

Acceptance check:
- There is a clear written rule for where Medallia-specific logic should go first.

### 4. Treat guidelines and cluster registry as first-class extension content

Goal:
- Make it clear that environment/domain specialization is primarily data/config driven.

Tasks:
- Document the ownership of:
  - guideline files
  - cluster registry data
  - environment-specific runtime config
- Keep those files obviously swappable.
- Avoid mixing domain-specific text into generic code unless necessary.

Primary files:
- [k8s/guidelines-configmap.yaml](/Users/erauner/git/side/investigation-poc/k8s/guidelines-configmap.yaml)
- [k8s/cluster-registry-configmap.yaml](/Users/erauner/git/side/investigation-poc/k8s/cluster-registry-configmap.yaml)
- [k8s/configmap.yaml](/Users/erauner/git/side/investigation-poc/k8s/configmap.yaml)
- [src/investigation_service/guidelines.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/guidelines.py)

Acceptance check:
- A Medallia fork can replace guidelines and cluster metadata without touching the core report pipeline first.

### 5. Expand cluster metadata intentionally

Goal:
- Treat cluster selection as product surface, not just runtime plumbing.

Tasks:
- Review the current cluster registry shape.
- Decide which additional fields should exist before the fork, even if some stay unused at first.

Candidate metadata fields:
- `environment`
- `domain_tags`
- `safe_builtin_followup_tools`
- `allowed_namespaces`
- `observability_provider`

Primary file:
- [k8s/cluster-registry-configmap.yaml](/Users/erauner/git/side/investigation-poc/k8s/cluster-registry-configmap.yaml)

Acceptance check:
- Cluster aliases remain the external API, and the schema is ready for non-homelab environments.

### 6. Define the future handoff boundary

Goal:
- Prepare for Slack, automation, or multi-agent follow-up without coupling that work into the current report contract.

Tasks:
- Decide whether `InvestigationReport` remains the only output or whether a future typed handoff model is needed.
- If a second model is likely, document it before implementing anything.

Candidate future model:
- `OperationalHandoff`
- or `InvestigationDecision`

Candidate fields:
- `cluster`
- `target`
- `diagnosis`
- `confidence`
- `likely_cause`
- `safe_next_actions`
- `suggested_owner`
- `escalation_needed`

Primary files:
- [src/investigation_service/models.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/models.py)
- [README.md](/Users/erauner/git/side/investigation-poc/README.md)

Acceptance check:
- The repo has a written answer for what the next non-human consumer should consume.

### 7. Preserve the two-MCP-surface distinction everywhere

Goal:
- Avoid regressing into confusion between controller MCP and raw tool MCP.

Tasks:
- Keep the boundary explicit in docs and admin handoff material.
- When adding new clients, make them choose one path intentionally:
  - controller-backed agent path
  - raw tool-server path

Definitions:
- `kagent-controller` = user-facing controller MCP path
- `investigation-mcp-server` = lower-level tool server used by the custom agent

Primary files:
- [README.md](/Users/erauner/git/side/investigation-poc/README.md)
- [desktop-extension/README.md](/Users/erauner/git/side/investigation-poc/desktop-extension/README.md)
- [desktop-extension/ADMIN_HANDOFF.md](/Users/erauner/git/side/investigation-poc/desktop-extension/ADMIN_HANDOFF.md)

Acceptance check:
- A reader cannot mistake the Desktop extension for a direct proxy to the raw investigation tool server.

### 8. Keep the Desktop extension client-only

Goal:
- Stop the extension from becoming an accidental second backend.

Tasks:
- Keep the extension surface narrow.
- Keep all diagnosis and report composition in the controller/agent/backend path.
- Resist adding business logic to the extension unless it is purely transport/client UX glue.

Primary files:
- [desktop-extension/server/index.js](/Users/erauner/git/side/investigation-poc/desktop-extension/server/index.js)
- [desktop-extension/manifest.json](/Users/erauner/git/side/investigation-poc/desktop-extension/manifest.json)

Acceptance check:
- The extension remains replaceable without changing the core investigation platform.

### 9. Write the fork map explicitly

Goal:
- Remove ambiguity about what should be copied as-is versus rewritten.

Tasks:
- Add a short section to this doc or a fork-specific doc listing:
  - carry over unchanged
  - replace in the fork
  - likely new Medallia additions

Carry forward mostly unchanged:
- [src/investigation_service/models.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/models.py)
- [src/investigation_service/routing.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/routing.py)
- [src/investigation_service/event_fingerprints.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/event_fingerprints.py)
- [src/investigation_service/synthesis.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/synthesis.py)
- [src/investigation_service/reporting.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/reporting.py)
- [src/investigation_service/correlation.py](/Users/erauner/git/side/investigation-poc/src/investigation_service/correlation.py)

Replace in the fork:
- [k8s/guidelines-configmap.yaml](/Users/erauner/git/side/investigation-poc/k8s/guidelines-configmap.yaml)
- [k8s/cluster-registry-configmap.yaml](/Users/erauner/git/side/investigation-poc/k8s/cluster-registry-configmap.yaml)
- [k8s/configmap.yaml](/Users/erauner/git/side/investigation-poc/k8s/configmap.yaml)
- [k8s/agent.yaml](/Users/erauner/git/side/investigation-poc/k8s/agent.yaml)
- [desktop-extension/manifest.json](/Users/erauner/git/side/investigation-poc/desktop-extension/manifest.json)

Likely Medallia additions:
- product/domain guideline packs
- richer cluster metadata
- artifact-specific adapters if guidelines are insufficient
- future handoff model for Slack/automation

Acceptance check:
- Fork setup can start from a written file map instead of tribal knowledge.

## What Not To Do In This Repo Before Forking

- Do not add write actions.
- Do not build the full Slack automation path yet.
- Do not hardcode product-specific operator logic into the platform core.
- Do not move backend logic into the Desktop extension.
- Do not overbuild the extension beyond the current thin controller-backed client shape.

## Recommended Scope For The Pre-Fork Pass

Keep this pass small:

- one architecture clarification pass
- one naming cleanup pass
- one config/domain-boundary pass
- one fork map

If the work starts turning into a new product roadmap, stop and fork instead.
