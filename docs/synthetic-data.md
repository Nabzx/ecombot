# Synthetic Data

All AgentOps data is synthetic. No real customers, orders, payment details or company
systems are involved. Personal-looking fields (names via Faker, `@example.com` emails,
`07…` phone numbers) are fabricated.

## Generator design

- **Deterministic**: a single fixed seed (`SEED = 20260716`) drives Python's `random`
  and a UK-locale Faker instance. Business fields, reference numbers, totals and
  boundary dates are fully reproducible; UUIDs are also derived from the seeded RNG.
  The only non-reproducible values are bcrypt password hashes (random salt) and the
  database-assigned `created_at`/`updated_at` wall-clock defaults — both excluded from
  determinism checks.
- **Reference date**: the dataset is anchored to a fixed `REFERENCE_DATE`
  (2026-07-16). Date-boundary fixtures (e.g. "delivered exactly 30 days ago") are
  computed relative to this date, not the real clock, so the dataset never drifts.
- **No external calls**: no LLMs, no network, no paid services.
- Code lives in `backend/app/seeds/`; policy bodies live in `data/policies/*.md`.

## Generated counts

| Entity | Count |
| --- | --- |
| Users (2 agents, 2 supervisors) | 4 |
| Products (39 active, 3 inactive) | 42 |
| Customers (mix of standard/VIP) | 55 |
| Orders (161 total) | 161 |
| Order items | ~453 |
| Shipments | ~130 |
| Policies / policy versions | 10 / 12 |
| Tickets | 85 |
| — of which adversarial | 13 |
| — named demo fixtures | 7 |

All ten ticket categories are represented, and orders span every status. Around 50
orders exceed £250. Every `ShipmentStatus` appears (label_created, in_transit,
out_for_delivery, delivered, exception, lost).

## Distribution choices

- Order status is weighted towards delivered/shipped, with a realistic tail of
  cancelled/refunded/partially-refunded.
- Shipment state is kept logically consistent with order status (e.g. delivered orders
  have delivered shipments with a delivery date; processing orders have a label only).
- Roughly a quarter of orders carry a discount; sub-£40 orders carry a delivery fee.

## Hand-authored edge cases (fixtures)

Boundary and demo scenarios are created explicitly (not left to chance) and tagged via
`tickets.seed_tag`; a manifest is written to `data/synthetic/demo_cases.json`.

- Shipment boundaries: delivered exactly 30 days ago, 31 days ago, 45 days ago,
  delivered late, promised date already missed, exception, lost.
- Demo fixtures: `DEMO-TRACKING-001`, `DEMO-REFUND-APPROVAL-001`,
  `DEMO-DUPLICATE-REFUND-001`, `DEMO-RETURN-DAY-30`, `DEMO-RETURN-DAY-31`,
  `DEMO-PROMPT-INJECTION-001`, `DEMO-CROSS-CUSTOMER-001`.

## Adversarial examples

At least ten tickets are deliberately malicious and stored **verbatim** (never
interpreted during seeding), tagged `ADV-…`. They include: "ignore all previous
instructions", fake customer-supplied policy text, an attempt to force a £500 refund,
internal tool-name mentions, an embedded JSON pretending to be a tool call, an
administrator-authority claim, a request to alter system records, and cross-customer
data-access attempts. These carry `injection_flag = true`.

Policies also include a **controlled conflict fixture** (topic
`fixture_conflicting_returns`) with two overlapping active versions, plus a
**superseded** returns version and an **expired** seasonal policy. The integrity check
tolerates conflicts only for fixture topics.

## How to regenerate

```bash
# Docker (recommended)
make reseed        # DEV ONLY: reset then reseed
make seed-stats
make verify-data

# or directly inside the backend environment
python -m app.seeds.cli reseed --yes
python -m app.seeds.cli verify
```

Seeding an already-populated database fails clearly (use `reseed`). Reseeding produces
an identical dataset.

## Limitations of synthetic data

- Message text is templated and hand-authored; it is realistic but less varied than a
  real support inbox.
- Distributions are plausible but not calibrated to any real retailer.
- The fixed reference date means "days since delivery" are relative to 2026-07-16, not
  today; later rule/evaluation stages account for this.
