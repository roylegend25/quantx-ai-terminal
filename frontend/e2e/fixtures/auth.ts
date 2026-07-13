import { execSync } from "node:child_process";
import { expect, type Page } from "@playwright/test";
import { PRIMARY_MOBILE_LABELS } from "../pages";

let cachedToken: string | null = null;

/** Mints a real JWT directly inside the backend container instead of
 * driving the login form with credentials this test suite doesn't have -
 * see the "Mint an API token without the admin password" note in project
 * memory (quantx-deploy-topology). Requires the quantx-backend container to
 * be running locally; throws (failing the test with a clear message)
 * otherwise rather than silently testing an unauthenticated app. */
function mintToken(): string {
  if (cachedToken) return cachedToken;
  const out = execSync(
    `docker exec quantx-backend python -c "from app.core.security import create_access_token; print(create_access_token(subject='admin'))"`,
    { encoding: "utf-8" }
  ).trim();
  if (!out) throw new Error("Failed to mint an auth token from the quantx-backend container.");
  cachedToken = out;
  return out;
}

/** Injects the token into localStorage before the app's first script runs,
 * so useAuth() sees a valid session on the very first render and never
 * shows the login form. */
export async function login(page: Page): Promise<void> {
  const token = mintToken();
  await page.addInitScript((t) => {
    window.localStorage.setItem("quantx_token", t);
  }, token);
}

/** Switches the app's design system before navigation, via the same
 * localStorage key useDesignSystem() reads on mount. */
export async function setDesignSystem(page: Page, mode: "classic" | "premium"): Promise<void> {
  await page.addInitScript((m) => {
    window.localStorage.setItem("quantx_design_system", m);
  }, mode);
}

/** Drives the app's state-based (router-less) navigation by clicking the
 * matching nav control - the desktop sidebar item, or on phone widths the
 * primary bottom-nav item / the "More" sheet. Read-only: never clicks a
 * destructive action (close/stop/reset) - this suite only asserts layout.
 *
 * Waits for the clicked control to actually gain its "active" class before
 * returning: `networkidle` alone is not a reliable "navigation finished"
 * signal here, because a target page's data is frequently already warm
 * from the shared 10s poll, so no new request fires and `networkidle`
 * resolves near-instantly - sometimes before React has re-rendered past
 * the previous page. Asserting `active` ties directly to the same `active`
 * state variable that drives which page renders, so it can't race it. */
export async function navigateTo(page: Page, label: string, isMobile: boolean): Promise<void> {
  if (!isMobile) {
    const item = page.locator(".nav, .qp-nav-item").filter({ hasText: label }).first();
    await item.click();
    await expect(item).toHaveClass(/active/, { timeout: 10_000 });
    return;
  }

  const mobileLabel = PRIMARY_MOBILE_LABELS[label] ?? label;
  const primaryBtn = page.locator(".mobile-bottom-nav-item, .qp-bottom-nav-item").filter({ hasText: mobileLabel });
  if (await primaryBtn.count()) {
    await primaryBtn.first().click();
    await expect(primaryBtn.first()).toHaveClass(/active/, { timeout: 10_000 });
    return;
  }

  await page.locator(".mobile-bottom-nav-item, .qp-bottom-nav-item").filter({ hasText: "More" }).first().click();
  const moreItem = page.locator(".mobile-more-item, .qp-more-item").filter({ hasText: label }).first();
  await moreItem.click();
  // The sheet closes on navigate (see MobileBottomNav/PremiumBottomNav's
  // `useEffect(() => setMoreOpen(false), [active])`), so waiting for the
  // sheet item's own active class isn't reliable once it unmounts -
  // instead wait for the sheet to close, which only happens after `active`
  // state updates.
  await expect(page.locator(".notif-sheet, .qp-sheet")).toBeHidden({ timeout: 10_000 });
}
