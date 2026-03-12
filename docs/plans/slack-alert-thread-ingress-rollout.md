# Slack Alert Thread Ingress Rollout Plan

## Summary

Implement alert-thread auto-routing as a targeted Slack-adapter change in the embedded Slack bot runtime at:

- `k8s/optional-slack-a2a/slack-bot-configmap.yaml`

The Slack adapter should:

- always prepend a deterministic `[INVESTIGATION_ENTRYPOINT]=generic|alert` directive
- route to `alert` when either:
  - the latest Slack request is already an explicit alert-form request, or
  - the bounded thread transcript contains strong, explicit alert-shaped signals
- otherwise fall back to `generic`

This remains a Slack-only ingress enhancement.
Alert extraction, target resolution, planning, runtime behavior, and rendering stay backend-owned.

## Current Repo Shape

The implementation plan should assume the current Slack stack now looks like this:

- the combined local Slack rollout goes through `make kind-enable-slack`
- the canonical combined manifest path is `k8s-overlays/local-kind-slack`
- the Slack bot is deployed from a prebuilt image via `Dockerfile.slack-bot`
- the Slack MCP server is deployed from a prebuilt image via `Dockerfile.slack-mcp`
- Slack bot and Slack MCP credentials are split:
  - `slack-bot-credentials`
  - `slack-mcp-credentials`
- `slack-a2a-agent` already exists in the A2A slice by default
- the Slack MCP slice upgrades `slack-a2a-agent` for `send_message_to_slack` support

So the planned change does not need to solve runtime packaging, secret clobbering, or the old A2A/MCP wiring break.
Those issues were already resolved in the current merged Slack stack.

## Why This Is Needed

The current Slack thread flow can:

- receive `app_mention` events
- read bounded thread history when `SLACK_USER_TOKEN` is present
- pass thread context into the agent
- continue multi-turn A2A context per thread

But it still does not deterministically treat existing alert threads as alert-mode ingress.

Today the Slack bot:

- builds a freeform prompt from thread transcript plus latest request
- does not prepend `[INVESTIGATION_ENTRYPOINT]=...`
- does not classify alert-shaped thread context explicitly
- may drop the thread root because transcript rendering still effectively favors the tail

That means a common operator flow is still weak:

1. Alertmanager posts an alert in Slack.
2. An operator replies in-thread with `@agent investigate this`.
3. The system should infer alert mode from the thread, but currently depends too much on incidental phrasing.

## Scope

This slice should stay narrow.

Do:

- update the embedded Slack runtime in `slack-bot-configmap.yaml`
- add focused tests for Slack alert-thread behavior
- add policy assertions that keep Slack aligned with the shared wrapper contract

Do not:

- refactor the Slack bot out of the ConfigMap in this slice
- change backend APIs or planner/runtime tools
- create a cross-language shared routing library
- duplicate backend-owned alert extraction instructions inside Slack

## Desired Runtime Behavior

### App mentions

When the Slack bot handles `app_mention` in a thread:

1. strip the mention from the latest request
2. determine whether the latest request itself is explicit alert form
3. if not, classify the fetched thread transcript conservatively
4. prepend:
   - `[INVESTIGATION_ENTRYPOINT]=alert`, or
   - `[INVESTIGATION_ENTRYPOINT]=generic`
5. include a bounded transcript only when available and useful
6. send the resulting prompt to the existing A2A path

### Slash commands

For `/kind-kagent` and `/kind-shadow`:

- use the same explicit latest-request detection rules
- prepend the directive even without thread history
- do not add transcript fetch for slash commands in this slice

## Explicit Alert Detection Rules

Slack should route to `alert` only on strong, explicit signals.

### Latest-request explicit forms

Match the same user-facing alert forms already used by other clients:

- `Investigate alert <AlertName> ...`
- `alertname=<AlertName>`
- `alertname: <AlertName>`

Guardrails:

- reject candidates containing `/`
- reject:
  - `Backend/...`
  - `Frontend/...`
  - `Cluster/...`

### Thread transcript signals

Treat a thread as alert-shaped only when normalized thread messages contain explicit alert evidence such as:

- `alertname=...`
- `alertname: ...`
- `status: firing`
- `status: resolved`
- `startsAt`
- `endsAt`
- `generatorURL`
- both a labels marker and an annotations marker
- a strict structured summary like:
  - `<AlertName> firing for pod/<name> ...`
  - `<AlertName> resolved for service/<name> ...`

Do not classify from vague prose alone.
Prefer false negatives over false positives.

