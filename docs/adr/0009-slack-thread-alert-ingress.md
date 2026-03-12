# ADR 0009: Treat Alert Threads As First-Class Investigation Ingress

- Status: Proposed
- Date: 2026-03-11
- Related ADRs:
  - `docs/adr/0005-unified-ingress-and-subject-resolution.md`
  - `docs/adr/0007-alertmanager-as-deterministic-alert-state-evidence.md`
  - `docs/interface-contract.md`

## Context

The current Slack integration can now do all of the following:

- receive a direct mention in a Slack thread
- read thread history when a Slack user token is available
- pass the current mention plus prior thread content to the investigation agent
- continue a conversation across follow-up replies in the same thread

That is enough for a useful threaded operator workflow, but it does not yet make one important case deterministic:

> an operator replies to an existing alert thread, tags the agent, and expects investigation to start in alert mode automatically.

This matters because many real channels already contain alert-originated threads from Alertmanager or similar systems.
Operators do not want a separate integration hop before investigation can begin.
They want to reply in place and say:

- `@agent investigate this`
- `@agent what is failing here?`
- `@agent investigate the error above`

In those cases, the alert content may already exist in the thread root or earlier replies.
Requiring the operator to restate the alert shape manually reduces the value of the Slack flow.

Today the product behavior is only partially sufficient:

- thread context can be included
- explicit alert-shaped text can already be routed correctly by the unified investigate entrypoint
- but replying inside an alert thread does not yet deterministically force alert-aware ingress

That leaves too much to incidental phrasing in the current mention text.

## Decision

Slack thread replies should become a first-class ingress mode with explicit alert-thread detection.

When a Slack mention occurs inside a thread, the Slack adapter should:

1. read a bounded transcript for the thread
2. classify whether the thread contains explicit alert-shaped material
3. if yes, invoke the unified `Investigate` entrypoint in alert mode
4. otherwise invoke the unified `Investigate` entrypoint in generic mode

This means the product surface remains one user-facing action:

- `Investigate`

But Slack becomes responsible for supplying a stronger ingress hint when the surrounding thread already contains alert facts.

## What Counts As An Alert Thread

A thread should be treated as alert-shaped only when the transcript contains explicit alert evidence, not vague operational prose.

Strong alert signals include:

- `alertname=...`
- `alertname: ...`
- Alertmanager-style labels and annotations blocks
- explicit `status: firing` or `status: resolved`
- known alert payload fields such as `startsAt`, `endsAt`, `generatorURL`
- a structured alert summary posted by an alert-forwarding bot

Weak signals that should not by themselves force alert mode include:

- `prod is broken`
- `seeing errors`
- `this looks bad`
- arbitrary incident chatter without structured alert evidence

The Slack adapter should prefer false negatives over false positives.
If thread classification is uncertain, fall back to generic mode.

## Adapter Responsibilities

The Slack adapter should own only transport-local concerns:

- obtain the current message
- obtain a bounded thread transcript
- remove Slack UI noise such as `Thinking...` placeholders or token footers
- normalize mentions and obvious message formatting artifacts
- classify whether the thread contains explicit alert-shaped content
- prepend the correct ingress directive

The Slack adapter should not:

- reimplement full alert extraction semantics
- become the source of truth for cluster or namespace resolution
- decide final diagnosis logic
- replace backend alert normalization

Slack should provide an ingress hint, not its own alert investigation engine.

## Backend Responsibilities

Backend-owned alert semantics remain unchanged:

- alert extraction rules stay product-owned
- subject resolution stays product-owned
- planner/runtime behavior stays product-owned
- report composition stays product-owned

The backend should continue to normalize both Slack-derived alert mode and direct alert-shaped user input through the same internal ingress pipeline.

## Bounded Transcript Rule

Slack thread ingestion must remain bounded.

The adapter should:

- fetch only a capped number of recent relevant messages
- always include the thread root when available
- prefer messages authored by humans or alert-forwarding bots
- exclude prior temporary placeholder messages from the investigation bot itself

The goal is:

- enough thread history to recover alert context
- without turning Slack transcripts into unbounded prompt stuffing

## Preferred User Experience

The intended operator flow is:

1. Alertmanager posts an alert into Slack.
2. An operator replies in the thread and tags the investigation bot.
3. The bot automatically recognizes the thread as alert-shaped.
4. The bot starts investigation without requiring the operator to restate the alert payload.

Example:

- Thread root:
  - `PodCrashLooping firing for pod/crashy in namespace kagent-smoke`
- Operator reply:
  - `@kagent-kind-demo investigate this`

Preferred behavior:

- the Slack adapter classifies the thread as alert-shaped
- the unified investigate entrypoint receives alert mode plus the operator request and bounded thread context
- the resulting investigation focuses on the alert subject rather than treating the mention as vague freeform prose

## Non-Goals

This ADR does not require:

- a direct Alertmanager-to-controller integration
- storing all Slack thread history forever
- full Slack incident management semantics
- automatic mutation or remediation actions
- treating every threaded conversation as an alert

## Consequences

Benefits:

- matches how operators already work in alert-heavy Slack channels
- reduces repeated copying and pasting of alert payloads
- keeps one primary `Investigate` action while improving Slack ergonomics
- preserves backend ownership of investigation semantics

Costs and risks:

- Slack adapter complexity increases
- false alert-thread classification is possible
- transcript trimming must stay disciplined to avoid prompt bloat
- user-token thread reads remain a deployment prerequisite for full fidelity in channel threads

## Follow-Up Work

The next implementation slice should:

1. define a small alert-thread classifier in the Slack bot runtime
2. classify thread transcripts using explicit alert-shape rules only
3. prepend `[INVESTIGATION_ENTRYPOINT]=alert` when the thread is clearly alert-shaped
4. otherwise prepend `[INVESTIGATION_ENTRYPOINT]=generic`
5. add regression tests for:
   - structured alert thread root plus vague operator reply
   - non-alert thread plus vague operator reply
   - thread with mixed incident chatter and one explicit alert payload
   - long thread where transcript trimming still preserves the alert root

Success means an operator can reply to an existing alert thread with a short mention and reliably trigger alert-aware investigation without needing a separate external integration.
