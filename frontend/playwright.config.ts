import { defineConfig, devices } from "@playwright/test";

/** Real multi-viewport rendering, separate from vitest.config.ts's jsdom
 * unit tests (vitest.config.ts excludes e2e/** so the two never collide -
 * see that file). Points at the same Vite dev server developers use
 * locally, which now proxies /api and /ws to the backend container (see
 * vite.config.ts) so authenticated pages render real data. */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  // The Dashboard page alone fans out to ~20 API calls (useAppData); a
  // single local backend instance under concurrent test workers can be
  // slower than production. 2 local workers by default keeps this
  // reliable - raise workers in CI against a dedicated backend.
  workers: process.env.CI ? undefined : 2,
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"]],
  timeout: 45_000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run dev -- --port 5173 --strictPort",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
