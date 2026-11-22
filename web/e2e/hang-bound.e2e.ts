// The bounds hold for a hung page: a nested run of the planted test (planted-hang.ts: a page stuck in a loop, under a
// 2 s test timeout) fails, names that test, and ends, its Chrome closed, well within its bound.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, test } from './cpu.ts';

const WEB = fileURLToPath(new URL('..', import.meta.url));
const BOUND_MS = 15_000; // the planted test's 2 s, the nested run's start and Chrome's

test('a hung page is killed and named within its bound', async () => {
  test.setTimeout(BOUND_MS + 5_000);
  // The nested run gets this environment without the runner's own variables (a worker's index, its port).
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !/^(TEST_|PW_|PLAYWRIGHT_)/.test(k)));
  const started = Date.now();
  const run = spawn(process.execPath, ['node_modules/@playwright/test/cli.js', 'test', '--reporter', 'line'], {
    cwd: WEB,
    env: { ...env, XYF_E2E_PLANTED: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  run.stdout.on('data', (d: Buffer) => (output += d.toString()));
  run.stderr.on('data', (d: Buffer) => (output += d.toString()));
  const status = await new Promise<number | null>((done) => run.once('exit', done));
  expect(Date.now() - started).toBeLessThan(BOUND_MS);
  expect(status, output).toBe(1);
  expect(output).toContain('planted: a page that never returns');
  expect(output).toContain('Test timeout of 2000ms exceeded');
});