## Transcript Selection Rules

The transcript logic should remain bounded but preserve the useful parts of the thread.

When rendering transcript context:

- always include the thread root when it contains relevant non-noise text
- force-include any message whose content triggered alert classification
- fill remaining space with the newest relevant messages
- preserve chronological order in the final rendered transcript
- exclude Slack noise and placeholder messages emitted by the investigation bot

The current placeholder set already includes:

- `Reviewing the thread...`
- `Checking recent signals...`
- `Checking the cluster...`
- `Preparing the update...`
- `Working on it...`

That filtering should remain centralized.

## Backend Ownership Boundary

Slack should own only:

- thread fetch
- text cleanup
- bounded transcript rendering
- conservative alert-thread classification
- ingress directive prefixing

Slack should not own:

- canonical alert extraction
- workload/namespace/cluster resolution
- planner-seed derivation
- report section semantics
- final diagnosis logic

Slack is providing a transport-local ingress hint, not replacing backend investigation semantics.

## File Impact

### `k8s/optional-slack-a2a/slack-bot-configmap.yaml`

Primary implementation file.

Expected changes:

- add directive and detection constants
- add small internal helpers for:
  - explicit alertname extraction
  - current-bot message detection
  - thread entrypoint classification
  - prefixed prompt building
- change transcript selection so root + matched alert messages are preserved
- update `build_thread_aware_prompt()` to always return a prefixed prompt
- update `_run_slack_command()` to use the same directive builder without transcript fetch
- keep logging limited to mode/signal metadata, not transcript contents

### `tests/test_slack_alert_ingress.py`

New focused behavior tests for the embedded Slack runtime.

Because the runtime lives inside YAML, the test harness should:

- load `handlers.py` from the ConfigMap payload
- `exec()` it into a test namespace
- stub the minimum Slack/A2A imports needed for helper-level tests

### `tests/test_agent_policy.py`

Add policy assertions that the Slack runtime:

- contains `[INVESTIGATION_ENTRYPOINT]=generic`
- contains `[INVESTIGATION_ENTRYPOINT]=alert`
- aligns on explicit alert markers:
  - `Investigate alert`
  - `alertname=`
  - `alertname:`
- keeps operator target guardrails:
  - `Backend/`
  - `Frontend/`
  - `Cluster/`
- does not embed long backend-owned alert extraction or planner instructions

## Test Plan

### Behavioral tests

Add these scenarios in `tests/test_slack_alert_ingress.py`:

1. structured alert root + vague operator reply -> `alert`
2. non-alert thread + vague operator reply -> `generic`
3. mixed chatter + one explicit alert payload -> `alert`
4. long thread still preserves root alert message
5. missing `SLACK_USER_TOKEN` or Slack history failure -> safe `generic` fallback
6. explicit alert form in latest request without thread context -> `alert`

### Policy tests

Add Slack runtime assertions in `tests/test_agent_policy.py` for directive vocabulary and guardrails.

### Manual validation

Run the current canonical local rollout:

```bash
set -a
source .env.local
set +a
make kind-enable-slack
```

Then validate in Slack:

1. alert-like thread root + vague reply:
   - expected `alert`
2. non-alert thread + vague reply:
   - expected `generic`
3. mixed chatter with explicit alert payload:
   - expected `alert`
4. long thread where the alert root is older:
   - expected preserved alert context

Operational checks:

- ensure only one active `kagent-slack-bot` pod/session is consuming Socket Mode events
- confirm the deployment images are still:
  - `kagent-slack-bot:local`
  - `slack-mcp-server:local`

## Implementation Order

1. Add helper functions inside embedded `handlers.py`
2. Update transcript selection and speaker labeling
3. Make `build_thread_aware_prompt()` always return directive-prefixed prompts
4. Reuse the same explicit latest-request logic in `_run_slack_command()`
5. Add `tests/test_slack_alert_ingress.py`
6. Extend `tests/test_agent_policy.py`
7. Validate with `make kind-enable-slack` and real Slack thread scenarios

## Acceptance Criteria

This slice is done when all of the following are true:

- replying to an alert-shaped Slack thread with a vague tagged request routes to `alert`
- replying to a non-alert thread with vague prose stays `generic`
- explicit alert-form latest requests route to `alert` even without thread history
- transcript rendering preserves the alert root when needed
- Slack runtime tests cover the key scenarios
- policy tests lock Slack to the shared directive vocabulary without copying backend instructions
- local rollout validation succeeds through the current overlay-based Slack path
