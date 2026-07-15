import {test,expect} from "@playwright/test";
import {login,setDesignSystem} from "./fixtures/auth";

for(const viewport of [{width:390,height:844},{width:834,height:1194},{width:1440,height:900}]){
 test(`live NO_TRADE dashboard ${viewport.width}x${viewport.height}`,async({page})=>{
  test.setTimeout(90_000);
  await page.setViewportSize(viewport);
  await login(page); await setDesignSystem(page,"classic"); await page.goto("/");
  await expect(page.getByLabel("Active Drive V2 live decision flow")).toBeVisible({timeout:20_000});
  await expect(page.getByText("Decision Flow",{exact:true})).toBeVisible();
  await expect(page.getByText("Decision requirements",{exact:true})).toBeVisible();
  await expect(page.locator(".market-connection")).toContainText(/LIVE|CACHED|RECONNECTING/,{timeout:15_000});
  await page.waitForTimeout(1500);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1)).toBe(true);
  await page.screenshot({path:`test-results/live-decision-${viewport.width}x${viewport.height}.png`,animations:"disabled"});
 });
}
