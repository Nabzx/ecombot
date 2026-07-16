/** Shapes returned by the backend health endpoints. Kept in sync with the
 *  Pydantic schemas in `backend/app/schemas/health.py`. */

export type DependencyStatus = "ok" | "error";

export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
}

export interface ReadinessResponse {
  status: "ready" | "not_ready";
  service: string;
  version: string;
  checks: Record<string, DependencyStatus>;
}
