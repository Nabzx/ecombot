import { describe, expect, it } from "vitest";

import { buildUrl } from "@/lib/api";

describe("buildUrl", () => {
  it("joins base and path without duplicating slashes", () => {
    expect(buildUrl("http://localhost:8000", "/health")).toBe(
      "http://localhost:8000/health",
    );
  });

  it("tolerates a trailing slash on the base", () => {
    expect(buildUrl("http://localhost:8000/", "/health/ready")).toBe(
      "http://localhost:8000/health/ready",
    );
  });

  it("adds a leading slash to the path when missing", () => {
    expect(buildUrl("http://localhost:8000", "health")).toBe(
      "http://localhost:8000/health",
    );
  });
});
