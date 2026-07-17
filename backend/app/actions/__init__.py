"""Consequential action execution (S6): simulated refunds and order cancellations.

Effects are **simulated** — no real payment provider, Shopify, carrier or email is
contacted. Each effect executes exactly once via unique idempotency keys and a single
transaction, after final deterministic revalidation of every mutable precondition.
"""
