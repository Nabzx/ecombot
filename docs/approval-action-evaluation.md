# Approval / action safety evaluation

The S6 evaluation proves — by *actually executing* the approval → outbox → simulated-effect
path — that every unsafe outcome has rate zero. It is offline, deterministic and needs no
internet, hosted model or external service.

Run it:

```bash
make eval-approvals                       # in Docker
python -m app.actions.evaluation          # directly
```

A JSON report is written under `evaluations/reports/approvals_actions/`; the process exits
non-zero if any hard gate is breached.

## Dataset

`evaluations/datasets/approvals_actions_v1.json` enumerates **87 cases** (≥ 75 required)
across six categories:

| Category | Cases |
| --- | --- |
| Approval → outbox | 8 |
| Refund execution | 27 |
| Cancellation | 9 |
| Reliability | 20 |
| Workflow | 11 |
| API & security | 12 |

They cover the full matrix: refund amounts from £0.01 to £250.01, exact item/balance limits
and one-penny overshoots, prior-refund balances, concurrent refunds, duplicate worker/job,
expiry, snapshot/payload tampering, invalid policy version, cross-customer orders; every
cancellable and non-cancellable order state; crash points, lease expiry, two workers,
retry/dead-letter/retry-authorisation, worker restart; every workflow final state; and the
API/security surface (agent restrictions, no direct execute, unsupported payload version,
PII/secret redaction).

## How the runner works

The runner drives a curated set of **live** scenarios that collectively exercise every hard
gate ([evaluation.py](../backend/app/actions/evaluation.py)). Each scenario seeds isolated
fixtures, starts a real v2 workflow, creates and decides a real approval, processes real
outbox jobs (including injected failures, competing workers and crash recovery), and asserts
the safe outcome. A hard gate counts **unsafe outcomes**, so a correct system reports 0 for
all of them.

## Hard gates (all must be 0.00)

| Hard gate | Meaning |
| --- | --- |
| approved_action_missing_outbox | an approved executable action left no job |
| outbox_without_valid_approval | a job exists without a valid approval |
| action_without_valid_approval | an effect ran without a valid approval |
| duplicate_business_effect | a business action produced two effects |
| refund_above_item_limit | a refund exceeded the item total |
| refund_above_order_balance | a refund exceeded the remaining balance |
| refund_above_250 | a refund exceeded the £250 ceiling |
| cancellation_after_shipment | a shipped order was cancelled |
| cross_customer_execution | an action ran against another customer's order |
| expired_approval_execution | an expired approval executed |
| tampered_snapshot_or_payload_execution | tampered evidence executed |
| unsupported_action_execution | an unmapped action auto-executed |
| lost_committed_action | a committed effect was lost |
| replay_business_effect | a replay produced a business effect |

## Latest result

```
dataset           approvals-actions-v1
cases             87
scenarios         11/11 passed
hard gates        all 14 = 0
ALL HARD GATES PASS
```

The same gates run inside the ordinary test suite
([test_approval_action_evaluation.py](../backend/tests/test_approval_action_evaluation.py))
and in CI, so a regression fails the build.

## Simulation warning

Every effect is simulated. The evaluation proves the *control* behaviour — atomicity,
exactly-once, revalidation, recovery — not that any money moved. No payment processor,
Shopify, carrier or email service is contacted at any point.
