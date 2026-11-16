// npm run test:browser: the browser suite (vitest.browser.config.ts) in the system Chrome. Skips with
// the reason, exit status 0, when the simulator cannot run here (python3, the private stock images,
// clang++) or the system Chrome cannot start; it never downloads a browser. Arguments go to Vitest.
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { findSimulator } from './simulators.ts';

function skip(why: string): never {
  console.log(`browser tests skipped: ${why}`);
  process.exit(0);
}

const found = findSimulator();
if (found.why) skip(found.why);
try {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  await browser.close();
} catch (error) {
  skip(`the system Chrome cannot start (${(error as Error).message.split('\n')[0]})`);
}
const vitest = fileURLToPath(new URL('../node_modules/vitest/vitest.mjs', import.meta.url));
const run = spawnSync(process.execPath, [vitest, 'run', '--config', 'vitest.browser.config.ts', ...process.argv.slice(2)],
  { stdio: 'inherit', cwd: fileURLToPath(new URL('..', import.meta.url)) });
process.exit(run.status ?? 1);
