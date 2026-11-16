import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['test/**/*.spec.ts'],               // test/v1.test.ts and test/conformance.ts also run under plain Node
    exclude: ['test/browser/**', 'node_modules/**'],  // the browser suite: vitest.browser.config.ts
    globalSetup: ['test/simulators.ts'],
    testTimeout: 30_000,
    hookTimeout: 30_000,
  },
});
