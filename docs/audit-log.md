# The audit log

Every security- and consequence-relevant event AgentOps takes is recorded in an immutable,
**hash-chained** audit log. Deleting or altering any event breaks the chain and is
detectable — the log is tamper-evident, not merely append-only.

## What is audited

| Area | Events |
| --- | --- |
| Authentication | login succeeded / failed |
| Approvals | requested, approved, rejected, cancelled, expired, retry authorised |
| Execution | outbox job created, action executed, action failed, dead-lettered, manual required |

Each row ([audit.py](../backend/app/models/audit.py)) records the event type, actor and
role, subject, correlation id, a PII-safe summary and metadata, timestamps, and the chain
hashes. Metadata carries identifiers, statuses and hashes only — never a customer message,
contact detail, token or secret.

## The hash chain

```mermaid
flowchart LR
    G[genesis 000…] --> E1[#1 entry_hash = sha256(fields + genesis)]
    E1 --> E2[#2 entry_hash = sha256(fields + #1.hash)]
    E2 --> E3[#3 entry_hash = sha256(fields + #2.hash)]
```

`entry_hash = sha256(canonical(entry_without_hashes) + previous_hash)`
([hashing.py](../backend/app/audit/hashing.py)). Verification walks the chain in sequence
order, recomputing each hash and checking each `previous_hash` link
([repository.py](../backend/app/audit/repository.py)); a changed summary, altered metadata,
deleted row or sequence gap all fail verification and name the offending sequence.

Appends serialise on a transaction-scoped Postgres advisory lock so the monotonic sequence
and the chain stay consistent under concurrent writers, without locking the whole table.

## Written in the same transaction

The audit write uses the **caller's session**, so an approval decision and its audit row,
or an executed action and its audit row, **commit together** — there is never a
consequential action without its audit record, and a rollback drops both. This is enforced
by the observability evaluation's `consequential_action_without_audit` gate and a rollback
test.

## Reading it

Authenticated, read-only, and PII-safe. Supervisors (`outbox_inspect`) read the full trail
and run chain verification; anyone with `action_status_read` can follow one ticket's
journey by correlation id. No endpoint writes or mutates an audit event.

```text
GET  /api/audit                       # list (supervisor)
GET  /api/audit/{event_id}            # one event (supervisor)
GET  /api/audit/verify                # chain verification (supervisor)
GET  /api/audit/correlation/{id}      # a ticket's journey (agent + supervisor)
```

```bash
python -m app.audit.cli list
python -m app.audit.cli verify-chain
python -m app.audit.cli trace <correlation-id>
make audit-list / make audit-verify / make audit-trace COR=<id>
```

## Retention

Audit events are never deleted within the retention window, and any deletion is itself
audited. Retention windows and log rotation are configurable and documented in
[production-reliability.md](production-reliability.md#retention-and-slos). The audit log is
the durable record; it outlives ephemeral logs and metrics.
