// A Playwright reporter that holds the run to its budgets on CPU time, as the Python suite's are (tests/conftest.py):
// each test's, by project (tests), and the sum of the run's tests' (runMs), about twice an unloaded run's; a test's
// CPU time is its worker's and that of the processes the worker started (e2e/cpu.ts), and the budgets scale for
// slower hardware (the client's test/cpu.ts budgetScale). Going over fails the run at its end and names the test;
// XYF_BUDGETS=off only reports. Wall time is only reported. Every run prints one line: the run's CPU and each
// project's slowest test against their budgets. The hang bounds, on wall time (playwright.config.ts: timeout,
// globalTimeout), stay the outer limit. Used by playwright.config.ts.
import type { FullResult, Reporter, TestCase, TestResult } from '@playwright/test/reporter';
import { budgetScale } from 'xy-forever-client/test/cpu.ts';

export interface Budgets {
  /** The run's budget: its tests' CPU ms, summed. */
  runMs: number;
  /** One test's budget, CPU ms, by project name. */
  tests: Record<string, number>;
}

export default class E2eBudgets implements Reporter {
  private readonly budgets: Budgets;
  private readonly enforced = process.env['XYF_BUDGETS'] !== 'off';
  private readonly times: [number, string, TestCase][] = [];
  private scale: () => number = () => 1;

  constructor(budgets: Budgets) {
    this.budgets = budgets;
  }

  onBegin(): void {
    this.scale = budgetScale();
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    if (result.status === 'skipped') return;
    const cpu = result.annotations.find((a) => a.type === 'cpu-ms')?.description; // e2e/cpu.ts
    this.times.push([cpu ? Number(cpu) : result.duration, test.parent.project()?.name ?? '', test]);
  }

  async onEnd(result: FullResult): Promise<{ status?: FullResult['status'] } | undefined> {
    const run = this.times.reduce((sum, [ms]) => sum + ms, 0);
    const scale = this.scale();
    const limit = (ms: number | undefined): number => Math.round((ms ?? Infinity) * scale);
    const over = this.times
      .filter(([ms, project]) => ms > limit(this.budgets.tests[project]))
      .map(
        ([ms, project, test]) =>
          `[${project}] ${test.location.file.split('/').pop()}:${test.location.line} ${test.title} took ${ms} ms of CPU: ` +
          `over the ${limit(this.budgets.tests[project])} ms a test`,
      );
    if (run > limit(this.budgets.runMs))
      over.push(`the run's tests took ${run} ms of CPU: over its ${limit(this.budgets.runMs)} ms`);
    const slowest = new Map<string, number>();
    for (const [ms, project] of this.times) slowest.set(project, Math.max(slowest.get(project) ?? 0, ms));
    const shown = [...slowest].map(([p, ms]) => `${p} ${ms} of ${limit(this.budgets.tests[p])} ms`);
    console.log(
      `\nbudgets, CPU (x${scale.toFixed(2)}): run ${run} of ${limit(this.budgets.runMs)} ms; slowest test: ` +
        `${shown.join(', ') || 'none ran'}; wall (reported only): ${Math.round(result.duration)} ms`,
    );
    for (const line of over) console.error(`${this.enforced ? 'BUDGET' : 'budget (not enforced)'}: ${line}`);
    return over.length && this.enforced && result.status === 'passed' ? { status: 'failed' } : undefined;
  }
}
