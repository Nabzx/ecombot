"""Durable PostgreSQL outbox (S6).

At-least-once delivery with `FOR UPDATE SKIP LOCKED` claiming and time-bounded leases;
exactly-once *business effects* via unique idempotency keys and transactional execution.
No Redis, Celery, Kafka or Temporal.
"""
