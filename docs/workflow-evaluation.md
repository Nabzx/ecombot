# Workflow Evaluation (S5)

Offline, deterministic, network-free evaluation of the workflow engine using the mock
provider. Runs named demo cases (exact expected state) and sampled category buckets (must
reach a safe conclusion), plus structural safety checks, and enforces eight hard gates.

## Dataset

`evaluations/datasets/workflows_v1.json` — **66 cases** (7 named demo fixtures + 59 sampled
category cases) authored in `backend/scripts/build_workflow_dataset.py`. Distribution across
categories: order_tracking, delayed/missing delivery, damaged/incorrect item, return, refund,
cancellation, policy question. Named cases carry an exact expected final/paused state and
safety flags (injection, cross-customer, eligible/ineligible).

## Metrics

- **Expected-state accuracy** (named demo cases).
- **Safe-conclusion rate** — every run reaches a paused/terminal state (never stuck active).
- **Route distribution** across `await_agent` / `await_supervisor` / `escalate` / other.

## Hard gates (must equal 0)

| Gate | Result (mock) |
| --- | ---: |
| Unsafe execution rate | 0 |
| Cross-customer continuation rate | 0 |
| Forbidden-action acceptance rate | 0 |
| Policy-conflict silent-resolution rate | 0 |
| Prompt-injection autonomous-continuation rate | 0 |
| Duplicate active workflow rate | 0 |
| Concurrent-processing violation rate | 0 |
| Checkpoint-hash acceptance after tampering | 0 |

The concurrent-processing and checkpoint-tampering gates are exercised by dedicated
structural checks in the runner (two competing claims; a mutated snapshot).

## Measured results (deterministic mock, 66 cases)

| Metric | Value |
| --- | ---: |
| Expected-state accuracy | 1.00 (6/6) |
| Safe-conclusion rate | 1.00 |
| Runs executed | 65 |

Route distribution: `continue_processing` 20, `await_supervisor` 14, `escalate` 16,
none/other 15. **All eight hard gates pass at 0.**

The six demo outcomes are exactly as required: tracking → `awaiting_agent`, refund →
`awaiting_approval`, prompt-injection → `escalated`, cross-customer → `blocked`, return day-30
→ `awaiting_approval` vs day-31 → `awaiting_agent` (ineligible, informational).

## Reproduce

```bash
make eval-workflows            # runs the 66-case eval; non-zero if a hard gate fails
```

A timestamped JSON report is written to `evaluations/reports/workflows/` (git-ignored).

## Known limitations

- The mock provider is a deterministic keyword/rule engine, not a language model: it
  exercises the engine (routing, checkpoints, safety, recovery), not language quality.
- Category-sampled cases assert a **safe conclusion + hard gates**, not an exact state per
  ticket (their expected outcomes are not hand-labelled).
- No consequential action, approval or outbox exists — S5 stops at the review/approval
  boundary.
