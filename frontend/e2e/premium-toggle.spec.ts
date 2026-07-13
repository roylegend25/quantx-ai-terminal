import { test, expect } from "@playwright/test";
import { login, setDesignSystem } from "./fixtures/auth";

test.describe("design system toggle", () => {
  test("classic is the default with no stored preference", async ({ page }) => {
    await login(page);
    await page.goto("/");
    await expect(page.locator(".sidebar")).toBeVisible({ timeout: 15_000 });
    expect(await page.evaluate(() => document.documentElement.getAttribute("data-design"))).toBe("classic");
  });

  test("premium mode mounts the premium shell and persists across reload", async ({ page }) => {
    await login(page);
    await setDesignSystem(page, "premium");
    await page.goto("/");
    await expect(page.locator(".qp-sidebar")).toBeVisible({ timeout: 15_000 });
    expect(await page.evaluate(() => document.documentElement.getAttribute("data-design"))).toBe("premium");

    await page.reload();
    await expect(page.locator(".qp-sidebar")).toBeVisible({ timeout: 15_000 });
  });

  test("switching back to classic from the premium appearance menu restores the classic shell", async ({ page }) => {
    await login(page);
    await setDesignSystem(page, "premium");
    await page.goto("/");
    await expect(page.locator(".qp-sidebar")).toBeVisible({ timeout: 15_000 });

    await page.locator('.qp-icon-btn[title="Appearance"]').click();
    await page.locator(".design-toggle-opt").filter({ hasText: "QuantX Classic" }).click();

    await expect(page.locator(".sidebar")).toBeVisible({ timeout: 15_000 });
    expect(await page.evaluate(() => window.localStorage.getItem("quantx_design_system"))).toBe("classic");
  });
});
