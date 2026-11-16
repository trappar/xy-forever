// npm run test:e2e: the app in the system Chrome (headless) against the device simulator.
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: { include: ['e2e/**/*.e2e.ts'], environment: 'node', testTimeout: 90_000, hookTimeout: 60_000 },
});
