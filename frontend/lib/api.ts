/** Small typed API client for the AgentOps backend.
 *
 * Components call these helpers rather than using `fetch` directly, so request
 * construction, error handling and response typing live in one place.
 */

import type { HealthResponse, ReadinessResponse } from "@/types/health";
import { API_BASE_URL } from "@/lib/config";

/** Join a base URL and a path without duplicating slashes. */
export function buildUrl(base: string, path: string): string {
  const trimmedBase = base.replace(/\/+$/, "");
  const trimmedPath = path.startsWith("/") ? path : `/${path}`;
  return `${trimmedBase}${trimmedPath}`;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJson<T>(path: string, acceptStatuses: number[] = [200]): Promise<T> {
  let response: Response;
  try {
    response = await fetch(buildUrl(API_BASE_URL, path), {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(`Network error contacting the API at ${API_BASE_URL}`);
  }

  if (!acceptStatuses.includes(response.status)) {
    throw new ApiError(`Unexpected response ${response.status}`, response.status);
  }

  return (await response.json()) as T;
}

/** Combined backend health. */
export function getHealth(): Promise<HealthResponse> {
  return getJson<HealthResponse>("/health");
}

/** Backend readiness. A `not_ready` result is returned with HTTP 503, which is an
 *  expected outcome here (not an error) so the UI can render the degraded state. */
export function getReadiness(): Promise<ReadinessResponse> {
  return getJson<ReadinessResponse>("/health/ready", [200, 503]);
}
