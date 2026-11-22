// pnpm run test:e2e: the app in the system Chrome (headless) against the device simulator and fake devices (e2e/).
// One Vite dev server serves the whole run (webServer, on a free port chosen once and passed to the workers in the
// environment), so a cold dependency cache is optimized once. Files run in parallel, tests in a file in order (but
// install-sim.e2e.ts, parallel). Projects: app (Web Serial off) and install (Web Serial and WebUSB off: the pages use
// the fakes); bounds runs the planted hang (planted-hang.ts) in a nested run, the only place it runs (XYF_E2E_PLANTED).
// Budgets: each test, and the run as a whole (globalTimeout); a hung page fails its test by name within its bound.
import { createServer } from 'node:net';
import { defineConfig } from '@playwright/test';
import type { PlaywrightTestOptions, PlaywrightWorkerOptions } from '@playwright/test';

function freePort(): Promise<number> {
  return new Promise((done, fail) => {
    const probe = createServer();
    probe.once('error', fail);
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address() as { port: number };
      probe.close(() => done(port));
    });
  });
}

const planted = process.env['XYF_E2E_PLANTED'] === '1';
process.env['XYF_E2E_PORT'] ??= String(await freePort()); // the workers load this file again: same port
const url = (process.env['XYF_E2E_URL'] = `http://127.0.0.1:${process.env['XYF_E2E_PORT']}/`);
/** The system Chrome (no browser download), headless; features: the Blink features switched off. */
const chrome = (features: string): Partial<PlaywrightTestOptions & PlaywrightWorkerOptions> => ({
  channel: 'chrome',
  headless: true,
  launchOptions: { args: [`--disable-blink-features=${features}`] },
});

export default defineConfig({
  testDir: 'e2e',
  testMatch: '*.e2e.ts',
  timeout: 15_000,
  globalTimeout: planted ? 20_000 : 90_000,
  expect: { timeout: 5_000 },
  reporter: 'list',
  outputDir: 'node_modules/.cache/playwright',
  workers: 3,
  forbidOnly: true,
  ...(planted
    ? {}
    : {
        webServer: {
          command: `node node_modules/vite/bin/vite.js --port ${process.env['XYF_E2E_PORT']} --strictPort --logLevel error`,
          url,
          timeout: 30_000,
          reuseExistingServer: false,
        },
      }),
  projects: planted
    ? [{ name: 'planted', testMatch: 'planted-hang.ts', timeout: 2_000, use: chrome('Serial,WebUSB') }]
    : [
        { name: 'app', testMatch: 'app.e2e.ts', use: chrome('Serial') },
        { name: 'install', testMatch: ['install.e2e.ts', 'install-sim.e2e.ts'], use: chrome('Serial,WebUSB') },
        { name: 'bounds', testMatch: 'hang-bound.e2e.ts' },
      ],
});
