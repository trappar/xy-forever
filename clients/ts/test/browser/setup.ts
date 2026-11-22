// Global setup of the browser suite (vitest.browser.config.ts), run by Node: two simulators and a
// relay to each, for the specs in Chrome.
//   xyf     sim XYF_TS_BROWSER_SIM_PORT + 0
//   preV1   sim XYF_TS_BROWSER_SIM_PORT + 2 (--pre-v1)
// The base is random by default (randomBase: parallel runs and checkouts do not collide); each relay takes a
// free port the system picks. State under <private sessions>/ts-client-browser-BASE (XYF_TS_BROWSER_SIM_STATE), removed afterwards
// unless XYF_KEEP_SIM=1. Without the simulator the tests skip with the reason (test/browser.ts,
// the npm script, says so before Chrome starts).
import { rmSync } from 'node:fs';
import { join } from 'node:path';
import type { TestProject } from 'vitest/node';
import { startRelay } from '../relay.ts';
import type { Relay } from '../relay.ts';
import { findSimulator, randomBase, startAll } from '../simulators.ts';

export interface WebSim {
  /** The relay's ws:// URL, and the simulator's endpoint port. */
  relay: string;
  port: number;
  /** The simulator's control port and its /yaffs2 folder on the Mac (for the browser commands). */
  control: number;
  root: string;
}
export interface WebSims { why: string | null; xyf: WebSim | null; preV1: WebSim | null }
declare module 'vitest' {
  export interface ProvidedContext { web: WebSims }
}

export default async function setup(project: TestProject): Promise<() => Promise<void>> {
  const found = findSimulator();
  const web: WebSims = { why: found.why, xyf: null, preV1: null };
  const base = parseInt(process.env['XYF_TS_BROWSER_SIM_PORT'] ?? String(randomBase()), 10);
  const state = found.sessions ? process.env['XYF_TS_BROWSER_SIM_STATE'] ?? join(found.sessions, `ts-client-browser-${base}`) : null;
  const relays: Relay[] = [];
  let stopAll = async (): Promise<void> => undefined;
  if (!web.why && state) {
    try {
      const started = await startAll(state, base, [['xyf', 0, []], ['preV1', 2, ['--pre-v1']]]);
      stopAll = started.stopAll;
      for (const name of ['xyf', 'preV1'] as const) {
        const sim = started.infos[name]!;
        const relay = await startRelay({ target: sim.port, control: sim.control });
        relays.push(relay);
        web[name] = { relay: relay.url, port: sim.port, control: sim.control, root: sim.root };
      }
    } catch (error) {
      web.why = (error as Error).message;
      web.xyf = web.preV1 = null;
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
