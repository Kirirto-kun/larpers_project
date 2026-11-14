import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  use: { baseURL: "http://127.0.0.1:5173/", trace: "retain-on-failure" },
  webServer: [
    {
      command: "node dev/download_fixture.mjs",
      url: "http://127.0.0.1:18991/health",
      reuseExistingServer: !process.env.CI,
    },
    {
      command: "npm run dev",
      env: { CAMPUS_API_URL: "http://127.0.0.1:18991" },
      url: "http://127.0.0.1:5173/",
      reuseExistingServer: !process.env.CI,
    },
  ],
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
