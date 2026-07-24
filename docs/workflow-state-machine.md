# Workflow State Machine (S5)

`support-ticket-v1` is an explicit, versioned state machine. Branches are chosen by step
handlers from **typed results** (never arbitrary model text), and every produced destination
is validated against the transition table before it is persisted. Invalid transitions
terminate the run safely.

## State catalogue

| Partition | States |
| --- | --- |
| **Active** (processing) | `received`, `validating`, `sanitising`, `classifying`, `extracting_identifiers`, `resolving_customer`, `resolving_order`, `retrieving_order_data`, `retrieving_policy`, `evaluating_rules`, `summarising_evidence`, `drafting_response`, `calculating_route` |
| **Paused** (awaits a human/external event) | `awaiting_agent`, `awaiting_approval`, `needs_information`, `escalated` |
| **Terminal** (cannot continue automatically) | `blocked`, `failed_validation`, `failed_dependency`, `failed_model`, `cancelled`, `resolved_without_action` |

There is **no** `executing_refund` / `executing_cancellation` state — consequential
execution is S6. The partition is disjoint and complete (property-tested).

## Status catalogue

`pending`, `running`, `paused`, `completed`, `failed`, `cancelled`. Status is derived from
state so the two never disagree (e.g. state `awaiting_approval` ⇒ status `paused`; state
`blocked` ⇒ status `failed`; `resolved_without_action` ⇒ `completed`; `cancelled` ⇒
`cancelled`).

## Transition diagram

```mermaid
stateDiagram-v2
    [*] --> received
    received --> validating
    validating --> sanitising
    validating --> failed_validation
    sanitising --> classifying
    classifying --> extracting_identifiers
    classifying --> escalated
    extracting_identifiers --> resolving_customer
    resolving_customer --> resolving_order
    resolving_customer --> needs_information
    resolving_customer --> escalated
    resolving_order --> retrieving_order_data
    resolving_order --> needs_information
    resolving_order --> escalated
    resolving_order --> blocked
    retrieving_order_data --> retrieving_policy
    retrieving_order_data --> needs_information
    retrieving_order_data --> failed_dependency
    retrieving_policy --> evaluating_rules
    retrieving_policy --> escalated
    evaluating_rules --> summarising_evidence
    evaluating_rules --> needs_information
    evaluating_rules --> blocked
    summarising_evidence --> drafting_response
    drafting_response --> calculating_route
    calculating_route --> awaiting_agent
    calculating_route --> awaiting_approval
    calculating_route --> needs_information
    calculating_route --> escalated
    calculating_route --> blocked
```

## Entry / exit conditions (branch drivers)

- **resolve_customer** → `needs_information` if the customer cannot be resolved.
- **resolve_order** → `blocked` on a cross-customer ownership mismatch (the other customer's
  order is never revealed); `needs_information` if no order and the category needs one.
- **retrieve_policy** → `escalated` on a policy conflict (never silently resolved) or an
  unsupported consequential policy claim.
- **evaluate_rules** → `blocked` on an ownership block; `needs_information` if the
  deterministic route needs more facts.
- **calculate_route** maps the deterministic `Route` to the final state: `await_agent` →
  `awaiting_agent`, `await_supervisor` → `awaiting_approval`, `needs_information` →
  `needs_information`, `escalate`/`manual_handling` → `escalated`, `blocked` → `blocked`.

## Invalid transitions

`is_valid_transition(source, dest)` gates every destination. A handler that produces an
illegal edge fails the step and terminates the run as `failed_dependency` (internal error).
Paused and terminal states have no outgoing edges (enforced at module load and property-
tested).

## v2 execution states (S6)

`support-ticket-v2` reuses every v1 processing stage and adds the approval/execution
continuation from `awaiting_approval`. These edges are validated by the state machine but
driven by **human decisions and the outbox worker**, never by the normal runner loop —
the runner only auto-advances active processing states, so a v2 run pauses at
`awaiting_approval` exactly like v1.

```mermaid
stateDiagram-v2
    awaiting_approval --> approved_pending_execution: supervisor approves (executable)
    awaiting_approval --> manual_action_required: approves (manual-only)
    awaiting_approval --> approval_rejected: reject
    awaiting_approval --> approval_expired: expire
    awaiting_approval --> awaiting_agent: cancel (withdraw)
    approved_pending_execution --> executing_action: worker claims
    executing_action --> action_succeeded: effect applied (terminal)
    executing_action --> action_failed: technical failure / dead-letter (paused)
    executing_action --> manual_action_required: precondition changed (paused)
    action_failed --> approved_pending_execution: supervisor authorises retry
    action_failed --> manual_action_required
```

`action_succeeded` is a completed terminal state; `action_failed` and
`manual_action_required` are paused (they await a human). Every execution transition writes
a workflow step **and** a checkpoint. `support-ticket-v1` can never enter these states, and
a default replay never executes an effect (see [action-execution.md](action-execution.md)
and [exactly-once-semantics.md](exactly-once-semantics.md)).

## Workflow identity

A workflow definition is keyed by semantic version; both the canonical and the legacy name
resolve to it, so early v2 runs persisted as `support-ticket-v1@2.0.0` stay fully readable
and replayable while new v2 runs use the canonical `support-ticket-v2 @ 2.0.0`. The CLI and
APIs display the canonical `name @ version` regardless of how a row was stored.
