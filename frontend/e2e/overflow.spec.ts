import { test, expect } from "@playwright/test";
import { BREAKPOINTS, isPhoneBreakpoint } from "./breakpoints";
import { PAGES } from "./pages";
import { login, setDesignSystem, navigateTo } from "./fixtures/auth";

const DESIGN_SYSTEMS = ["classic", "premium"] as const;

/** Data-driven: 9 breakpoints x ~19 pages x 2 design systems, generated from
 * one spec instead of ~340 hand-written files (see plan section 25/"Testing"
 * decision). Every case asserts the same three things the redesign spec
 * requires: no page-level horizontal overflow, no card clipped past the
 * viewport edge, and on phone widths the bottom nav doesn't cover content. */
for (const bp of BREAKPOINTS) {
  test.describe(`${bp.name} (${bp.width}x${bp.height})`, () => {
    test.use({ viewport: { width: bp.width, height: bp.height } });

    for (const designSystem of DESIGN_SYSTEMS) {
      test.describe(designSystem, () => {
        for (const item of PAGES) {
          test(`${item.label} has no overflow/clipping`, async ({ page }) => {
            await login(page);
            await setDesignSystem(page, designSystem);
            await page.goto("/");
            await expect(page.locator(".sidebar, .qp-sidebar, .mobile-bottom-nav, .qp-bottom-nav").first()).toBeVisible({
              timeout: 15_000,
            });

            if (item.key !== "dashboard") {
              await navigateTo(page, item.label, isPhoneBreakpoint(bp));
            }

            // Cards (Open Positions, Order Book, liquidation heatmap, etc.)
            // render a shorter loading/skeleton state before useAppData's
            // ~20-call initial fetch resolves, so their final height/
            // position isn't settled until then - waiting on networkidle
            // (no in-flight requests for 500ms) is a much more reliable
            // signal than a fixed timeout, since the page polls forever and
            // a fixed wait can race the data load under CI/test load.
            await page.waitForLoadState("networkidle", { timeout: 20_000 }).catch(() => {});
            await page.waitForTimeout(300);

            const hasHorizontalOverflow = await page.evaluate(
              () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
            );
            expect(hasHorizontalOverflow, "page should not scroll horizontally").toBe(false);

            const clippedCount = await page.locator(".card, .qp-card, .qp-chart-card, .qp-metric-card").evaluateAll((els) =>
              els.filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1).length
            );
            expect(clippedCount, "no card should extend past the viewport").toBe(0);

            if (isPhoneBreakpoint(bp)) {
              const nav = page.locator(".mobile-bottom-nav, .qp-bottom-nav").first();
              if (await nav.count()) {
                // Scroll to the very end of the page first - checking the
                // "last card" without scrolling just measures whatever
                // happens to be below the fold on a long page, which is
                // never meaningfully comparable to the fixed nav's position.
                await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
                await page.waitForTimeout(100);

                const navBox = await nav.boundingBox();
                const lastCard = page.locator(".card, .qp-card, .qp-chart-card").last();
                if (navBox && (await lastCard.count())) {
                  const cardBox = await lastCard.boundingBox();
                  if (cardBox) {
                    expect(cardBox.y + cardBox.height <= navBox.y + 1, "bottom nav should not cover the last card").toBe(true);
                  }
                }
              }
            }
          });
        }
      });
    }
  });
}
