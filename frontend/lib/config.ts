/** Frontend runtime configuration.
 *
 * `NEXT_PUBLIC_API_BASE_URL` is inlined at build/dev time and points the browser at
 * the FastAPI backend. It defaults to the local backend for zero-config development.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
