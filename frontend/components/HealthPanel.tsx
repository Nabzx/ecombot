"use client";

import { useCallback, useEffect, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { getHealth, getReadiness } from "@/lib/api";
import { API_BASE_URL } from "@/lib/config";
import type { HealthResponse, ReadinessResponse } from "@/types/health";

type LoadState = "loading" | "loaded" | "error";

export function HealthPanel() {
  const [state, setState] = useState<LoadState>("loading");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState("loading");
    setError(null);
    try {
      const [healthResult, readinessResult] = await Promise.all([
        getHealth(),
        getReadiness(),
      ]);
      setHealth(healthResult);
      setReadiness(readinessResult);
      setState("loaded");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unknown error");
      setState("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const dbStatus = readiness?.checks.database ?? "error";

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-900">System status</h2>
        <button
          type="button"
          onClick={() => void load()}
          className="rounded-md border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50"
        >
          Refresh
        </button>
      </div>

      {state === "loading" && (
        <p className="text-sm text-slate-500">Checking backend…</p>
      )}

      {state === "error" && (
        <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">
          <p className="font-medium">Cannot reach the backend.</p>
          <p className="mt-1 text-red-600">{error}</p>
          <p className="mt-1 text-xs text-red-500">Target: {API_BASE_URL}</p>
        </div>
      )}

      {state === "loaded" && (
        <dl className="divide-y divide-slate-100">
          <Row label="Backend API">
            <StatusBadge
              tone={health?.status === "ok" ? "ok" : "error"}
              label={health?.status === "ok" ? "Healthy" : "Unavailable"}
            />
          </Row>
          <Row label="Database (PostgreSQL)">
            <StatusBadge
              tone={dbStatus === "ok" ? "ok" : "error"}
              label={dbStatus === "ok" ? "Ready" : "Not ready"}
            />
          </Row>
          <Row label="API version">
            <span className="text-sm text-slate-700">
              {health?.version ?? "—"}
            </span>
          </Row>
        </dl>
      )}
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between py-2.5">
      <dt className="text-sm text-slate-600">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
