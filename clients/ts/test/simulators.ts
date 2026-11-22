// Vitest global setup: starts the device simulators the spec files use and stops them after the
// run. Each is `python3 -m xy_forever.sim --port N --state DIR` (the repository's own simulator,
// built from target/), one per spec file so that files can run in parallel:
//   pre-v1       XYF_TS_SIM_PORT + 2   the pre-v1 hello (--pre-v1)
//   e2e          XYF_TS_SIM_PORT + 4   end-to-end and fault tests
//   ws           XYF_TS_SIM_PORT + 6   the WebSocket transport through the relay (websocket.spec.ts)
//   files        XYF_TS_SIM_PORT + 8   the file methods (files.spec.ts)
//   mods         XYF_TS_SIM_PORT + 20  mod packages (mods.spec.ts)
// Control ports are N + 1. XYF_TS_SIM_PORT: a random base by default (randomBase: parallel runs and
// checkouts do not collide); state under <private sessions>/ts-client-BASE (XYF_TS_SIM_STATE), removed
// afterwards unless XYF_KEEP_SIM=1. Without python3, the private stock
// images or clang++, the simulator tests are skipped with the reason; the codec tests still run.
// The browser suite (test/browser/setup.ts) uses the same functions with its own ports and folder.
import { spawn, spawnSync } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { mkdirSync, openSync, rmSync, writeSync } from 'node:fs';
import * as net from 'node:net';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { TestProject } from 'vitest/node';
import { control } from './relay.ts';

export { control };

export interface SimInfo {
  address: string;
  host: string;
  port: number;
  control: number;
  /** The simulator's /yaffs2 folder on the Mac. */
  root: string;
}
export interface Sims {
  why: string | null;
  preV1: SimInfo | null;
  e2e: SimInfo | null;
  ws: SimInfo | null;
  files: SimInfo | null;
  mods: SimInfo | null;
  fixtures: Record<string, string>;
}
declare module 'vitest' {
  export interface ProvidedContext {
    sims: Sims;
  }
}

export const REPO = fileURLToPath(new URL('../../..', import.meta.url));
const QUERY = `
import json
from xy_forever import paths, sim
build = paths.newest_verified_build()
print(json.dumps({
    'why': sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent'),
    'sessions': str(paths.SESSIONS),
    'stock_os_ldr': str(sim.STOCK_OS) if sim.STOCK_OS.is_file() else None,
    'xyf_build': str(build / 'candidate.ldr') if build else None}))
`;

function portBusy(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const sock = net.createConnection({ host: '127.0.0.1', port });
    sock.on('connect', () => {
      sock.destroy();
      resolve(true);
    });
    sock.on('error', () => resolve(false));
  });
}

