function normalizeBaseUrl(value: string | undefined): string {
  const normalized = (value ?? "").trim().replace(/\/$/, "");

  // API methods already use /api/... paths, so the conventional /api base
  // means same-origin rather than /api/api. Absolute origins still work.
  return normalized === "/api" ? "" : normalized;
}

/** Production defaults to /api; local development uses Vite's same path. */
export const API_BASE_URL = normalizeBaseUrl(
  import.meta.env.VITE_API_BASE_URL || import.meta.env.VITE_API_URL,
);
