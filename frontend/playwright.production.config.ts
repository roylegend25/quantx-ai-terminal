import base from "./playwright.config";
import { defineConfig } from "@playwright/test";

export default defineConfig({
  ...base,
  use: {
    ...base.use,
    baseURL: "https://www.quantxterminal.com",
  },
  webServer: undefined,
});
