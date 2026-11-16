// The browser suite (npm run test:browser, through test/browser.ts): test/browser/*.spec.ts in the
// system Chrome (Playwright's channel 'chrome': no browser download), headless, in a throwaway
// profile. One file at a time: each simulator serves one client. xyfRelay: the demo page's relay.
import { playwright } from '@vitest/browser-playwright';
import { defineConfig } from 'vitest/config';
import { commands } from './test/browser/commands.ts';
import { xyfRelay } from './test/relay.ts';

export default defineConfig({
  server: { host: '127.0.0.1' },
  plugins: [xyfRelay()],
  test: {
    include: ['test/browser/**/*.spec.ts'],
    globalSetup: ['test/browser/setup.ts'],
    testTimeout: 60_000,
    hookTimeout: 60_000,
    fileParallelism: false,
    browser: {
      enabled: true,
      headless: true,
      provider: playwright({
        launchOptions: {
          channel: 'chrome',
          args: ['--disable-background-networking', '--disable-component-update', '--disable-domain-reliability',
            '--disable-sync', '--metrics-recording-only', '--no-default-browser-check', '--no-first-run', '--no-pings'],
        },
      }),
      instances: [{ browser: 'chromium' }],
      screenshotFailures: false,
      traceView: false,
      commands,
    },
  },
});
