# Authentication and RBAC

Approvals are only meaningful if the system knows *who* decided. This layer supplies the
identity every approval decision is attributed to.

## Tokens

JWT (HS256, PyJWT) with a short-lived **access** token and a longer-lived **refresh**
token. Every token carries a `type` claim, and the type is checked on use — a refresh token
presented as an access token is rejected, not silently accepted.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/auth/login` | E-mail + password → token pair |
| `POST /api/auth/refresh` | Refresh token → new token pair |
| `GET /api/auth/me` | The caller's identity, role and permissions |

Passwords are verified with bcrypt. Seeded development accounts share the obviously-fake
password `agentops-dev` at a deliberately low cost factor; they exist only in the synthetic
development database.

The signing secret comes from `JWT_SECRET`. The default is an explicitly-labelled
development value and configuration validation **refuses to start in production** while it
is still in use.

## Roles and permissions

Two roles, each mapped to a fixed permission set
([enums.py](../backend/app/auth/enums.py)). Permissions are checked; roles are not
string-compared at call sites.

| Permission | Support agent | Supervisor |
| --- | --- | --- |
| `ticket_review` | ✅ | ✅ |
| `proposal_edit` | ✅ | ✅ |
| `approval_request_create` | ✅ | ✅ |
| `approval_queue_read` | ✅ | ✅ |
| `approval_decide` | ❌ | ✅ |
| `approval_high_value` | ❌ | ✅ |
| `action_status_read` | ✅ | ✅ |
| `outbox_inspect` | ❌ | ✅ |
| `manual_retry_request` | ❌ | ✅ |

A third identity, the **system executor**, is reserved for machine-initiated events. It has
no login and cannot decide anything; it appears in the audit trail as the actor for
system-generated events such as approval expiry.

## Using identity safely

Two rules the approval layer depends on:

1. **The actor always comes from the token**, never from a request body or query parameter.
   There is no code path that lets a caller name someone else as the decider.
2. **Role permission is necessary but not sufficient.** Holding `approval_decide` does not
   let a Supervisor approve a request they raised themselves; self-approval is refused by
   comparing the actor against `requester_user_id`. See
   [approval-system.md](approval-system.md).

Inactive users are rejected at the service boundary regardless of role or token validity.

## Inspecting users

```bash
make list-users
```

Prints every seeded account with its role and resolved permissions. It never prints
password hashes or plaintext passwords.
