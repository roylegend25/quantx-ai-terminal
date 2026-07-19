import { test, expect, devices } from "@playwright/test";
import { login, setDesignSystem } from "./fixtures/auth";

const cases = [
  { name: "MacBook", viewport: { width: 1280, height: 800 }, touch: false },
  { name: "tablet-touch", viewport: { width: 768, height: 1024 }, touch: true },
  { name: "mobile-touch", viewport: { width: 390, height: 844 }, touch: true },
] as const;

for (const item of cases) {
  test.describe(item.name, () => {
    test.use({
      viewport: item.viewport,
      hasTouch: item.touch,
      isMobile: item.touch,
      userAgent: item.touch ? devices["Pixel 7"].userAgent : devices["Desktop Chrome"].userAgent,
    });

    test("chart timeframe, resize and interactions work without browser errors", async ({ page }) => {
      const errors: string[] = [];
      const failedResponses: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("response", (response) => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`); });
      await page.route("**/api/exchange/binance/credential-status", (route) => route.fulfill({
        status: 200, contentType: "application/json", body: JSON.stringify({ configured: true, connection_valid: true,
          permissions_valid: true, environment: "real", account_mode: "PAPER", last_verified_at: null, validation_error: null }),
      }));
      await login(page);
      await setDesignSystem(page, "classic");
      await page.goto("/");
      const canvas = page.locator(".pcx-canvas-wrap canvas").first();
      await expect(canvas).toBeVisible({ timeout: 30_000 });
      const chart = page.locator(".pc-chart-wrap").first();
      const box = await chart.boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(item.viewport.width < 768 ? 360 : 470);

      const request = page.waitForRequest((r) => r.url().includes("/candles") && r.url().includes("interval=1w"));
      await page.locator(".tf-btn").filter({ hasText: "1W" }).click({ force: true });
      expect((await request).url()).toContain("interval=1w");
      await expect(page.locator(".tf-btn.active")).toHaveText("1W");

      const canvasBox = await canvas.boundingBox();
      if (!canvasBox) throw new Error("chart canvas has no box");
      if (item.touch) {
        const cdp = await page.context().newCDPSession(page);
        await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [
          { x: canvasBox.x + 80, y: canvasBox.y + 100, id: 1 }, { x: canvasBox.x + 180, y: canvasBox.y + 100, id: 2 },
        ] });
        await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [
          { x: canvasBox.x + 60, y: canvasBox.y + 100, id: 1 }, { x: canvasBox.x + 230, y: canvasBox.y + 100, id: 2 },
        ] });
        await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      } else {
        await canvas.hover({ position: { x: 200, y: 150 } });
        await page.mouse.wheel(0, -300);
        await page.mouse.move(canvasBox.x + 250, canvasBox.y + 160);
        await page.mouse.down();
        await page.mouse.move(canvasBox.x + 180, canvasBox.y + 160, { steps: 4 });
        await page.mouse.up();
      }
      await page.getByTitle(/Reset view/).click({ force: true });
      await expect(canvas).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
      expect(errors).toEqual([]);
      expect(failedResponses).toEqual([]);
    });
  });
}
