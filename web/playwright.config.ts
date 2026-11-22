// npm run test:e2e: the app in the system Chrome (headless) against the device simulator and fake devices (e2e/).
// Each file starts its own Vite server, browser and simulators; files run in parallel, tests in a file in order.
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: 'e2e',
  testMatch: '*.e2e.ts',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: 'list',
  outputDir: 'node_modules/.cache/playwright',
  workers: 3,
  forbidOnly: true,
});
