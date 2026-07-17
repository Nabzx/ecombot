"""Authentication and role-based access control (S6).

JWT auth over the seeded synthetic users, an explicit permission model for the
support-agent and supervisor roles, and a non-human system-executor actor for the outbox
worker. The actor is always taken from the authenticated context, never from a
request-supplied field — a forged user id in a body is ignored.
"""
