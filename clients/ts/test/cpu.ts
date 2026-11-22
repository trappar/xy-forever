// CPU time for the test budgets (test/run-budget.ts, web/e2e/budgets.ts), as the Python suite's are
// (tests/conftest.py): a busy machine stretches wall time, barely CPU time. A test's CPU time is its own process's
// (ownCpuMs) plus that of the processes it drives (DrivenCpu): the simulator a client spec talks to (its board and
// processors: test/cpu-setup.ts countSim) or everything a Playwright worker started (Chrome, simulators, a nested
// run: web/e2e/cpu.ts). Those are sampled every SAMPLE_MS (`ps` on macOS, /proc on Linux; elsewhere they count 0):
// a process that ends during a test (a power cut's processors, a closed page's renderer) loses at most its last
// SAMPLE_MS, and one that starts and ends between two samples is not counted.
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';

const SAMPLE_MS = 250;
const PROBE_MS = 15; // probeMs() on the reference machine (an Apple M5 Max), idle
const LINUX_TICK_MS = 10; // /proc/PID/stat counts CPU time in USER_HZ (100) ticks

/** This process's CPU ms so far (every thread). */
export function ownCpuMs(): number {
  const { user, system } = process.cpuUsage();
  return (user + system) / 1000;
}

/** ps's time ([[dd-]hh:]mm:ss.cc) in ms. */
function psMs(text: string): number {
  const [days, clock] = text.includes('-') ? text.split('-') : ['0', text];
  return (clock!.split(':').reduce((s, part) => s * 60 + Number(part), 0) + Number(days) * 86_400) * 1000;
}

/** Every process's [parent pid, CPU ms so far]; null where this platform has no cheap way. */
async function processes(): Promise<Map<number, [number, number]> | null> {
  const table = new Map<number, [number, number]>();
  if (process.platform === 'darwin') {
    let ps = 0;
    const out = await new Promise<string>((done, fail) => {
      ps = execFile('ps', ['-A', '-o', 'pid=,ppid=,time='], (error, stdout) =>
        error ? fail(error) : done(stdout),
      ).pid!;
    });
    for (const line of out.split('\n')) {
      const [pid, ppid, time] = line.trim().split(/\s+/);
      if (time && Number(pid) !== ps) table.set(Number(pid), [Number(ppid), psMs(time)]); // not this sample's own ps
    }
    return table;
  }
  if (process.platform !== 'linux') return null;
  const pids = (await readdir('/proc')).filter((name) => /^\d+$/.test(name));
  await Promise.all(
    pids.map(async (pid) => {
      const stat = await readFile(`/proc/${pid}/stat`, 'utf-8').catch(() => null); // gone meanwhile
      if (!stat) return;
      const f = stat.slice(stat.lastIndexOf(')') + 2).split(' '); // after "pid (comm) ": state ppid ... utime stime
      table.set(Number(pid), [Number(f[1]), (Number(f[11]) + Number(f[12])) * LINUX_TICK_MS]);
    }),
  );
  return table;
}

/** The CPU ms the process trees under roots (the roots too when withRoots) use from start() to stop(). */
export class DrivenCpu {
  private readonly base = new Map<number, number>();
  private readonly last = new Map<number, number>();
  private timer: ReturnType<typeof setInterval> | undefined;
  private sampling: Promise<void> | null = null;
  private readonly roots: readonly number[];
  private readonly withRoots: boolean;

  constructor(roots: readonly number[], withRoots: boolean) {
    this.roots = roots;
    this.withRoots = withRoots;
  }

  async start(): Promise<void> {
    await this.sample(this.base);
    this.timer = setInterval(() => {
      this.sampling ??= this.sample(this.last).finally(() => (this.sampling = null));
    }, SAMPLE_MS);
    this.timer.unref();
  }

  async stop(): Promise<number> {
    clearInterval(this.timer);
    await this.sampling;
    await this.sample(this.last);
    let ms = 0;
    for (const [pid, now] of this.last) ms += Math.max(0, now - (this.base.get(pid) ?? 0));
    return ms;
  }

  private async sample(into: Map<number, number>): Promise<void> {
    const table = await processes().catch(() => null);
    if (!table) return;
    const children = new Map<number, number[]>();
    for (const [pid, [ppid]] of table) children.set(ppid, [...(children.get(ppid) ?? []), pid]);
    const todo = this.withRoots ? this.roots.filter((pid) => table.has(pid)) : [...this.roots];
    for (let pid = todo.pop(); pid !== undefined; pid = todo.pop()) {
      if (this.withRoots || !this.roots.includes(pid)) into.set(pid, table.get(pid)![1]);
      todo.push(...(children.get(pid) ?? []));
    }
  }
}

/** Wall ms of a fixed workload (hashing), its fastest of three (the first pays for the JIT). */
function probeMs(): number {
  let best = Infinity;
  for (let k = 0; k < 3; k++) {
    const start = performance.now();
    let total = 0;
    for (let i = 0; i < 20_000; i++) {
      const d = createHash('sha256').update(`${i}:${total}`).digest();
      total = (total + d[0]! + d.length) & 0xffff;
    }
    best = Math.min(best, performance.now() - start);
  }
  return best;
}

/**
 * Call at the run's start; the function it returns, at its end, gives the budgets' scale: the probe's time against
 * PROBE_MS at the start or the end, the slower (slower hardware, or a machine so busy that this run's work lands on
 * slower cores), never below 1, as the Python suite's probe_s(). XYF_BUDGETS_SCALE=N fixes it.
 */
export function budgetScale(): () => number {
  const fixed = process.env['XYF_BUDGETS_SCALE'];
  if (fixed) return () => Number(fixed);
  const first = probeMs();
  return () => Math.max(1, Math.max(first, probeMs()) / PROBE_MS);
}
