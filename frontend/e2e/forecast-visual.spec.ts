import { test, expect } from "@playwright/test";
import { login, setDesignSystem } from "./fixtures/auth";

const viewports=[[390,844],[430,932],[768,1024],[834,1194],[1024,1366],[1440,900],[1920,1080]] as const;
for(const [width,height] of viewports){
  test(`informational forecast is visible at ${width}x${height}`,async({page},testInfo)=>{
    test.use;
    await login(page); await setDesignSystem(page,"premium"); await page.setViewportSize({width,height}); await page.goto("/");
    const tf=page.locator(".tf-btn").filter({hasText:"15m"}).first(); await tf.click();
    await expect(page.getByText("AI Forecast — Informational").first()).toBeVisible({timeout:30000});
    await expect(page.locator(".pcx-canvas-wrap canvas")).toBeVisible();
    const forecastPixels=await page.locator(".pcx-canvas-wrap canvas").evaluate((node:HTMLCanvasElement)=>{
      const context=node.getContext("2d"); if(!context)return 0;
      const pixels=context.getImageData(0,0,node.width,node.height).data; let count=0;
      for(let i=0;i<pixels.length;i+=4) if(pixels[i]<90&&pixels[i+1]>170&&pixels[i+2]>140&&pixels[i+3]>100) count++;
      return count;
    });
    expect(forecastPixels,"canvas should contain a visible cyan forecast path").toBeGreaterThan(5);
    if(width<768) await expect(page.getByRole("button",{name:"View Active Drive V2 decision details"})).toBeVisible();
    else await expect(page.locator(".pc-tablet-decision:visible, .pc-desktop-decision:visible").getByText("Active Drive V2")).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1)).toBe(true);
    await page.screenshot({path:testInfo.outputPath(`forecast-${width}x${height}.png`),fullPage:true});
  });
}
