// A Playwright reporter that holds the run to its budgets, as the Python suite's are (tests/conftest.py): each test's
// time, by project (tests), and the whole run's (runMs), about twice an unloaded run's, scaled for slower hardware or a
// busy machine (the client's test/load-scale.ts). Going over fails the run at its end and names the test;
// XYF_BUDGETS=off only reports. Every run prints one line: the run and each project's
// slowest test against their budgets. The hang bounds (playwright.config.ts: timeout, globalTimeout) stay the outer
// limit. Used by playwright.config.ts.
import type { FullResult, Reporter, TestCase, TestResult } from '@playwright/test/reporter';
import { loadMeter } from 'xy-forever-client/test/load-scale.ts';

export interface Budgets {
  /** The whole run's budget, ms. */
  runMs: number;
  /** One test's budget, ms, by project name. */
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
    this.scale = loadMeter();
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    if (result.status === 'skipped') return;
    this.times.push([result.duration, test.parent.project()?.name ?? '', test]);
  }

  async onEnd(result: FullResult): Promise<{ status?: FullResult['status'] } | undefined> {
    const run = Math.round(result.duration);
    const scale = this.scale();
    const limit = (ms: number | undefined): number => Math.round((ms ?? Infinity) * scale);
    const over = this.times
      .filter(([ms, project]) => ms > limit(this.budgets.tests[project]))
      .map(
        ([ms, project, test]) =>
          `[${project}] ${test.location.file.split('/').pop()}:${test.location.line} ${test.title} took ${ms} ms: ` +
          `over the ${limit(this.budgets.tests[project])} ms a test`,
      );
    if (run > limit(this.budgets.runMs)) over.push(`the run took ${run} ms: over its ${limit(this.budgets.runMs)} ms`);
    const slowest = new Map<string, number>();
    for (const [ms, project] of this.times) slowest.set(project, Math.max(slowest.get(project) ?? 0, ms));
    const shown = [...slowest].map(([p, ms]) => `${p} ${ms} of ${limit(this.budgets.tests[p])} ms`);
    console.log(
      `\nbudgets (x${scale.toFixed(2)}): run ${run} of ${limit(this.budgets.runMs)} ms; slowest test: ${shown.join(', ') || 'none ran'}`,
    );
    for (const line of over) console.error(`${this.enforced ? 'BUDGET' : 'budget (not enforced)'}: ${line}`);
    return over.length && this.enforced && result.status === 'passed' ? { status: 'failed' } : undefined;
  }
}
