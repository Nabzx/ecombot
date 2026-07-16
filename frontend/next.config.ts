import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit a self-contained server bundle for small, reproducible production images.
  output: "standalone",
  reactStrictMode: true,
  // Pin the file-tracing root to this app so an unrelated lockfile elsewhere on the
  // machine can't be mis-inferred as the workspace root.
  outputFileTracingRoot: process.cwd(),
};

export default nextConfig;
