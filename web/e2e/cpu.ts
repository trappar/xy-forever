// The e2e specs' test and expect: @playwright/test's, with each test's CPU time (the client's test/cpu.ts) in an
// annotation (cpu-ms) for e2e/budgets.ts: this worker's own, plus every process it started (Chrome and its
// renderers, the simulators, a nested run), from the test's first fixture to its last.
import { test as base } from '@playwright/test';
import { DrivenCpu, ownCpuMs } from 'xy-forever-client/test/cpu.ts';

export { expect } from '@playwright/test';

export const test = base.extend<{ cpu: void }>({
  cpu: [
    // eslint-disable-next-line no-empty-pattern -- Playwright reads a fixture's dependencies from this pattern
    async ({}, use, testInfo) => {
      const own = ownCpuMs();
      const driven = new DrivenCpu([process.pid], false);
      await driven.start();
      await use();
      const ms = ownCpuMs() - own + (await driven.stop());
      testInfo.annotations.push({ type: 'cpu-ms', description: String(Math.round(ms)) });
    },
    { auto: true },
  ],
});
