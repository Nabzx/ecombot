"""Human approval layer (S6): approval requests, immutable decisions and expiry.

An approval never comes from a model. A Support Agent requests; only a Supervisor who is
not the requester may decide. Every consequential action requires a valid, unexpired
Supervisor approval bound to the exact action, order, amount and evidence snapshot.
"""
