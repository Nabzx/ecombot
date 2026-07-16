# AgentOps Frontend

Next.js (App Router) + TypeScript + Tailwind operator console for AgentOps.
**Stage S0 — Foundations**: a single status page that verifies the frontend can reach
the backend and that PostgreSQL is ready. The real dashboard arrives in later stages.

## Stack

- Next.js 15 (App Router), React 19, TypeScript (strict + `noUncheckedIndexedAccess`)
- Tailwind CSS 3
- ESLint (`next/core-web-vitals`, `next/typescript`), Vitest

## Layout

```
app/          App Router pages (layout, home) + global styles
components/    Presentational components (HealthPanel, StatusBadge)
lib/          Typed API client and runtime config
types/        Shared response types (mirrors backend schemas)
tests/        Vitest unit tests
```

## Local development (without Docker)

```bash
npm install
echo "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000" > .env.local
npm run dev            # http://localhost:3000
```

## Quality commands

```bash
npm run lint
npm run typecheck
npm run test
npm run build
```

## Configuration

| Variable                   | Purpose                          | Default                 |
| -------------------------- | -------------------------------- | ----------------------- |
| `NEXT_PUBLIC_API_BASE_URL` | Backend base URL used by browser | `http://localhost:8000` |
