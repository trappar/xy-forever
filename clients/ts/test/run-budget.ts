// A Vitest reporter that bounds the whole run (Vitest has a per-test and per-hook time-out, but none for a run): past
// budgetMs it names the tests still running, fails the run and cancels it; if the cancel does not end it, it exits
// (this process's exit stops the simulators: test/simulators.ts). XYF_RUN_BUDGET_MS overrides the budget (a planted
// check: XYF_RUN_BUDGET_MS=1 fails the run at once and says so). Used by vitest.config.ts here and in web/.
import type { Reporter, TestCase, Vitest } from 'vitest/node';

export default class RunBudget implements Reporter {
  private readonly budgetMs: number;
  private readonly running = new Set<TestCase>();
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(budgetMs: number) {
    this.budgetMs = Number(process.env['XYF_RUN_BUDGET_MS'] ?? budgetMs);
  }

  onInit(vitest: Vitest): void {
    this.timer = setTimeout(() => {
      const names = [...this.running].map((t) => `${t.module.moduleId.split('/').slice(-2).join('/')} > ${t.fullName}`);
      console.error(
        `\nrun budget: the run passed ${this.budgetMs} ms; still running: ${names.join('; ') || 'no test (setup or teardown)'}\n`,
      );
      process.exitCode = 1;
      void vitest.cancelCurrentRun('test-failure');
      setTimeout(() => process.exit(1), 5_000).unref();
    }, this.budgetMs);
    this.timer.unref();
  }

  onTestCaseReady(test: TestCase): void {
    this.running.add(test);
  }

  onTestCaseResult(test: TestCase): void {
    this.running.delete(test);
  }

  onTestRunEnd(): void {
    clearTimeout(this.timer);
  }
}
