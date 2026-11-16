// Global setup of the browser suite (vitest.browser.config.ts), run by Node: three simulators and a
// relay to each, for the pages in Chrome.
//   xyf     sim XYF_TS_BROWSER_SIM_PORT + 0 (default 7710), relay XYF_TS_RELAY_PORT + 0 (7750)
//   preV1   sim XYF_TS_BROWSER_SIM_PORT + 2 (--pre-v1),     relay XYF_TS_RELAY_PORT + 1 (7751)
//   demo    sim XYF_TS_BROWSER_SIM_PORT + 4 (7714),         relay XYF_TS_RELAY_PORT + 4 (7754);
//           the demo page itself goes through the suite server's xyfRelay (?sim=7714)
// State under <private sessions>/ts-client-browser (XYF_TS_BROWSER_SIM_STATE), removed afterwards
// unless XYF_KEEP_SIM=1. Without the simulator the tests skip with the reason (test/browser.ts,
// the npm script, says so before Chrome starts).
import { rmSync } from 'node:fs';
import { join } from 'node:path';
import type { TestProject } from 'vitest/node';
import { startRelay } from '../relay.ts';
import type { Relay } from '../relay.ts';
import { findSimulator, startAll } from '../simulators.ts';

export interface WebSim {
  /** The relay's ws:// URL, and the simulator's endpoint port. */
  relay: string;
  port: number;
  /** The simulator's control port and its /yaffs2 folder on the Mac (for the browser commands). */
  control: number;
  root: string;
}
export interface WebSims { why: string | null; xyf: WebSim | null; preV1: WebSim | null; demo: WebSim | null }
declare module 'vitest' {
  export interface ProvidedContext { web: WebSims }
}

export default async function setup(project: TestProject): Promise<() => Promise<void>> {
  const found = findSimulator();
  const web: WebSims = { why: found.why, xyf: null, preV1: null, demo: null };
  const state = found.sessions ? process.env['XYF_TS_BROWSER_SIM_STATE'] ?? join(found.sessions, 'ts-client-browser') : null;
  const relays: Relay[] = [];
  let stopAll = async (): Promise<void> => undefined;
  if (!web.why && state) {
    const base = parseInt(process.env['XYF_TS_BROWSER_SIM_PORT'] ?? '7710', 10);
    const relayBase = parseInt(process.env['XYF_TS_RELAY_PORT'] ?? '7750', 10);
    try {
      const started = await startAll(state, base, [['xyf', 0, []], ['preV1', 2, ['--pre-v1']], ['demo', 4, []]]);
      stopAll = started.stopAll;
      for (const [name, offset] of [['xyf', 0], ['preV1', 1], ['demo', 4]] as const) {
        const sim = started.infos[name]!;
        const relay = await startRelay({ port: relayBase + offset, target: sim.port, control: sim.control });
        relays.push(relay);
        web[name] = { relay: relay.url, port: sim.port, control: sim.control, root: sim.root };
      }
    } catch (error) {
      web.why = (error as Error).message;
      web.xyf = web.preV1 = web.demo = null;
    }
  }
  if (web.why) console.warn(`\nbrowser tests skipped: ${web.why}\n`);
  project.provide('web', web);
  return async () => {
    await Promise.all(relays.map((r) => r.close()));
    await stopAll();
    if (state && process.env['XYF_KEEP_SIM'] !== '1') rmSync(state, { recursive: true, force: true });
  };
}
