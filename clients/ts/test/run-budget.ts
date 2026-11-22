// A Vitest reporter that holds the run to its budgets, as the Python suite's are (tests/conftest.py): each test's time
// (testMs) and the whole run's (runMs), about twice what a busy Mac measured. Going over fails the run at its end and
// names the test, or the run's slowest; XYF_BUDGETS=off only reports. Every run prints one line: the run and its
// slowest test against their budgets. Hang bounds stay the outer limit: Vitest's per-test and per-hook time-outs, and
// hangMs for the run (Vitest has none): past it, it names the tests still running, fails the run and cancels it; if
// the cancel does not end it, it exits (this process's exit stops the simulators: test/simulators.ts).
// XYF_RUN_BUDGET_MS overrides hangMs (a planted check: XYF_RUN_BUDGET_MS=1 fails the run at once and says so). Used by
// vitest.config.ts here and in web/.
import type { Reporter, TestCase, TestModule, Vitest } from 'vitest/node';

export interface Budgets {
  /** The whole run's budget, ms. */
  runMs: number;
  /** One test's budget, ms (its own time, not its hooks'). */
  testMs: number;
  /** The run's hang bound, ms: past it the run is cancelled. */
  hangMs: number;
}

const nameOf = (t: TestCase): string => `${t.module.moduleId.split('/').slice(-2).join('/')} > ${t.fullName}`;

export default class RunBudget implements Reporter {
  private readonly budgets: Budgets;
  private readonly enforced = process.env['XYF_BUDGETS'] !== 'off';
  private readonly running = new Set<TestCase>();
  private readonly over: string[] = [];
  private slowest: [number, TestCase | null] = [0, null];
  private start = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(budgets: Budgets) {
    this.budgets = { ...budgets, hangMs: Number(process.env['XYF_RUN_BUDGET_MS'] ?? budgets.hangMs) };
  }

  onInit(vitest: Vitest): void {
    this.start = performance.now();
    this.timer = setTimeout(() => {
      const names = [...this.running].map(nameOf);
      console.error(
        `\nrun bound: the run passed ${this.budgets.hangMs} ms; still running: ${names.join('; ') || 'no test (setup or teardown)'}\n`,
      );
      process.exitCode = 1;
      void vitest.cancelCurrentRun('test-failure');
      setTimeout(() => process.exit(1), 5_000).unref();
    }, this.budgets.hangMs);
    this.timer.unref();
  }

  onTestCaseReady(test: TestCase): void {
    this.running.add(test);
  }

  onTestCaseResult(test: TestCase): void {
    this.running.delete(test);
    const ms = test.diagnostic()?.duration ?? 0;
    if (ms > this.slowest[0]) this.slowest = [ms, test];
    if (ms > this.budgets.testMs)
      this.over.push(`${nameOf(test)} took ${Math.round(ms)} ms: over the ${this.budgets.testMs} ms a test`);
  }

  onTestRunEnd(modules: readonly TestModule[]): void {
    clearTimeout(this.timer);
    if (!modules.length) return; // nothing ran (vitest --changed with no change, a filter that matches nothing)
    const run = performance.now() - this.start;
    if (run > this.budgets.runMs)
      this.over.push(`the run took ${Math.round(run)} ms: over its ${this.budgets.runMs} ms`);
    const [ms, test] = this.slowest;
    console.log(
      `\nbudgets: run ${Math.round(run)} of ${this.budgets.runMs} ms; slowest test ${Math.round(ms)} of ${this.budgets.testMs} ms` +
        (test ? ` (${nameOf(test)})` : ''),
    );
    for (const line of this.over) console.error(`${this.enforced ? 'BUDGET' : 'budget (not enforced)'}: ${line}`);
    if (this.over.length && this.enforced) process.exitCode = 1;
  }
}
