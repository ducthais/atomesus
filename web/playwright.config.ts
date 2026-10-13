import { defineConfig } from '@playwright/test';

const appPort = 3100;
const backendPort = 4011;
const appUrl = `http://127.0.0.1:${appPort}`;
const backendUrl = `http://127.0.0.1:${backendPort}`;

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: 'list',
  outputDir: '.next/playwright-results',
  use: {
    baseURL: appUrl,
    channel: process.env.CI ? undefined : process.env.PLAYWRIGHT_CHANNEL || 'chrome',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: 'node tests/e2e/fixture-backend.mjs',
      url: `${backendUrl}/health`,
      env: { E2E_BACKEND_PORT: String(backendPort) },
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `npm run dev -- --port ${appPort}`,
      url: appUrl,
      env: { PIPELINE_BACKEND_URL: backendUrl, CATALOG_MOCK: '0' },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