async function start(name: string, port: number, state: string, extra: string[]): Promise<[SimInfo, ChildProcess]> {
  for (const p of [port, port + 1])
    if (await portBusy(p)) throw new Error(`port ${p} is in use (XYF_TS_SIM_PORT picks others)`);
  const dir = join(state, name);
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  const log = openSync(join(dir, 'sim.log'), 'a');
  // In this process's group (a group kill of the test run reaches it); the wrapper ends its own
  // xyf_sim group when it is terminated or this process is gone (xy_forever/procgroup.py, #133).
  // Device time moves only under the simulator's control, so a loaded host never runs out a device
  // time-out (sim/clock.cpp). Its output goes to sim.log through here: B's "USB on: listening" line
  // says it is up (no polling); then the control port's settle answers once the power-on's work is done and the
  // device's clock stands still, as a host that comes a moment later finds it.
  const child = spawn('python3', ['-m', 'xy_forever.sim', '--port', String(port), '--state', dir, ...extra], {
    cwd: REPO,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  live.add(child);
  child.once('exit', () => live.delete(child));
  endWithThisProcess();
  const info: SimInfo = {
    address: `127.0.0.1:${port}`,
    host: '127.0.0.1',
    port,
    control: port + 1,
    root: join(dir, 'yaffs2'),
  };
  return new Promise((resolve, reject) => {
    let seen = '',
      ready = false;
    const timer = setTimeout(() => fail(`did not listen within 60 s`), 60_000);
    const fail = (why: string): void => {
      clearTimeout(timer);
      stopChild(child);
      reject(new Error(`the ${name} simulator ${why}; see ${dir}/sim.log`));
    };
    const exited = (code: number | null): void => fail(`exited (${code})`);
    child.once('exit', exited);
    const output = (data: Buffer): void => {
      writeSync(log, data);
      if (ready) return;
      seen = (seen + data.toString()).slice(-4096);
      if (!/USB on: listening/.test(seen)) return;
      ready = true;
      control(info.control, 'settle', 60_000).then(
        () => {
          clearTimeout(timer);
          child.off('exit', exited);
          resolve([info, child]);
        },
        (error: Error) => fail(`did not settle (${error.message})`),
      );
    };
    child.stdout!.on('data', output);
    child.stderr!.on('data', output);
  });
}

function stopChild(child: ChildProcess): void {
  if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
}

// Every simulator started here and still running: stopped when this process exits or is
// terminated, whatever the tests did (a failure, a timeout, Ctrl-C).
const live = new Set<ChildProcess>();
let hooked = false;
function endWithThisProcess(): void {
  if (hooked) return;
  hooked = true;
  process.once('exit', () => {
    for (const child of live) stopChild(child);
  });
  for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP'] as const) {
    process.once(signal, () => {
      for (const child of live) stopChild(child);
      if (process.listenerCount(signal) === 0) process.kill(process.pid, signal); // its default: end
    });
  }
}

async function stop(info: SimInfo, child: ChildProcess): Promise<void> {
  await control(info.control, 'quit', 2000).catch(() => undefined);
  const exited = new Promise<boolean>((ok) => {
    if (child.exitCode !== null) ok(true);
    child.once('exit', () => ok(true));
    setTimeout(() => ok(false), 5000);
  });
  if (!(await exited)) stopChild(child);
}

const MOD_SET = `
import json, sys
from pathlib import Path
sys.path.insert(0, 'tests')
from xy_forever import modkit as M
from test_modpatch import finish, kit_for, profile_for, stock
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
(out / 'kit.json').write_text(json.dumps(kit_for(profile_for(stock()))))
print(M.split_kit(out / 'kit.json', out / 'packages', finish=finish, sdk_source='https://github.com/example/sdk@abc')['root'])
`;

/**
 * The synthetic mod package set of tests/test_modsend.py (modkit split of tests/test_modpatch.py's
 * kit) written under out (which must not hold one yet); returns its generation folder.
 */
export function makeModSet(out: string): string {
  const run = spawnSync('uv', ['run', '--quiet', 'python', '-c', MOD_SET, out], { cwd: REPO, encoding: 'utf-8' });
  if (run.status !== 0) throw new Error(`the synthetic mod package set: ${run.stderr.trim()}`);
  return run.stdout.trim();
}

const MOD_BASE = `
import sys
from pathlib import Path
sys.path.insert(0, 'tests')
from xy_forever import sim
import hashlib
from test_modpatch import CATALOG, OFFICIAL, stock
root, base = Path(sys.argv[1]), sys.argv[2]
entry = root / 'xy-forever' / 'firmware' / base
entry.mkdir(parents=True, exist_ok=True)
data = stock()
(entry / 'os.ldr').write_bytes(data)
(entry / 'info.txt').write_bytes(sim.info_text(data, 'Mod base', '1.2.1'))
# the official content as tests/test_sim_content_switch.py seeds it (each file's bytes are its path, the
# catalog CATALOG), and its list: a content switch accepts /yaffs2/content/ as the official set
body = {p: (CATALOG if p == 'op1_factory.json' else p.encode()) for p in OFFICIAL}
(entry / 'content.sha256').write_text(''.join('%s  %s\\n' % (hashlib.sha256(body[p]).hexdigest(), p) for p in sorted(body)))
for path, blob in body.items():
    (root / 'content' / path).parent.mkdir(parents=True, exist_ok=True)
    (root / 'content' / path).write_bytes(blob)
`;

/**
 * What mods.apply needs beside the synthetic package set, as tests/test_sim_mod_job.py seeds it: a
 * library entry base (the synthetic stock os.ldr, info.txt, content.sha256) and the synthetic stock
 * catalog in content/op1_factory.json with the other official content files (so a content switch accepts it),
 * written into the simulator's /yaffs2 folder root. The base is
 * not a known one: apply it with unverified.
 */
export function makeModBase(root: string, base: string): void {
  const run = spawnSync('uv', ['run', '--quiet', 'python', '-c', MOD_BASE, root, base], {
    cwd: REPO,
    encoding: 'utf-8',
  });
  if (run.status !== 0) throw new Error(`the synthetic mod base: ${run.stderr.trim()}`);
}

/** A random base port for simulators (base + N and N + 1, N < 100), below the system's ephemeral ports (49152 on): parallel
 * runs and other checkouts do not collide (start() refuses a port in use). */
export function randomBase(): number {
  return 20_000 + 100 * Math.floor(Math.random() * 290);
}

/**
 * cond()'s value once truthy, the simulator's clock moved on between tries (advance 1 ms, doubling to 1 s): no host
 * time waited (xy_forever/sim.py Simulator.until). An Error past limitMs of device time.
 */
export async function until<T>(
  controlPort: number,
  what: string,
  cond: () => T | Promise<T>,
  limitMs = 60_000,
): Promise<T> {
  let spent = 0,
    step = 1;
  for (;;) {
    const value = await cond();
    if (value) return value;
    if (spent >= limitMs)
      throw new Error(
        `${what} not reached within ${limitMs} ms of device time: ${JSON.stringify(await control(controlPort, 'status'))}`,
      );
    if ((await control(controlPort, `advance ${step}`, 60_000))['ok'] !== true)
      throw new Error(`advance ${step} refused`);
    spent += step;
    step = Math.min(step * 2, 1000);
  }
}

export interface Found {
  /** Why the simulator cannot run here; null when it can. */
  why: string | null;
  /** The private sessions folder (state folders go under it). */
  sessions: string | null;
  fixtures: Record<string, string>;
}

/** Whether python3 can build and run the simulator here, and where its files are. */
export function findSimulator(): Found {
  const query = spawnSync('python3', ['-c', QUERY], { cwd: REPO, encoding: 'utf-8' });
  if (query.error || query.status !== 0) {
    return {
      why: query.error
        ? 'python3 not found'
        : `python3 could not load xy_forever: ${query.stderr.trim().split('\n').pop()}`,
      sessions: null,
      fixtures: {},
    };
  }
  const found = JSON.parse(query.stdout) as {
    why: string | null;
    sessions: string;
    stock_os_ldr: string | null;
    xyf_build: string | null;
  };
  const fixtures: Record<string, string> = {};
  if (found.stock_os_ldr) fixtures['stock_os_ldr'] = found.stock_os_ldr;
  if (found.xyf_build) fixtures['xyf_build'] = found.xyf_build;
  return { why: found.why, sessions: found.sessions, fixtures };
}

/** Starts the simulators of plan (name, port offset from base, extra arguments) in order; all or none. */
export async function startAll(
  state: string,
  base: number,
  plan: readonly (readonly [string, number, readonly string[]])[],
): Promise<{ infos: Record<string, SimInfo>; stopAll(): Promise<void> }> {
  const running: [SimInfo, ChildProcess][] = [];
  const stopAll = (): Promise<void> =>
    Promise.all(running.map(([info, child]) => stop(info, child))).then(() => undefined);
  const infos: Record<string, SimInfo> = {};
  try {
    for (const [name, offset, extra] of plan) {
      const started = await start(name, base + offset, state, [...extra]);
      running.push(started);
      infos[name] = started[0];
    }
  } catch (error) {
    await stopAll();
    throw error;
  }
  return { infos, stopAll };
}

export default async function setup(project: TestProject): Promise<() => Promise<void>> {
  const found = findSimulator();
  const sims: Sims = {
    why: found.why,
    preV1: null,
    e2e: null,
    ws: null,
    files: null,
    mods: null,
    fixtures: found.fixtures,
  };
  const base = parseInt(process.env['XYF_TS_SIM_PORT'] ?? String(randomBase()), 10);
  const state = found.sessions ? (process.env['XYF_TS_SIM_STATE'] ?? join(found.sessions, `ts-client-${base}`)) : null;
  let stopAll = async (): Promise<void> => undefined;
  if (!sims.why && state) {
    try {
      const started = await startAll(state, base, [
        ['preV1', 2, ['--pre-v1']],
        ['e2e', 4, []],
        ['ws', 6, []],
        ['files', 8, []],
        ['mods', 20, []],
      ]);
      Object.assign(sims, started.infos);
      stopAll = started.stopAll;
    } catch (error) {
      sims.why = (error as Error).message;
    }
  }
  if (sims.why) console.warn(`\nsimulator tests skipped: ${sims.why}\n`);
  project.provide('sims', sims);
  return async () => {
    await stopAll();
    if (state && process.env['XYF_KEEP_SIM'] !== '1') rmSync(state, { recursive: true, force: true });
  };
}
