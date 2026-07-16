import { HealthPanel } from "@/components/HealthPanel";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-8 px-6 py-16">
      <header>
        <div className="mb-2 inline-flex items-center gap-2">
          <span className="rounded-md bg-slate-900 px-2 py-1 text-xs font-bold tracking-wide text-white">
            AgentOps
          </span>
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
            Foundation mode · S0
          </span>
        </div>
        <h1 className="text-2xl font-semibold text-slate-900">
          AI Customer Support Operations Platform
        </h1>
        <p className="mt-2 text-sm text-slate-600">
          This is the S0 foundation build. The dashboard, tickets, workflows,
          approvals and evaluations are not implemented yet — this page only
          confirms that the frontend, backend and database are wired together.
        </p>
      </header>

      <HealthPanel />

      <footer className="text-xs text-slate-400">
        Next up: S1 — Domain &amp; Synthetic Data.
      </footer>
    </main>
  );
}
