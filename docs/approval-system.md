# The approval system

AgentOps never lets a model act on a customer's money. Anything consequential — a refund,
a cancellation — stops at `awaiting_approval` and waits for a named human Supervisor.

> **Status:** the human-approval half of S6 is complete. Durable execution (outbox worker,
> refund ledger writes, executed-action records) is **not** built yet. Today a successful
> approval moves the workflow to `approved_pending_execution` and stops there; no job is
> queued and nothing is executed.

## The core invariant

> No consequential action may execute unless it references a valid, unexpired Supervisor
> approval for the exact proposed action, order, amount and evidence snapshot, and carries
> a unique idempotency key.

The model proposes. Deterministic rules decide what is permissible. A human decides
whether it happens. Those three roles never collapse into one another.

## The approval snapshot

When an agent raises an approval, the system freezes the *basis of the decision* into an
`ApprovalSnapshot` ([snapshot.py](../backend/app/approvals/snapshot.py)) and hashes it with
SHA-256 over canonical, sorted-key JSON.

The snapshot binds the action type, order, requested amount, deterministic maximum,
eligibility outcome, reason codes, rule versions, policy citations, the draft-response hash
and the requester. Two volatile fields (`approval_request_id`, `snapshot_created_at`) are
excluded from the hash so identical decisions hash identically.

Before any approval is granted the stored snapshot is re-verified. If a single
action-relevant byte has changed, the decision is refused with
`approval_snapshot_tampered` — a Supervisor can only ever approve what they were shown.

## Amount limits are derived, not trusted

The requested amount and its ceiling are computed at request time from live order data via
the S2 refund rule, not copied from the model's proposal. An approval cannot be created
above the deterministic maximum, and at decision time the approved amount must satisfy:

```
0 < approved_amount <= requested_amount <= maximum_allowed_amount
```

A Supervisor may reduce an amount; nobody may raise one.

## Idempotency: two different keys

| Key | Scope | Purpose |
| --- | --- | --- |
| **Business action key** (`act-<sha256[:32]>`) | action type + order + amount | Makes the *business action* unique. No timestamps, so it is stable across retries. |
| **HTTP `Idempotency-Key`** | key + actor + operation | Makes an *HTTP request* safe to repeat. A replay returns the original outcome; the same key with a different payload is a `409`. |

Editing the response wording leaves the business key unchanged — it is the same refund.
Changing the amount produces a new key, because it is a different action.

## Who may do what

| Action | Support agent | Supervisor |
| --- | --- | --- |
| Raise an approval request | ✅ | ✅ |
| View the queue | ✅ | ✅ |
| Edit the draft response | ✅ (own request) | ✅ |
| Change the amount | ❌ | ✅ |
| Approve / reject | ❌ | ✅ |
| Cancel (withdraw) | ✅ (own request) | ✅ |

**Self-approval is impossible.** A Supervisor who raised a request cannot decide it, even
though their role permits deciding in general — checked against
`requester_user_id`, independently of role.

## Decision outcomes

| Decision | Approval status | Workflow state |
| --- | --- | --- |
| Approve | `approved` | `approved_pending_execution` (nothing queued yet) |
| Reject | `rejected` | `approval_rejected` (terminal) |
| Cancel | `cancelled` | `awaiting_agent` — the proposal is withdrawn, not refused |
| Expire | `expired` | `approval_expired` (paused, re-requestable) |

Every decision writes an append-only `approval_decisions` row recording the actor, their
role, the previous and new status, both amounts and the reason. Decisions are never updated
or deleted, so the audit trail is the record.

Exactly one decision can win: the approval row is locked with `SELECT … FOR UPDATE` for the
whole transaction, and the status transition is validated
(`pending → approved | rejected | cancelled | expired | superseded` only). A second
Supervisor deciding concurrently gets `approval_not_pending`.

## Workflow integration

Approvals only apply to `support-ticket-v2`, which adds the human/worker edges on top of
the frozen v1 graph. `support-ticket-v1` is unchanged and still replayable.

Because `awaiting_approval` is served by the `__human__` handler, the normal runner can
never leave it on its own — only a human decision moves the run forward. Each decision
writes a workflow step *and* a checkpoint carrying the approval id, resulting status and
the deciding actor, so a run's history shows exactly who authorised what.

## API

All routes require a bearer token. The actor always comes from the token, never the body.

| Method | Path |
| --- | --- |
| `GET` | `/api/approvals` — queue: expiring soonest, then highest risk, then oldest |
| `GET` | `/api/approvals/{id}` |
| `GET` | `/api/approvals/{id}/decisions` |
| `POST` | `/api/proposed-actions/{action_id}/approval` |
| `PATCH` | `/api/approvals/{id}` |
| `POST` | `/api/approvals/{id}/approve` · `/reject` · `/cancel` |

Responses are PII-safe: identifiers, amounts, evidence hashes and citations — never
customer contact details. Errors carry a stable code (`approval_expired`,
`approval_self_decision_forbidden`, …) mapped to `403`/`404`/`409`/`422`.

Decision responses include `outbox_job_created`, which is `false` throughout this
increment — the field exists so the change is visible when execution lands.

## CLI

```bash
make approval-list                     # the queue
make approval-inspect  APPROVAL=<uuid> # snapshot hash, limits, citations
make approval-decisions APPROVAL=<uuid>
make approval-approve  APPROVAL=<uuid> AS=super.priya@meridian.example
make approval-reject   APPROVAL=<uuid> AS=<email> REASON="outside policy"
make approval-expire                   # sweep past-deadline approvals
```

Every command runs as a named seeded user and enforces the same role, self-approval and
snapshot checks as the API.

## Worked example

```
$ make workflow-demo FIXTURE=DEMO-REFUND-APPROVAL-001
state / status    awaiting_approval / paused

$ python -m app.approvals.cli request <action> --as agent.amara@meridian.example
approval 7dcb9054… status=pending

$ make approval-list
- 7dcb9054… pending  high  request_supervisor_refund_approval  £59.00 max=£59.00

$ python -m app.approvals.cli approve 7dcb9054… --as agent.amara@meridian.example
approval_role_forbidden: missing permission: approval_decide

$ python -m app.approvals.cli approve 7dcb9054… --as super.priya@meridian.example
approved 7dcb9054… amount=£59.00 workflow=approved_pending_execution outbox_job_created=False

$ python -m app.approvals.cli reject 7dcb9054… --as super.george@meridian.example --reason "…"
approval_not_pending: approval is approved
```

## Still to build

The outbox worker, refund and cancellation execution, executed-action records, refund
ledger writes and dead-letter handling. The approval layer is deliberately shaped so that
work extends the existing decision transaction rather than replacing it.
