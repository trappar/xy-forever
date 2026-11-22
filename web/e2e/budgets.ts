// A Playwright reporter that holds the run to its budgets, as the Python suite's are (tests/conftest.py): each test's
// time, by project (tests), and the whole run's (runMs), about twice what a busy Mac measured. Going over fails the run
// at its end and names the test; XYF_BUDGETS=off only reports. Every run prints one line: the run and each project's
// slowest test against their budgets. The hang bounds (playwright.config.ts: timeout, globalTimeout) stay the outer
// limit. Used by playwright.config.ts.
import type { FullResult, Reporter, TestCase, TestResult } from '@playwright/test/reporter';

export interface Budgets {
  /** The whole run's budget, ms. */
  runMs: number;
  /** One test's budget, ms, by project name. */
  tests: Record<string, number>;
}

export default class E2eBudgets implements Reporter {
  private readonly budgets: Budgets;
  private readonly enforced = process.env['XYF_BUDGETS'] !== 'off';
  private readonly over: string[] = [];
  private readonly slowest = new Map<string, number>();

  constructor(budgets: Budgets) {
    this.budgets = budgets;
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    if (result.status === 'skipped') return;
    const project = test.parent.project()?.name ?? '';
    const limit = this.budgets.tests[project];
    this.slowest.set(project, Math.max(this.slowest.get(project) ?? 0, result.duration));
    if (limit !== undefined && result.duration > limit)
      this.over.push(
        `[${project}] ${test.location.file.split('/').pop()}:${test.location.line} ${test.title} took ` +
          `${result.duration} ms: over the ${limit} ms a test`,
      );
  }

  async onEnd(result: FullResult): Promise<{ status?: FullResult['status'] } | undefined> {
    const run = Math.round(result.duration);
    if (run > this.budgets.runMs) this.over.push(`the run took ${run} ms: over its ${this.budgets.runMs} ms`);
    const slowest = [...this.slowest].map(([p, ms]) => `${p} ${ms} of ${this.budgets.tests[p] ?? '-'} ms`);
    console.log(`\nbudgets: run ${run} of ${this.budgets.runMs} ms; slowest test: ${slowest.join(', ') || 'none ran'}`);
    for (const line of this.over) console.error(`${this.enforced ? 'BUDGET' : 'budget (not enforced)'}: ${line}`);
    return this.over.length && this.enforced && result.status === 'passed' ? { status: 'failed' } : undefined;
  }
}
